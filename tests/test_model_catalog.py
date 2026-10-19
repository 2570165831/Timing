import importlib.util
import json
from pathlib import Path

import pytest

from timing import model_catalog
from timing.model_catalog import MODELS, inventory


def downloader():
    spec = importlib.util.spec_from_file_location("timing_model_installer", Path(__file__).parents[1] / "scripts/download_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_contains_only_the_five_required_groups():
    assert list(MODELS) == ["ctc", "tifa", "singing", "bs_lead", "separator"]
    assert [spec.installer for spec in MODELS.values()] == [
        "download_ctc.py", "download_tifa.py", "download_sofa.py",
        "download_bs_karaoke.py", "download_separator.py",
    ]
    for spec in MODELS.values():
        assert spec.label and spec.folder and spec.purpose


def test_optional_selection_and_experiments_are_gone():
    for name in ("PROFILES", "EXPERIMENTS", "select_models"):
        assert not hasattr(model_catalog, name)
    assert not {"asr", "aligner", "lead", "hubertfa", "anvuew-karaoke"} & set(MODELS)


def test_installer_installs_every_group_in_order(monkeypatch):
    module = downloader()
    calls = []
    monkeypatch.setattr(module, "initialize", lambda: None)
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    module.install()
    assert [Path(args[-1]).name for args, kw in calls] == [
        "download_ctc.py", "download_tifa.py", "download_sofa.py",
        "download_bs_karaoke.py", "download_separator.py",
    ]
    assert all(kw["check"] and args[0] == module.sys.executable for args, kw in calls)


def test_dry_run_list_and_inventory_never_install(tmp_path, monkeypatch, capsys):
    module = downloader()
    monkeypatch.setattr(module, "install", lambda: pytest.fail("unexpected installation"))
    monkeypatch.setattr(module, "MODELS_DIR", tmp_path)
    module.main(["--dry-run"])
    assert json.loads(capsys.readouterr().out)["models"] == ["ctc", "tifa", "singing", "bs_lead", "separator"]
    module.main(["--list"])
    listing = capsys.readouterr().out
    assert "separator: Kim MelBand vocals" in listing
    assert all(f"{name}:" in listing for name in MODELS)
    module.main(["--inventory"])
    rows = json.loads(capsys.readouterr().out)
    assert not any(row["present"] for row in rows)
    assert not list(tmp_path.iterdir())


def test_removed_profile_and_model_flags_are_rejected():
    module = downloader()
    for args in (["--profile", "lrc"], ["--models", "asr"], ["--profile", "full", "--dry-run"]):
        with pytest.raises(SystemExit):
            module.main(args)


def test_inventory_reports_only_the_five_required_folders_without_deleting(tmp_path):
    ctc = tmp_path / MODELS["ctc"].folder
    ctc.mkdir(parents=True)
    (ctc / "weights").write_bytes(b"123")
    legacy = tmp_path / "hubertfa"
    legacy.mkdir()
    (legacy / "weights").write_bytes(b"4567")
    rows = {row["id"]: row for row in inventory(tmp_path)}
    assert list(rows) == ["ctc", "tifa", "singing", "bs_lead", "separator"]
    assert rows["ctc"]["present"] and rows["ctc"]["bytes"] == 3
    assert not rows["tifa"]["present"] and rows["tifa"]["bytes"] == 0
    assert "hubertfa" not in rows
    assert (legacy / "weights").read_bytes() == b"4567"


def test_japanese_mms_frontend_works_without_sofa_assets(tmp_path, monkeypatch):
    from timing import singing
    from timing.ctc import text_units
    monkeypatch.setattr(singing, "SOFA_DIR", tmp_path / "no-sofa")
    singing.japanese_dictionary.cache_clear()
    units, labels = text_units("今日も hello 2人")
    assert "".join(unit.text for unit in units) == "今日も hello 2人"
    assert labels[0] == "kyoo"
    assert "futari" in "".join(labels)
    assert not list(tmp_path.iterdir())


def test_english_frontend_can_use_cmu_without_sofa(tmp_path, monkeypatch):
    from timing import singing
    from timing.schema import Unit
    monkeypatch.setattr(singing, "SOFA_DIR", tmp_path / "no-sofa")
    singing.dictionary.cache_clear()
    try:
        result = singing.english_parts(Unit("hello", "hello"), False)
        assert result[0][0] == "hello" and result[0][1]
    finally:
        singing.dictionary.cache_clear()


def test_tifa_readiness_rejects_missing_pronunciation_assets(tmp_path, monkeypatch):
    from timing import tifa
    monkeypatch.setattr(tifa, 'FOLDER', tmp_path)
    required = ('model.pt', 'config.yaml', 'vocabulary.json', 'manifest.json',
                'runtime/inference/api.py', 'deps/g2pflow/__init__.py',
                'dictionaries/japanese_dict_full.txt', 'dictionaries/ds_cmudict-07b.txt',
                'dictionaries/ds-zh-pinyin-lite.txt')
    for name in required:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture')
    assert tifa.ready()
    for name in required[-3:]:
        target = tmp_path / name
        target.unlink()
        assert not tifa.ready()
        target.write_text('fixture')
