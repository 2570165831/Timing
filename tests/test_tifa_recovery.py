import copy
import numpy as np
import pytest

from timing import tifa
from timing.pipeline import finish
from timing.schema import Line


def prediction(identifier, valid=True):
    return {"id": identifier, "texts": ["hi"], "selected": [{"phonemes": ["en/h", "en/ay"]}],
            "display_plans": [None], "owners": [1,1], "groups": [1,1], "phones": ["en/h", "en/ay"],
            "spans": [[.1,.2], [.2,.5 if valid else .2]], "agreement": .9, "skipped": [] if valid else [1]}


def inputs():
    lines = [Line("hi", start=0, end=1), Line("hi", start=1, end=2)]
    clips = [{"id": f"line_{i+1:03}", "start": i, "end": i+1, "target_start": 0, "target_end": 2} for i in range(2)]
    raw = {"results": [prediction(clips[0]["id"]), prediction(clips[1]["id"], False)]}
    return lines, clips, raw


@pytest.mark.parametrize("valid_retry", [True, False])
def test_recovery_preserves_successful_edges_and_text_and_marks_lrc_only_when_needed(tmp_path, monkeypatch, valid_retry):
    lines, clips, raw = inputs()
    saved = copy.deepcopy(raw)
    calls = []
    def worker(folder, retries, log, pronunciations=None):
        calls.extend(retries)
        return {"results": [prediction(c["id"], valid_retry) for c in retries]}
    monkeypatch.setattr(tifa, "run_worker", worker)
    checks = tifa.complete_results(np.ones(2000, dtype="float32"), 1000, lines, tmp_path, clips, raw, log=lambda _:None)
    assert raw == saved and len(calls) == 3 and all(c["line_index"] == 1 for c in calls)
    assert [(w.start,w.end) for w in lines[0].words] == [(.1,.5)]
    assert "".join(w.text for w in lines[1].words) == "hi"
    if valid_retry:
        assert checks[1]["mode"] == "retry"
        assert [(w.start,w.end) for w in lines[1].words] == [(.98,1.38)]
        assert "tifa_context_retry_check" in lines[1].flags
    else:
        assert checks[1]["mode"] == "lrc_review_placeholder"
        assert [(w.start,w.end) for w in lines[1].words] == [(1,2)]
        assert "tifa_native_alignment" not in lines[1].words[0].flags
    result = finish(lines,2,"en","test",tmp_path,{"source":"original"},lambda _:None)
    assert result["manual_timing_lines"] == ([] if valid_retry else [2])
    if not valid_retry:
        assert "第 2 行" in result["review_message"] and "手动补上逐字时间" in result["review_message"]
    assert (tmp_path/"lyrics.ttml").is_file()


def test_retry_worker_failure_still_exports_explicit_lrc_block(tmp_path,monkeypatch):
    lines, clips, raw = inputs()
    def worker(*args, **kwargs):
        raise RuntimeError("worker failed")
    monkeypatch.setattr(tifa,"run_worker",worker)
    checks=tifa.complete_results(np.ones(2000,dtype="float32"),1000,lines,tmp_path,clips,raw,log=lambda _:None)
    assert checks[1]["mode"] == "lrc_review_placeholder"
    assert [(w.start,w.end) for w in lines[0].words] == [(.1,.5)]


def test_rounded_invalid_display_boundary_is_not_accepted():
    row=prediction("test")
    row["spans"]=[[.10001,.10002],[.10002,.10003]]
    with pytest.raises(ValueError, match="字边界"):
        tifa.map_clip(Line("hi"),row,{"start":0,"end":1,"target_start":0,"target_end":2},True)


def test_reversed_context_lines_are_retried_on_their_own_audio(tmp_path,monkeypatch):
    lines,clips,raw=inputs()
    lines[0].end=2
    clips[0]["end"]=2
    clips[1]["start"]=0
    raw["results"][0]["spans"]=[[1.1,1.2],[1.2,1.5]]
    raw["results"][1]=prediction(clips[1]["id"])
    calls=[]
    def worker(folder,retries,log,pronunciations=None):
        calls.extend(retries)
        return {"results":[prediction(c["id"]) for c in retries]}
    monkeypatch.setattr(tifa,"run_worker",worker)
    checks=tifa.complete_results(np.ones(2000,dtype="float32"),1000,lines,tmp_path,clips,raw,log=lambda _:None)
    assert len(calls)==6 and all(c["mode"]=="retry" for c in checks)
    assert lines[0].start < lines[1].start
