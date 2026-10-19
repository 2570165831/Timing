"""SOFA-side Mandarin contract: per-character units and official opencpop phones.

All checks run on CPU with stub dictionaries. The installed Mandarin checkpoint
is only read (never executed) by the guarded asset check at the end.
"""
import json

import pytest

from timing.schema import Line, Unit
from timing import singing

ZH_TABLE = {
    "ni": ["n", "i"],
    "hao": ["h", "ao"],
    "shi": ["sh", "ir"],
    "jie": ["j", "ie"],
    "de": ["d", "e"],
    "xing": ["x", "ing"],
    "guang": ["g", "uang"],
    "chong": ["ch", "ong"],
    "qing": ["q", "ing"],
    "er": ["er"],
    "he": ["h", "e"],
}


@pytest.fixture
def zh_dictionary(monkeypatch):
    monkeypatch.setattr(singing, "mandarin_dictionary", lambda: dict(ZH_TABLE))


def test_pronunciation_overrides_adds_zh_and_keeps_ja_en(tmp_path):
    path = tmp_path / "pronunciations.json"
    path.write_text(json.dumps({"ja": {"今日": "キョー"}, "en": {"Word": ["z", "iy"]},
                                "zh": {"重庆": "chong2 qing4", "你好": ["ni3", "hao3"]}},
                               ensure_ascii=False), encoding="utf-8")
    table = singing.pronunciation_overrides(path)
    assert table["ja"] == {"今日": "キョー"}
    assert table["en"] == {"word": ["z", "iy"]}
    assert table["zh"] == {"重庆": "chong2 qing4", "你好": "ni3 hao3"}
    assert singing.pronunciation_overrides(tmp_path / "missing.json") == {"ja": {}, "en": {}, "zh": {}}


@pytest.mark.parametrize("payload", [{"zh": {"你好": ""}}, {"zh": {"你好": 5}},
                                     {"zh": {"你好": "hao!"}}, {"zh": []}])
def test_pronunciation_overrides_rejects_invalid_chinese_entries(tmp_path, payload):
    path = tmp_path / "pronunciations.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        singing.pronunciation_overrides(path)


def test_chinese_line_emits_per_character_units_with_opencpop_phones(zh_dictionary):
    units, _, sequence, mapping = singing.phonemize([Line("你好", language="zh")], False, True)
    assert "".join(unit.text for unit in units) == "你好"
    assert [unit.reading for unit in units] == ["ni", "hao"]
    assert all("chinese_character" in unit.flags for unit in units)
    assert sequence == [("sp", "SP"), ("zh", "n"), ("zh", "i"), ("sp", "SP"),
                        ("zh", "h"), ("zh", "ao"), ("sp", "SP")]
    assert mapping[1] == mapping[2] == 0 and mapping[4] == mapping[5] == 1
    assert all(language != "ja" for language, _ in sequence)


def test_chinese_punctuation_and_digits_preserved(zh_dictionary):
    units, _, sequence, _ = singing.phonemize([Line("你好，世界。", language="zh")], False, True)
    assert "".join(unit.text for unit in units) == "你好，世界。"
    assert units[-1].text.endswith("。")
    assert [phone for language, phone in sequence if language == "zh"] == ["n", "i", "h", "ao", "sh", "ir", "j", "ie"]
    digits, _, digit_sequence, _ = singing.phonemize([Line("2", language="zh")], False, True)
    assert [unit.reading for unit in digits] == ["er"]
    assert "number_reading_candidate" in digits[0].flags
    assert [phone for language, phone in digit_sequence if language == "zh"] == ["er"]


def test_zh_en_line_uses_english_parts_for_english_words(zh_dictionary, monkeypatch):
    monkeypatch.setattr(singing, "dictionary", lambda: {"world": ["w", "er", "l", "d"]})
    units, _, sequence, _ = singing.phonemize([Line("你好 world", language="zh-en")], False, True)
    assert "".join(unit.text for unit in units) == "你好 world"
    world = next(unit for unit in units if unit.spoken == "world")
    assert world.reading == ""
    assert [phone for language, phone in sequence if language == "en"] == ["w", "er", "l", "d"]
    assert [phone for language, phone in sequence if language == "zh"] == ["n", "i", "h", "ao"]


def test_chinese_line_never_uses_japanese_or_fugashi(monkeypatch, zh_dictionary):
    import timing.text as text_module

    def forbidden(*args, **kwargs):
        raise AssertionError("中文行不得调用日语分词")

    monkeypatch.setattr(singing, "tokenize", forbidden)
    monkeypatch.setattr(singing, "japanese_acoustic_parts", forbidden)
    monkeypatch.setattr(text_module, "tagger", forbidden)
    units, _, sequence, _ = singing.phonemize([Line("你好 world", language="zh")], False, True)
    assert [phone for language, phone in sequence if language == "zh"] == ["n", "i", "h", "ao"]


def test_han_without_explicit_chinese_language_keeps_the_japanese_route(monkeypatch):
    monkeypatch.setattr(singing, "tokenize", lambda text, fine=True: [Unit(text, text)])
    calls = []

    def fake_japanese(word, fine, custom=None):
        calls.append(word.spoken)
        return [(word.text, "ニイハオ", ["n", "i"], ["japanese_reading_check"])], "ニイハオ"

    monkeypatch.setattr(singing, "japanese_acoustic_parts", fake_japanese)
    units, _, sequence, _ = singing.phonemize([Line("你好")], False, True)
    assert calls == ["你好"]
    assert [phone for language, phone in sequence if language == "ja"] == ["n", "i"]
    assert all(language != "zh" for language, _ in sequence)


def test_prepare_lines_llm_avoids_fugashi_for_chinese(monkeypatch):
    import timing.text as text_module

    def forbidden(*args, **kwargs):
        raise AssertionError("中文行不得调用 fugashi 分词")

    monkeypatch.setattr(text_module, "tagger", forbidden)
    monkeypatch.setattr(singing, "current_method", lambda: "llm")
    captured = []
    monkeypatch.setattr(singing, "prepare_llm_many", lambda groups: captured.extend(groups))
    monkeypatch.setattr(singing, "dictionary", lambda: {"world": ["w", "er", "l", "d"],
                                                        "hello": ["h", "ah", "l", "ow"]})
    singing.prepare_lines_llm([Line("你好 world", language="zh"), Line("hello", language="en")])
    assert captured[0] == ([("world", ("w", "er", "l", "d"))], "你好 world")
    assert captured[1] == ([("hello", ("h", "ah", "l", "ow"))], "hello")


def test_chinese_phrase_override_applies_pinyin_per_character(zh_dictionary, monkeypatch):
    monkeypatch.setattr(singing, "pronunciation_overrides",
                        lambda *a, **k: {"ja": {}, "en": {}, "zh": {"重庆": "chong2 qing4"}})
    units, _, sequence, _ = singing.phonemize([Line("重庆", language="zh")], False, True)
    assert [unit.reading for unit in units] == ["chong", "qing"]
    assert [phone for language, phone in sequence if language == "zh"] == ["ch", "ong", "q", "ing"]
    assert all("custom_pronunciation" in unit.flags for unit in units)


def test_chinese_phrase_override_requires_one_syllable_per_character(zh_dictionary, monkeypatch):
    monkeypatch.setattr(singing, "pronunciation_overrides",
                        lambda *a, **k: {"ja": {}, "en": {}, "zh": {"重庆": "chong2"}})
    with pytest.raises(ValueError, match="拼音音节"):
        singing.phonemize([Line("重庆", language="zh")], False, True)


def test_single_character_override_wins_over_phrase(zh_dictionary, monkeypatch):
    monkeypatch.setattr(singing, "pronunciation_overrides",
                        lambda *a, **k: {"ja": {}, "en": {}, "zh": {"你好": "ni3 hao3", "好": "he4"}})
    units, _, sequence, _ = singing.phonemize([Line("你好", language="zh")], False, True)
    assert [unit.reading for unit in units] == ["ni", "he"]
    assert [phone for language, phone in sequence if language == "zh"] == ["n", "i", "h", "e"]


def test_yu_uses_sofa_mapping_not_the_tifa_y0_phone(monkeypatch):
    monkeypatch.setattr(singing, "mandarin_dictionary", lambda: {**ZH_TABLE, "yu": ["y", "v"]})
    monkeypatch.setattr(singing, "pronunciation_overrides",
                        lambda *a, **k: {"ja": {}, "en": {}, "zh": {"鱼": "yu2"}})
    units, _, sequence, _ = singing.phonemize([Line("鱼", language="zh")], False, True)
    phones = [phone for language, phone in sequence if language == "zh"]
    assert phones == ["y", "v"]
    assert "y0" not in phones


def test_missing_sofa_dictionary_entry_is_explicit(monkeypatch):
    monkeypatch.setattr(singing, "mandarin_dictionary", lambda: {"ni": ["n", "i"]})
    with pytest.raises(ValueError, match="SOFA 中文声学词典"):
        singing.phonemize([Line("你好", language="zh")], False, True)


def test_ready_requires_the_installed_chinese_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(singing, "SOFA_DIR", tmp_path)
    for name in ("runtime/modules/task/forced_alignment.py", "en/tgm_en_v100.ckpt", "en/dictionary.txt",
                 "ja/akm_ja_v001/model.ckpt", "ja/akm_ja_v001/dict.txt", "manifest.json"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture")
    assert not singing.ready()
    for name in (singing.SOFA_CHECKPOINTS["zh"], singing.SOFA_ZH_DICTIONARY):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture")
    assert singing.ready()
    assert singing.SOFA_CHECKPOINTS["zh"] == "zh/pretrained_mandarin_singing/v1.0.0_mandarin_singing.ckpt"


def test_installed_chinese_checkpoint_covers_the_sofa_dictionary():
    singing.mandarin_dictionary.cache_clear()
    checkpoint = singing.SOFA_DIR / singing.SOFA_CHECKPOINTS["zh"]
    table = singing.mandarin_dictionary()
    if not checkpoint.is_file() or not table:
        pytest.skip("SOFA 中文资源未安装")
    import torch

    state = torch.load(checkpoint, map_location="cpu", weights_only=False)
    tokens = set()
    for row in str(state["hyper_parameters"]["vocab_text"]).splitlines():
        name = row.split(":", 1)[0].strip()
        if name:
            tokens.add(name)
    emitted = {phone for phones in table.values() for phone in phones}
    assert emitted <= tokens, sorted(emitted - tokens)
