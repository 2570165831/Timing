import json

import numpy as np
import pytest


def _decode(path, target, rate=16000, **kwargs):
    return np.ones(int(3 * rate), dtype="float32"), rate


def test_lrc_windows_use_native_path_and_release_models(tmp_path, monkeypatch):
    from timing import pipeline, alignment
    from timing.schema import Unit
    calls, releases = [], []
    monkeypatch.setattr(pipeline, "decode", _decode)
    monkeypatch.setattr(alignment, "decode", _decode)
    class Singer:
        def __init__(self, log): pass
        def release(self): releases.append(True)
        def align(self, audio, lines, *args, sample_rate, native=False):
            calls.append((len(audio)/sample_rate, native))
            line = lines[0]
            line.words = [Unit(line.text, line.text, start=.1, end=.4)]
            line.start, line.end = .1, .4
            return {"frame_ms": 2.9, "method": "native_monolingual"}
    monkeypatch.setattr(alignment, "SingingAligner", Singer)
    pipeline.run(tmp_path/"a.wav", "[00:00.25]hello\n[00:01.50]world", tmp_path,
                 audio_mode="source", log=lambda _: None, aligner="sofa")
    assert calls == [(1.25, True), (1.5, True)]
    assert releases == [True]
    report = json.loads((tmp_path/"alignment.json").read_text(encoding="utf-8"))
    assert [line["words"][0]["start"] for line in report["lines"]] == [.35, 1.6]
    assert [(row["range_start"], row["range_end"]) for row in report["anchored_alignment"]] == [(.25, 1.5), (1.5, 3)]


def test_out_of_window_predictions_fail_without_export(tmp_path, monkeypatch):
    from timing import pipeline, alignment
    from timing.schema import Unit
    released = []
    monkeypatch.setattr(pipeline, "decode", lambda path, target, rate=16000, **kw: (np.ones(rate, dtype="float32"), rate))
    monkeypatch.setattr(alignment, "decode", lambda path, target, rate=16000, **kw: (np.ones(rate, dtype="float32"), rate))
    class Singer:
        def __init__(self, log): pass
        def release(self): released.append(True)
        def align(self, audio, lines, *args, **kwargs):
            lines[0].words = [Unit("hello", "hello", start=.1, end=2)]
            return {"frame_ms": 2.9}
    monkeypatch.setattr(alignment, "SingingAligner", Singer)
    with pytest.raises(ValueError, match="越出"):
        pipeline.run(tmp_path/"a.wav", "[00:00.25]hello", tmp_path, audio_mode="source", log=lambda _: None, aligner="sofa")
    assert released == [True] and not (tmp_path/"lyrics.ttml").exists()
