"""Verify the local PyTorch CUDA runtime without model or network access."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Any, Sequence


MATRIX_SIZE = 128
_DLL_MARKERS = (
    "dll",
    "winerror",
    "error loading",
    "cannot load",
    "could not be found",
    "找不到指定的模块",
    "无法加载",
)


class ProbeFailure(Exception):
    """An expected, user-actionable probe failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.detail = detail


def _exception_detail(exc: BaseException) -> str:
    detail = " ".join(f"{type(exc).__name__}: {exc}".split())
    return detail[:1000]


def _looks_like_dll_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    return any(marker in text for marker in _DLL_MARKERS)


def _import_module(name: str) -> Any:
    return importlib.import_module(name)


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return None


def _base_report() -> dict[str, Any]:
    return {
        "ok": False,
        "status": "error",
        "gpu": None,
        "gpu_index": 0,
        "device_count": 0,
        "torch": None,
        "cuda": None,
        "torch_cuda": None,
        "transformers": _installed_version("transformers"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "memory_gb": None,
        "memory_bytes": None,
        "memory_allocated_bytes": None,
        "memory_reserved_bytes": None,
        "capability": None,
        "allocation": "not_run",
        "matmul": "not_run",
        "dtype": "float32",
        "shape": [MATRIX_SIZE, MATRIX_SIZE],
        "finite": None,
    }


def _load_torch() -> Any:
    try:
        return _import_module("torch")
    except ModuleNotFoundError as exc:
        message = str(exc)
        if getattr(exc, "name", None) == "torch" or "No module named 'torch'" in message:
            raise ProbeFailure(
                "missing_torch",
                "缺少 PyTorch：当前 Python 环境未安装 torch。请先在项目目录执行 uv sync。",
            ) from None
        raise ProbeFailure(
            "torch_import",
            "PyTorch 导入失败：当前环境缺少运行依赖。请重新执行 uv sync。",
            detail=_exception_detail(exc),
        ) from None
    except Exception as exc:
        if isinstance(exc, OSError) or _looks_like_dll_error(exc):
            raise ProbeFailure(
                "import_dll",
                "PyTorch 动态链接库（DLL）加载失败。请重新执行 uv sync；"
                "本脚本不会安装或修改显卡驱动。",
                detail=_exception_detail(exc),
            ) from None
        raise ProbeFailure(
            "torch_import",
            "PyTorch 导入失败。请检查当前 Python 环境并重新执行 uv sync。",
            detail=_exception_detail(exc),
        ) from None


def _finish_failure(report: dict[str, Any], failure: ProbeFailure) -> dict[str, Any]:
    report["ok"] = False
    report["status"] = "error"
    report["error"] = {"code": failure.code, "message": failure.message}
    if failure.stage:
        report["error"]["stage"] = failure.stage
    if failure.detail:
        report["error"]["detail"] = failure.detail
    return report


def _fail(
    report: dict[str, Any],
    code: str,
    message: str,
    *,
    stage: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return _finish_failure(
        report,
        ProbeFailure(code, message, stage=stage, detail=detail),
    )


def _safe_int(call: Any) -> int | None:
    try:
        return int(call())
    except Exception:
        return None


def probe() -> dict[str, Any]:
    """Return a JSON-serializable CUDA runtime report without raising on setup errors."""

    report = _base_report()
    try:
        torch = _load_torch()
    except ProbeFailure as failure:
        return _finish_failure(report, failure)
    except Exception as exc:
        return _fail(
            report,
            "torch_import",
            "PyTorch 初始化失败。请重新执行 uv sync。",
            detail=_exception_detail(exc),
        )

    report["torch"] = getattr(torch, "__version__", None)
    cuda_version = getattr(getattr(torch, "version", None), "cuda", None)
    report["cuda"] = cuda_version
    report["torch_cuda"] = cuda_version

    try:
        cuda = torch.cuda
        available = bool(cuda.is_available())
        device_count = int(cuda.device_count())
        report["device_count"] = device_count
    except Exception as exc:
        return _fail(
            report,
            "no_cuda",
            "无法检查 CUDA 可用性。请确认 nvidia-smi 正常，并安装与当前环境匹配的 "
            "CUDA 版 PyTorch；本脚本不会安装驱动。",
            stage="cuda_check",
            detail=_exception_detail(exc),
        )

    if not available or device_count == 0:
        return _fail(
            report,
            "no_cuda",
            "未检测到可用的 CUDA 设备。请确认 nvidia-smi 正常，并安装与当前环境匹配的 "
            "CUDA 版 PyTorch；本脚本不会安装驱动。",
            stage="cuda_check",
        )

    try:
        gpu_name = str(cuda.get_device_name(0))
        properties = cuda.get_device_properties(0)
    except Exception as exc:
        return _fail(
            report,
            "cuda_device_query",
            "无法读取 CUDA 设备信息。请确认显卡驱动状态正常，并重新执行 uv sync。",
            stage="device_query",
            detail=_exception_detail(exc),
        )

    report["gpu"] = gpu_name
    total_memory = getattr(properties, "total_memory", None)
    if total_memory is not None:
        try:
            total_memory = int(total_memory)
            report["memory_bytes"] = total_memory
            report["memory_gb"] = round(total_memory / 1024**3, 2)
        except (TypeError, ValueError):
            pass

    major = getattr(properties, "major", None)
    minor = getattr(properties, "minor", None)
    if major is not None and minor is not None:
        try:
            report["capability"] = [int(major), int(minor)]
        except (TypeError, ValueError):
            pass

    try:
        tensor = torch.randn(
            (MATRIX_SIZE, MATRIX_SIZE),
            device="cuda",
            dtype=torch.float32,
        )
        report["allocation"] = "passed"
        result = tensor @ tensor
        cuda.synchronize()
        finite_value = torch.isfinite(result).all()
        finite = bool(finite_value.item() if hasattr(finite_value, "item") else finite_value)
    except Exception as exc:
        report["matmul"] = "failed"
        return _fail(
            report,
            "kernel_failure",
            "CUDA 内核验证失败：显存分配或 float32 矩阵乘法执行时出错。",
            stage="allocation_or_matmul",
            detail=_exception_detail(exc),
        )

    device_type = getattr(getattr(result, "device", None), "type", None)
    if device_type != "cuda":
        report["matmul"] = "failed"
        return _fail(
            report,
            "kernel_failure",
            "CUDA 内核验证失败：矩阵乘法结果未位于 CUDA 设备上。",
            stage="device_check",
            detail=f"result device type: {device_type!r}",
        )

    if not finite:
        report["matmul"] = "failed"
        report["finite"] = False
        return _fail(
            report,
            "kernel_failure",
            "CUDA 内核验证失败：float32 矩阵乘法返回 NaN 或 Inf，结果不是有限值。",
            stage="finite_check",
        )

    report["memory_allocated_bytes"] = _safe_int(lambda: cuda.memory_allocated(0))
    report["memory_reserved_bytes"] = _safe_int(lambda: cuda.memory_reserved(0))
    report["matmul"] = "passed"
    report["finite"] = True
    report["ok"] = True
    report["status"] = "ok"
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查本机 PyTorch CUDA 显存分配、float32 矩阵乘法和结果有限性。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="可选：将 UTF-8 JSON 报告写入指定文件。",
    )
    args = parser.parse_args(argv)

    try:
        report = probe()
    except Exception as exc:
        report = _fail(
            _base_report(),
            "probe_failure",
            "GPU 验证过程中发生未预期错误，请重新执行 uv sync 后重试。",
            detail=_exception_detail(exc),
        )

    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(payload, flush=True)

    write_failed = False
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.write("\n")
        except OSError as exc:
            write_failed = True
            print(
                f"无法写入 GPU 验证报告：{_exception_detail(exc)}",
                file=sys.stderr,
                flush=True,
            )

    if not report["ok"]:
        message = report.get("error", {}).get("message", "GPU 验证失败。")
        print(f"GPU 验证失败：{message}", file=sys.stderr, flush=True)
    return 0 if report["ok"] and not write_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
