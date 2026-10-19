"""One-time, checksum-verified separator download; not called by inference."""
import json
import os
import subprocess
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from download_support import atomic_write_json, error_tail, fetch, file_matches
from timing.config import MODELS, SEPARATOR_DIR, SEPARATOR_NAME, initialize

CHECKPOINT = "MelBandRoformer.ckpt"
CONFIG = "config_vocals_mel_band_roformer.yaml"
CHECKPOINT_SHA = "87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e"
CHECKPOINT_BYTES = 913106900
CHECKPOINT_URL = "https://huggingface.co/KimberleyJSN/melbandroformer/resolve/main/MelBandRoformer.ckpt"


def _manifest_ok():
    folder = SEPARATOR_DIR / SEPARATOR_NAME
    assets = SEPARATOR_DIR / "assets.json"
    if not assets.is_file():
        return False
    try:
        info = json.loads(assets.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (info.get("model") == SEPARATOR_NAME and
            info.get("sha256") == CHECKPOINT_SHA and
            file_matches(folder / CHECKPOINT, sha256=CHECKPOINT_SHA, size=CHECKPOINT_BYTES) and
            (folder / CONFIG).is_file())


def _package_ok():
    code = "from mel_band_roformer import MelBandRoformerSession, ensure_model_assets"
    env = os.environ.copy(); env.update(PYTHONUTF8="1", HF_HUB_OFFLINE="1")
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
    return result.returncode == 0, result.stderr[-800:]


def main():
    initialize()
    folder = SEPARATOR_DIR / SEPARATOR_NAME
    checkpoint = folder / CHECKPOINT
    if not _manifest_ok():
        print("下载 Kim MelBand 人声模型（约 913 MB）；服务器支持时可用 .part 续传。", flush=True)
        folder.mkdir(parents=True, exist_ok=True)
        fetch(CHECKPOINT_URL, checkpoint, expected_sha256=CHECKPOINT_SHA,
              expected_size=CHECKPOINT_BYTES, label="Kim MelBand 人声权重")
        from mel_band_roformer import ensure_model_assets
        checkpoint, config = ensure_model_assets(SEPARATOR_NAME, models_dir=SEPARATOR_DIR,
                                                 download_missing=False)
        if not file_matches(checkpoint, sha256=CHECKPOINT_SHA, size=CHECKPOINT_BYTES):
            raise RuntimeError("Kim MelBand 权重 SHA-256 校验失败。")
        info = {"model": SEPARATOR_NAME, "checkpoint": str(checkpoint.resolve()),
                "config": str(config.resolve()), "sha256": CHECKPOINT_SHA,
                "bytes": checkpoint.stat().st_size}
        atomic_write_json(SEPARATOR_DIR / "assets.json", info)
        manifest_path = MODELS / "manifest.json"
        manifest = {}
        if manifest_path.is_file():
            try:
                stored = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    manifest = stored
            except (OSError, ValueError):
                manifest = {}
        manifest["separator"] = {"model": SEPARATOR_NAME, "sha256": CHECKPOINT_SHA,
                                  "runtime_commit": "55e4217935da91aee0f2f7ac44cbad512a3485ac"}
        atomic_write_json(manifest_path, manifest)
    compatible, detail = _package_ok()
    if not compatible:
        raise RuntimeError("Kim MelBand 运行依赖导入失败：" + error_tail(detail))
    if not _manifest_ok():
        raise RuntimeError("Kim MelBand 文件完整性校验失败，请重新运行 scripts/download_separator.py。")
    print("Kim MelBand 人声模型与运行依赖校验通过。", flush=True)
    print(json.dumps(json.loads((SEPARATOR_DIR / "assets.json").read_text(encoding="utf-8")), indent=2),
          flush=True)


if __name__ == "__main__":
    main()
