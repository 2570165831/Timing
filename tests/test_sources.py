"""Audio route coverage for the uniform prepare_sources contract."""
import numpy as np
import pytest
import soundfile as sf

from timing import sources
from timing.audio import validate_stems


def _run_prepare(tmp_path, monkeypatch, *, mode, background=False, provided=False, kim=None, bs=None):
    monkeypatch.setattr(sources, "decode", lambda path, target, rate=16000, *, floating=False, **kw: (
        np.full(int(2 * rate), 0.5, dtype="float32"), rate))
    def kim_separate(source, output_dir, log=print):
        kim.append(str(source))
        stem = output_dir / "kim_vocals.wav"
        stem.write_bytes(b"kim")
        return stem, {"model": "kim-fixture"}
    def bs_separate(source, output_dir, log=print):
        bs.append(str(source))
        stem = output_dir / "bs_lead.wav"
        stem.write_bytes(b"bs")
        return stem, {"model": "bs-fixture"}
    monkeypatch.setattr(sources, "separate_vocals", kim_separate)
    monkeypatch.setattr(sources, "separate_bs_lead", bs_separate)
    return sources.prepare_sources(
        tmp_path / "song.wav", np.zeros(32000, dtype="float32"), 16000, tmp_path,
        vocals_path=tmp_path / "supplied.wav" if provided else None,
        audio_mode=mode, has_background=background, log=lambda _: None)


def test_lead_default_uses_bs_on_the_selected_track_and_skips_kim_without_background(tmp_path, monkeypatch):
    kim, bs = [], []
    tracks = _run_prepare(tmp_path, monkeypatch, mode="lead", kim=kim, bs=bs)
    assert bs == [str(tmp_path / "song.wav")] and kim == []
    assert tracks.source == "separated_vocals"
    assert tracks.main_path == tmp_path / "bs_lead.wav"
    assert tracks.background_path == tmp_path / "song.wav"
    assert tracks.lead_separation == {"model": "bs-fixture"} and tracks.separation is None


def test_lead_with_background_sends_kim_the_original_and_bs_keeps_it_too(tmp_path, monkeypatch):
    kim, bs = [], []
    tracks = _run_prepare(tmp_path, monkeypatch, mode="lead", background=True, kim=kim, bs=bs)
    assert kim == [str(tmp_path / "song.wav")]
    assert bs == [str(tmp_path / "song.wav")]
    assert tracks.main_path == tmp_path / "bs_lead.wav"
    assert tracks.background_path == tmp_path / "kim_vocals.wav"
    assert tracks.separation == {"model": "kim-fixture"}


def test_supplied_vocals_are_used_unchanged_and_bs_reads_the_supplied_stem(tmp_path, monkeypatch):
    kim, bs = [], []
    tracks = _run_prepare(tmp_path, monkeypatch, mode="lead", background=True, provided=True, kim=kim, bs=bs)
    assert kim == []
    assert bs == [str(tmp_path / "supplied.wav")]
    assert tracks.background_path == tmp_path / "supplied.wav"


def test_vocals_and_source_modes_pick_the_expected_tracks(tmp_path, monkeypatch):
    kim, bs = [], []
    vocals = _run_prepare(tmp_path, monkeypatch, mode="vocals", kim=kim, bs=bs)
    assert kim == [str(tmp_path / "song.wav")] and bs == []
    assert vocals.source == "separated_vocals" and vocals.main_path == tmp_path / "kim_vocals.wav"
    kim.clear(); bs.clear()
    direct = _run_prepare(tmp_path, monkeypatch, mode="source", kim=kim, bs=bs)
    assert kim == [] and bs == []
    assert direct.source == "original" and direct.main_path == tmp_path / "song.wav"


def test_validate_stems_preserves_timeline_and_reconstruction(tmp_path):
    mix = np.random.default_rng(7).normal(0, .1, (44100, 2)).astype("float32")
    paths = [tmp_path / name for name in ("mix.wav", "lead.wav", "residual.wav")]
    for path, signal in zip(paths, (mix, mix*.8, mix-mix*.8)):
        sf.write(path, signal, 44100, subtype="FLOAT")
    info = validate_stems(*paths)
    assert info["frames"] == 44100 and info["timeline_preserved"]
    assert info["reconstruction_max_error"] < 1e-6
    sf.write(paths[1], mix[1:], 44100, subtype="FLOAT")
    with pytest.raises(RuntimeError, match="时间轴"):
        validate_stems(*paths)


def test_missing_bs_model_fails_offline_without_download(tmp_path, monkeypatch):
    import socket
    from timing import bs_lead
    monkeypatch.setattr(bs_lead, "ready", lambda: False)
    def no_network(*args, **kwargs):
        raise AssertionError("inference must stay offline")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    with pytest.raises(RuntimeError, match="模型"):
        bs_lead.separate(tmp_path / "missing.wav", tmp_path)
