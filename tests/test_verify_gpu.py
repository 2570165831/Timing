"""Offline tests for the setup GPU probe. No CUDA device is required."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_gpu.py"


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location("_timing_verify_gpu", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


class FakeFinite:
    def __init__(self, value: bool) -> None:
        self.value = value

    def all(self):
        return self

    def item(self) -> bool:
        return self.value


class FakeTensor:
    def __init__(self, error: Exception | None = None) -> None:
        self.device = SimpleNamespace(type="cuda")
        self.error = error

    def __matmul__(self, other):
        if self.error is not None:
            raise self.error
        return self


class FakeCuda:
    def __init__(
        self,
        *,
        available: bool = True,
        device_count: int = 1,
        total_memory: int = 16 * 1024**3,
    ) -> None:
        self.available = available
        self.count = device_count
        self.total_memory = total_memory
        self.synchronize_calls = 0

    def is_available(self) -> bool:
        return self.available

    def device_count(self) -> int:
        return self.count

    def get_device_name(self, index: int) -> str:
        assert index == 0
        return "Mock CUDA GPU"

    def get_device_properties(self, index: int):
        assert index == 0
        return SimpleNamespace(
            total_memory=self.total_memory,
            major=12,
            minor=0,
        )

    def synchronize(self) -> None:
        self.synchronize_calls += 1

    def memory_allocated(self, index: int) -> int:
        assert index == 0
        return 4 * 1024**2

    def memory_reserved(self, index: int) -> int:
        assert index == 0
        return 8 * 1024**2


class FakeTorch:
    def __init__(
        self,
        *,
        available: bool = True,
        finite: bool = True,
        matmul_error: Exception | None = None,
    ) -> None:
        self.__version__ = "2.9.0"
        self.version = SimpleNamespace(cuda="12.9")
        self.float32 = "float32"
        self.cuda = FakeCuda(
            available=available,
            device_count=1 if available else 0,
        )
        self.finite = finite
        self.matmul_error = matmul_error
        self.randn_shape: tuple[int, ...] | None = None
        self.randn_kwargs: dict[str, object] = {}

    def randn(self, shape, **kwargs):
        self.randn_shape = shape
        self.randn_kwargs = kwargs
        return FakeTensor(self.matmul_error)

    def isfinite(self, tensor):
        return FakeFinite(self.finite)


def test_probe_success_and_output_file(verifier, monkeypatch, tmp_path, capsys):
    fake_torch = FakeTorch()
    monkeypatch.setattr(verifier, "_import_module", lambda name: fake_torch)
    report_path = tmp_path / "GPU验证.json"

    exit_code = verifier.main(["--output", str(report_path)])
    captured = capsys.readouterr()
    printed = json.loads(captured.out)
    written = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert captured.err == ""
    assert written == printed
    assert printed["ok"] is True
    assert printed["status"] == "ok"
    assert printed["gpu"] == "Mock CUDA GPU"
    assert printed["memory_gb"] == 16.0
    assert printed["allocation"] == "passed"
    assert printed["matmul"] == "passed"
    assert printed["finite"] is True
    assert printed["dtype"] == "float32"
    assert printed["memory_allocated_bytes"] == 4 * 1024**2
    assert printed["memory_reserved_bytes"] == 8 * 1024**2
    assert fake_torch.randn_shape == (128, 128)
    assert fake_torch.randn_kwargs == {"device": "cuda", "dtype": "float32"}
    assert fake_torch.cuda.synchronize_calls == 1


def test_missing_torch_has_chinese_error_and_nonzero_exit(verifier, monkeypatch, capsys):
    def missing_torch(name: str):
        assert name == "torch"
        raise ModuleNotFoundError("No module named 'torch'", name="torch")

    monkeypatch.setattr(verifier, "_import_module", missing_torch)

    assert verifier.main([]) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert report["ok"] is False
    assert report["error"]["code"] == "missing_torch"
    assert "缺少 PyTorch" in report["error"]["message"]
    assert "GPU 验证失败" in captured.err
    assert "Traceback" not in captured.err


def test_dll_load_error_has_chinese_error(verifier, monkeypatch):
    def failing_import(name: str):
        assert name == "torch"
        raise ImportError("DLL load failed while importing _C: WinError 126")

    monkeypatch.setattr(verifier, "_import_module", failing_import)
    report = verifier.probe()

    assert report["ok"] is False
    assert report["error"]["code"] == "import_dll"
    assert "动态链接库" in report["error"]["message"]
    assert "DLL load failed" in report["error"]["detail"]


def test_unavailable_cuda_has_chinese_error(verifier, monkeypatch):
    fake_torch = FakeTorch(available=False)
    monkeypatch.setattr(verifier, "_import_module", lambda name: fake_torch)

    report = verifier.probe()

    assert report["ok"] is False
    assert report["error"]["code"] == "no_cuda"
    assert "未检测到可用的 CUDA 设备" in report["error"]["message"]
    assert report["matmul"] == "not_run"
    assert report["device_count"] == 0


def test_nonfinite_matmul_is_a_kernel_failure(verifier, monkeypatch):
    fake_torch = FakeTorch(finite=False)
    monkeypatch.setattr(verifier, "_import_module", lambda name: fake_torch)

    report = verifier.probe()

    assert report["ok"] is False
    assert report["error"]["code"] == "kernel_failure"
    assert report["error"]["stage"] == "finite_check"
    assert "NaN 或 Inf" in report["error"]["message"]
    assert report["allocation"] == "passed"
    assert report["matmul"] == "failed"
    assert report["finite"] is False


def test_matmul_exception_is_a_kernel_failure(verifier, monkeypatch):
    fake_torch = FakeTorch(matmul_error=RuntimeError("CUDA error: invalid device function"))
    monkeypatch.setattr(verifier, "_import_module", lambda name: fake_torch)

    report = verifier.probe()

    assert report["ok"] is False
    assert report["error"]["code"] == "kernel_failure"
    assert report["error"]["stage"] == "allocation_or_matmul"
    assert "invalid device function" in report["error"]["detail"]
    assert report["allocation"] == "passed"
    assert report["matmul"] == "failed"
