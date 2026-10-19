import sys
from pathlib import Path

import pytest

from timing import tifa_worker

MODELS = Path(__file__).resolve().parent.parent / "models" / "tifa"


@pytest.fixture(scope="module")
def g2pflow():
    if not (MODELS / "deps/g2pflow").is_dir() or not (MODELS / "dictionaries/japanese_dict_full.txt").is_file():
        pytest.skip("TIFA 依赖或词典未下载")
    sys.path.insert(0, str(MODELS / "deps"))
    import g2pflow
    return g2pflow


def test_silent_mark_reattaches_to_previous_word():
    texts = ["箱", "と", "箱", "ア", "ア", "を", "な", "ら", "べ", "る"]
    source = "箱と箱アーアーをならべる"
    merged, changed = tifa_worker.restore_silent_marks(source, texts)
    assert merged == ["箱", "と", "箱", "アー", "アー", "を", "な", "ら", "べ", "る"]
    assert changed == {3, 4}
    assert "".join(merged) == source


def test_trailing_silent_mark_attaches_to_last_word():
    merged, changed = tifa_worker.restore_silent_marks("今日ナー", ["今日", "ナ"])
    assert merged == ["今日", "ナー"]
    assert changed == {1}


def test_exact_labels_are_returned_unchanged():
    texts = ["ナー", "ナー"]
    merged, changed = tifa_worker.restore_silent_marks("ナーナー", texts)
    assert merged == texts
    assert changed == set()


def test_wrong_character_is_not_silently_merged():
    merged, changed = tifa_worker.restore_silent_marks("ナーネー", ["ナ", "ナ"])
    assert merged == ["ナ", "ナ"]
    assert changed == set()


def test_label_with_punctuation_is_left_to_validation():
    merged, changed = tifa_worker.restore_silent_marks("ナーナー", ["ナー!", "ナー"])
    assert merged == ["ナー!", "ナー"]
    assert changed == set()


def test_transcripts_reads_text_beside_each_clip(tmp_path):
    audio = tmp_path / "line_001.wav"
    audio.write_bytes(b"")
    source = "箱と箱アーアーをならべる"
    audio.with_suffix(".txt").write_text(source, encoding="utf-8")
    assert tifa_worker.transcripts([{"id": "line_001", "audio": str(audio)}]) == {"line_001": source}


def test_shared_mark_plan_carries_review_flag(g2pflow):
    choice = {"phonemes": ["ja/n", "ja/a"], "scripts": ["na"]}
    plan = tifa_worker.display_plans(["ナー"], [choice], MODELS, None, {0})
    assert plan[0][-1]["text"] == "ナー"
    assert "prolonged_mark_shared" in plan[0][-1]["flags"]
    plain = tifa_worker.display_plans(["ナー"], [choice], MODELS)
    assert "prolonged_mark_shared" not in plain[0][-1]["flags"]


def test_map_result_accepts_merged_label():
    from timing.schema import Line
    from timing.tifa import map_result
    row = {"texts": ["ナー"], "selected": [{"phonemes": ["ja/n", "ja/a"]}],
           "display_plans": [[{"text": "ナー", "group_count": 1, "flags": ["prolonged_mark_shared"]}]],
           "owners": [1, 1], "groups": [1, 1], "phones": ["ja/n", "ja/a"],
           "spans": [[.1, .2], [.2, .5]], "skipped": []}
    line = Line("ナー")
    map_result(line, row, 0)
    assert [unit.text for unit in line.words] == ["ナー"]
    assert "prolonged_mark_shared" in line.words[0].flags
