import socket

import numpy as np
import pytest

from timing.audio import match_timeline
from timing.separation import separate_vocals


def test_only_endpoint_rounding_adjustment_preserves_origin():
    source = np.array([1., 2., 3., 4.], dtype="float32")
    assert np.array_equal(match_timeline(source, 5), [1, 2, 3, 4, 0])
    assert np.array_equal(match_timeline(source, 3), [1, 2, 3])
    with pytest.raises(ValueError, match="长度不一致"):
        match_timeline(source, 100)


def test_missing_separator_fails_offline_before_using_audio(tmp_path, monkeypatch):
    # A missing checkpoint must never initiate a download during a user job.
    from timing import separation
    monkeypatch.setattr(separation, "separator_ready", lambda: False)
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    def no_network(*args, **kwargs):
        raise AssertionError("inference attempted network access")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    with pytest.raises(RuntimeError, match="尚未下载完整"):
        separate_vocals(tmp_path / "nonexistent.wav", tmp_path)
