"""Pinned SOFA singing weights/runtime, explicit setup downloads only."""
import json
import os
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from download_support import atomic_write_json, error_tail, fetch, file_matches, sha256_file
from timing.config import MODELS

COMMIT = "584d6b9a57927843f85decebf4cbf03b9598125f"
ASSETS = [
    ("en/tgm_en_v100.ckpt", "https://github.com/spicytigermeat/SOFA-Models/releases/download/v1.0.0_en/tgm_en_v100.ckpt", "6aba5f0ba3461155bf0c20190df85fad580de42409784737573309f6962449ff"),
    ("en/dictionary.txt", "https://raw.githubusercontent.com/spicytigermeat/SOFA-Models/main/tgm_sofa_dict.txt", "1b2a52ac559169196cb7cdfd3edcfe3947d37ec6a28a6ec281abac838331439c"),
    ("ja/akm_ja_v001.zip", "https://github.com/ariikamusic/SOFA_Models/releases/download/akm_ja_v001/akm_ja_v001.zip", "c1d7ad16b73e6f21b0df93819e65b0cacdcb3d349990f89f51d77738b3c6ef8f"),
    ("zh/pretrained_mandarin_singing.zip", "https://github.com/qiuqiao/SOFA/releases/download/v1.0.1/pretrained_mandarin_singing.zip", "63a8c6191fe6bdf29ac40af2955ef96816b8391596d94a4340e0f58dcf47f26e"),
    ("zh/opencpop-extension.txt", f"https://raw.githubusercontent.com/qiuqiao/SOFA/{COMMIT}/dictionary/opencpop-extension.txt", "e912ee4ec1d87fcdfd8b82c0a8f3999f0db7fd731bec08435256b621ebebb5a1"),
    ("runtime.zip", f"https://codeload.github.com/qiuqiao/SOFA/zip/{COMMIT}", None),
]


def update_manifest(path, entries):
    """Merge verified assets into the existing manifest without dropping older entries."""
    manifest = {}
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                manifest = stored
        except (OSError, ValueError):
            manifest = {}
    assets = {row["name"]: row for row in manifest.get("assets", [])
              if isinstance(row, dict) and isinstance(row.get("name"), str)}
    for row in entries:
        assets[row["name"]] = row
    manifest["runtime_commit"] = COMMIT
    manifest["assets"] = [assets[name] for name in sorted(assets)]
    manifest.pop("files", None)
    atomic_write_json(path, manifest)


def _zip_valid(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("压缩包内部校验失败。")


def _write_member(base, parts, data):
    target = base.joinpath(*parts)
    if not target.resolve().is_relative_to(base.resolve()):
        raise RuntimeError("模型压缩包包含不安全路径。")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    temporary.write_bytes(data)
    temporary.replace(target)
    return target


def _extract_ja(base):
    with zipfile.ZipFile(base / "ja/akm_ja_v001.zip") as archive:
        for item in archive.infolist():
            if not item.is_dir():
                _write_member(base / "ja", Path(item.filename).parts, archive.read(item))


def _extract_zh(base):
    with zipfile.ZipFile(base / "zh/pretrained_mandarin_singing.zip") as archive:
        for item in archive.infolist():
            if not item.is_dir():
                _write_member(base / "zh", Path(item.filename).parts, archive.read(item))


def _extract_runtime(base):
    with zipfile.ZipFile(base / "runtime.zip") as archive:
        for item in archive.infolist():
            parts = Path(item.filename).parts[1:]
            if not parts or item.is_dir():
                continue
            if not ((parts[0] == "modules" and parts[-1].endswith(".py")) or
                    (parts[0] == "LICENSE" and len(parts) == 2)):
                continue
            _write_member(base / "runtime", parts, archive.read(item))


def _manifest_ok(base):
    path = base / "manifest.json"
    if not path.is_file():
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if manifest.get("runtime_commit") != COMMIT:
        return False
    rows = {row.get("name"): row for row in manifest.get("assets", [])
            if isinstance(row, dict) and row.get("name")}
    for name, url, expected in ASSETS:
        row = rows.get(name)
        if row is None or row.get("url") != url:
            return False
        if expected and row.get("sha256") != expected:
            return False
        stored = row.get("sha256")
        if not isinstance(stored, str) or len(stored) != 64:
            return False
        if not file_matches(base / name, sha256=stored):
            return False
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return False
    for relative, asset in files.items():
        if not isinstance(asset, dict) or not isinstance(asset.get("sha256"), str):
            return False
        if not file_matches(base / relative, sha256=asset.get("sha256"), size=asset.get("bytes")):
            return False
    return all((base / name).is_file() for name in (
        "runtime/modules/task/forced_alignment.py", "en/tgm_en_v100.ckpt",
        "en/dictionary.txt", "ja/akm_ja_v001/model.ckpt", "ja/akm_ja_v001/dict.txt",
        "zh/pretrained_mandarin_singing/v1.0.0_mandarin_singing.ckpt",
        "zh/opencpop-extension.txt"))


def _package_ok(base):
    code = ("import sys; from pathlib import Path; "
            "sys.path.insert(0,str(Path(sys.argv[1])/'runtime')); "
            "import modules.task.forced_alignment")
    env = os.environ.copy(); env.update(PYTHONUTF8="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(base)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=120, env=env)
    return result.returncode == 0, result.stderr[-800:]


def main():
    base = MODELS / "sofa"
    if _manifest_ok(base):
        print("SOFA 本地文件校验通过，继续检查运行依赖。", flush=True)
    else:
        base.mkdir(parents=True, exist_ok=True)

        def fetch_asset(item):
            name, url, expected = item
            fetch(url, base / name, expected_sha256=expected, label=f"SOFA / {name}",
                  validate=_zip_valid if name.endswith(".zip") else None)
            digest = sha256_file(base / name)
            print(f"SOFA / {name}：校验通过。", flush=True)
            return {"name": name, "url": url, "sha256": digest}

        with ThreadPoolExecutor(max_workers=3) as pool:
            manifest_assets = list(pool.map(fetch_asset, ASSETS))
        _extract_ja(base)
        _extract_zh(base)
        _extract_runtime(base)
        files = {}
        archive_names = {name for name, _, _ in ASSETS}
        for path in base.rglob("*"):
            if not path.is_file() or path.name.endswith(".part") or path.name.endswith(".partial"):
                continue
            relative = str(path.relative_to(base)).replace("\\", "/")
            if relative in archive_names or relative == "manifest.json":
                continue
            files[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        update_manifest(base / "manifest.json", manifest_assets)
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        manifest["files"] = files
        atomic_write_json(base / "manifest.json", manifest)

    compatible, detail = _package_ok(base)
    if not compatible:
        raise RuntimeError("SOFA 运行库依赖导入失败：" + error_tail(detail))
    if not _manifest_ok(base):
        raise RuntimeError("SOFA 文件完整性校验失败，请重新运行 scripts/download_sofa.py。")
    print("SOFA 英语、日语、普通话歌声模型与运行依赖已就绪。", flush=True)


if __name__ == "__main__":
    main()
