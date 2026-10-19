import json
import sys
from pathlib import Path

import pytest

from timing import singing
from timing import tifa_pronunciation as tp

MODELS = Path(__file__).resolve().parent.parent / "models" / "tifa"


@pytest.fixture(scope="module")
def g2pflow():
    if not (MODELS / "deps/g2pflow").is_dir() or not (MODELS / "dictionaries/japanese_dict_full.txt").is_file():
        pytest.skip("TIFA 依赖或词典未下载")
    sys.path.insert(0, str(MODELS / "deps"))
    import g2pflow
    return g2pflow


def test_override_loader_validates_japanese_kana_and_english_phones(tmp_path):
    path = tmp_path / "pronunciations.json"
    path.write_text(json.dumps({"ja": {"今日": "キョー"}, "en": {"Word": ["z", "iy"]}}, ensure_ascii=False), encoding="utf-8")
    assert singing.pronunciation_overrides(path) == {"ja": {"今日": "キョー"}, "en": {"word": ["z", "iy"]}, "zh": {}}
    path.write_text(json.dumps({"ja": {"今日": "kyou"}, "en": {}}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="假名"):
        singing.pronunciation_overrides(path)
    path.write_text(json.dumps({"ja": {}, "en": {"word": ["z", 3]}}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="音素数组"):
        singing.pronunciation_overrides(path)
    assert singing.pronunciation_overrides(tmp_path / "missing.json") == {"ja": {}, "en": {}, "zh": {}}


def test_prepare_configs_writes_only_active_languages(tmp_path):
    overrides = {"ja": {"今日": "キョー"}, "en": {"hello": ["hh", "ah"]}}
    japanese = tp.prepare_configs(overrides, ["ja"], tmp_path, MODELS)
    assert [entry["id"] for entry in japanese] == [tp.CONVERTER_ID]
    assert japanese[0]["language"] == "ja"
    assert json.loads(Path(japanese[0]["kwargs"]["overrides_path"]).read_text(encoding="utf-8")) == {"ja": {"今日": "キョー"}}
    assert not (tmp_path / "english_overrides.txt").exists()

    folder = tmp_path / "second"
    folder.mkdir()
    english = tp.prepare_configs(overrides, ["en"], folder, MODELS)
    assert english == [{"id": "dictionary", "language": "en", "kwargs": {"dict_path": str(folder / "english_overrides.txt")}}]
    assert (folder / "english_overrides.txt").read_text(encoding="utf-8") == "hello\thh ah\n"

    assert tp.prepare_configs({"ja": {}, "en": {}}, ["ja", "en"], tmp_path, MODELS) == []


def test_prepare_configs_rejects_english_phone_with_whitespace(tmp_path):
    with pytest.raises(ValueError, match="空白"):
        tp.prepare_configs({"ja": {}, "en": {"word": ["z z"]}}, ["en"], tmp_path, MODELS)


def test_worker_request_records_pronunciations_path(tmp_path, monkeypatch):
    from timing import tifa

    class Proc:
        stdout = []

        def __init__(self, *args, **kwargs):
            pass

        def wait(self):
            return 1

    monkeypatch.setattr(tifa.subprocess, "Popen", Proc)
    folder = tmp_path / "tifa"
    folder.mkdir()
    with pytest.raises(RuntimeError):
        tifa.run_worker(folder, [], lambda _: None, pronunciations=tmp_path / "pron.json")
    payload = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    assert payload == {"clips": [], "pronunciations": str(tmp_path / "pron.json")}

    second = tmp_path / "second"
    second.mkdir()
    with pytest.raises(RuntimeError):
        tifa.run_worker(second, [], lambda _: None)
    assert "pronunciations" not in json.loads((second / "request.json").read_text(encoding="utf-8"))


def test_japanese_override_converter_forces_reading(g2pflow, tmp_path):
    tp.ensure_registered()
    from g2pflow import get_converter
    table = tmp_path / "japanese_overrides.json"
    table.write_text(json.dumps({"ja": {"今日": "キョー"}}, ensure_ascii=False), encoding="utf-8")
    converter = get_converter(tp.CONVERTER_ID)(
        overrides_path=str(table), dict_path=str(MODELS / "dictionaries/japanese_dict_full.txt"))
    word = converter.convert("今日")[0]
    assert word.text == "今日"
    path = word.readings[0].paths[0]
    assert [group.script for group in path] == ["kyo"]
    assert [phone for group in path for phone in group.phonemes] == ["ky", "o"]
    assert converter.find("あの今日も") == (2, 4)
    assert converter.find("あの昨日も") is None


def test_display_plan_uses_override_reading(g2pflow):
    from timing import tifa_worker
    choice = {"phonemes": ["ja/n", "ja/u", "ja/m", "ja/u"], "scripts": ["nu", "mu"]}
    plan = tifa_worker.display_plans(["飲む"], [choice], MODELS, {"飲む": "ヌム"})
    assert plan == [[{"text": "飲", "group_count": 1, "flags": []},
                     {"text": "む", "group_count": 1, "flags": []}]]
    fallback = tifa_worker.display_plans(["飲む"], [choice], MODELS, None)
    assert fallback == [[{"text": "飲む", "group_count": 2, "flags": ["reading_mapping_indivisible"]}]]


def test_map_result_marks_custom_pronunciation():
    from timing.schema import Line
    from timing.tifa import map_result
    row = {"texts": ["hi"], "selected": [{"phonemes": ["en/h", "en/ay"], "custom_pronunciation": True}],
           "display_plans": [None], "owners": [1, 1], "groups": [1, 1], "phones": ["en/h", "en/ay"],
           "spans": [[.1, .2], [.2, .5]], "skipped": []}
    line = Line("hi")
    map_result(line, row, 0)
    assert any("custom_pronunciation" in unit.flags for unit in line.words)
