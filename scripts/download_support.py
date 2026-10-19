"""Small, bounded download helpers shared by the explicit model installers.

This module only handles setup-time downloads.  It deliberately has no model
loading code and never removes an existing completed file.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable

import httpx


DEFAULT_ATTEMPTS = 3
CHUNK_SIZE = 1024 * 1024
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class DownloadError(RuntimeError):
    """A concise setup error suitable for an end user."""


class IntegrityError(DownloadError):
    """A transfer completed but failed a protocol or checksum check."""


def sha256_file(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_matches(path: Path, *, sha256: str | None = None, size: int | None = None) -> bool:
    path = Path(path)
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    return sha256 is None or sha256_file(path) == sha256


def safe_url(url: str) -> str:
    """Return a diagnostic URL with credentials, query strings and fragments removed."""
    parsed = httpx.URL(url)
    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.host}{path}"


def human_bytes(value: int | None) -> str:
    if value is None:
        return "未知大小"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _setup_log(message: str) -> None:
    """Show progress in the setup console and append it to the group log."""
    print(message, flush=True)
    log_path = os.environ.get("TIMING_DOWNLOAD_LOG")
    if log_path:
        try:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(message + "\n")
        except OSError:
            pass


def _atomic_replace(source: Path, destination: Path) -> None:
    """Publish atomically, tolerating brief Windows scanner/indexer sharing races."""
    for attempt in range(5):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _header_int(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise IntegrityError(f"服务器返回了无效的 {name}。") from exc
    if parsed < 0:
        raise IntegrityError(f"服务器返回了无效的 {name}。")
    return parsed


def _content_range(value: str | None) -> tuple[int | None, int | None, int]:
    """Parse Content-Range as (start, end, total), including ``bytes */N``."""
    if not value:
        raise IntegrityError("服务器缺少 Content-Range，无法验证断点续传。")
    raw = value.strip()
    if raw.lower().startswith("bytes "):
        raw = raw[6:].lstrip()
    left, separator, right = raw.partition("/")
    if not separator or not right.isdigit():
        raise IntegrityError("服务器返回了无效的 Content-Range。")
    total = int(right)
    if left == "*":
        return None, None, total
    first, dash, last = left.partition("-")
    if not dash or not first.isdigit() or not last.isdigit():
        raise IntegrityError("服务器返回了无效的 Content-Range。")
    start, end = int(first), int(last)
    if end < start or total <= end:
        raise IntegrityError("服务器返回了越界的 Content-Range。")
    return start, end, total


class _Progress:
    def __init__(self, label: str, total: int | None, log: Callable[[str], None]):
        self.label = label
        self.total = total
        self.log = log
        self.started = time.monotonic()
        self.last = self.started
        self.next_bytes = 64 * 1024 * 1024

    def update(self, written: int, complete: int) -> None:
        now = time.monotonic()
        if (self.total is None or complete < self.total) and written < self.next_bytes and now - self.last < 3:
            return
        self.last = now
        self.next_bytes = written + 64 * 1024 * 1024
        if self.total:
            percent = min(100, int(complete * 100 / self.total))
            self.log(f"{self.label}：{percent}%（{human_bytes(complete)} / {human_bytes(self.total)}）")
        else:
            self.log(f"{self.label}：已下载 {human_bytes(complete)}")


def fetch(
    url: str,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    client: httpx.Client | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
    label: str | None = None,
    validate: Callable[[Path], None] | None = None,
) -> Path:
    """Fetch one file with bounded retries, safe resume and atomic publication.

    A server that ignores ``Range`` is handled by truncating the partial file
    before writing the fresh 200 response.  A 206 response is accepted only
    when its range, length and final size agree.  Checksums are checked before
    ``os.replace`` publishes the file.
    """
    destination = Path(destination)
    log = log or _setup_log
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    display = label or destination.name
    attempts = max(1, int(attempts))

    if file_matches(destination, sha256=expected_sha256, size=expected_size):
        try:
            if validate is not None:
                _validated(validate, destination, display)
        except Exception:
            log(f"{display}：本地文件未通过格式校验，将重新获取。")
        else:
            log(f"{display}：本地文件校验通过，跳过下载。")
            return destination
    if destination.exists():
        log(f"{display}：现有文件未通过校验，将在新文件校验成功后替换。")

    owns_client = client is None
    active_client = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0),
    )
    last_error = "网络请求失败。"
    try:
        for attempt in range(1, attempts + 1):
            offset = part.stat().st_size if part.is_file() else 0
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            try:
                with active_client.stream("GET", url, headers=headers) as response:
                    status = response.status_code
                    if status == 416:
                        _, _, total = _content_range(response.headers.get("content-range"))
                        complete_part = offset and total == offset and file_matches(
                            part, sha256=expected_sha256, size=expected_size)
                        if complete_part and validate is not None:
                            try:
                                _validated(validate, part, display)
                            except Exception:
                                complete_part = False
                        if complete_part:
                            _atomic_replace(part, destination)
                            log(f"{display}：断点文件已完整，校验通过。")
                            return destination
                        if not offset:
                            raise DownloadError("服务器拒绝了完整文件请求（HTTP 416）。")
                        log(f"{display}：服务器拒绝当前续传位置，将从文件开头重新下载。")
                        part.open("wb").close()
                        last_error = "断点位置已失效。"
                        if attempt < attempts:
                            sleeper(min(2 ** (attempt - 1), 4))
                            continue
                        raise DownloadError(last_error)

                    if status not in (200, 206):
                        message = f"服务器返回 HTTP {status}。"
                        if status in RETRY_STATUS and attempt < attempts:
                            last_error = message
                            log(f"{display}：{message} 第 {attempt}/{attempts} 次失败，将重试。")
                            sleeper(min(2 ** (attempt - 1), 4))
                            continue
                        raise DownloadError(message)

                    content_length = _header_int(response, "content-length")
                    range_start = 0
                    total = expected_size
                    if status == 206:
                        range_start, range_end, range_total = _content_range(response.headers.get("content-range"))
                        if range_start is None or range_start != offset:
                            raise IntegrityError("服务器返回的续传起点与本地 .part 不一致。")
                        if expected_size is not None and range_total != expected_size:
                            raise IntegrityError("服务器返回的文件总大小与固定清单不一致。")
                        if range_end != range_total - 1:
                            raise IntegrityError("服务器返回的续传范围没有覆盖文件末尾。")
                        total = range_total
                        remaining = max(0, range_total - range_start)
                        if content_length is not None and content_length != remaining:
                            raise IntegrityError("服务器返回的 Content-Length 与续传范围不一致。")
                    elif offset:
                        log(f"{display}：服务器未接受断点续传，已从头重新下载。")

                    if status == 200 and content_length is not None:
                        if expected_size is not None and content_length != expected_size:
                            raise IntegrityError("服务器返回的文件大小与固定清单不一致。")
                        total = content_length
                    mode = "ab" if status == 206 and range_start else "wb"
                    if mode == "wb":
                        offset = 0
                    written = offset
                    progress = _Progress(display, total, log)
                    log(f"{display}：开始下载（{human_bytes(total)}）。可续传时下次会从 .part 继续。")
                    with part.open(mode) as stream:
                        for chunk in response.iter_bytes(CHUNK_SIZE):
                            if not chunk:
                                continue
                            stream.write(chunk)
                            written += len(chunk)
                            progress.update(written, written)
                    if status == 206 and total is not None and part.stat().st_size != total:
                        raise IntegrityError("续传后文件大小与服务器声明不一致。")
                    if status == 200 and content_length is not None and written != content_length:
                        raise IntegrityError("下载字节数与 Content-Length 不一致。")
                    if expected_size is not None and part.stat().st_size != expected_size:
                        raise IntegrityError("下载文件大小与固定清单不一致。")
                    if expected_sha256 and sha256_file(part) != expected_sha256:
                        raise IntegrityError("下载文件 SHA-256 校验失败。")
                    if validate is not None:
                        _validated(validate, part, display)
                    _atomic_replace(part, destination)
                    log(f"{display}：下载完成并通过校验。")
                    return destination
            except (httpx.HTTPError, OSError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    last_error = str(exc)
                elif isinstance(exc, PermissionError):
                    last_error = "无法写入模型文件，请检查目录权限或文件是否被占用。"
                elif isinstance(exc, OSError):
                    last_error = ("磁盘空间不足，请腾出空间后重新运行安装。" if exc.errno == errno.ENOSPC
                                  else "本地文件读写失败，请检查磁盘、目录权限和文件占用。")
                else:
                    last_error = "网络连接中断或超时。"
                if attempt < attempts:
                    log(f"{display}：{last_error} 第 {attempt}/{attempts} 次失败，将重试。")
                    sleeper(min(2 ** (attempt - 1), 4))
                    continue
                raise DownloadError(f"{last_error} 已尝试 {attempts} 次。") from exc
            except DownloadError:
                raise
    finally:
        if owns_client:
            active_client.close()
    raise DownloadError(f"{last_error} 已尝试 {attempts} 次（来源：{safe_url(url)}）。")


def _validated(validate: Callable[[Path], None], path: Path, label: str) -> bool:
    validate(path)
    return True


def atomic_write_text(path: Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    with part.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    _atomic_replace(part, path)
    return path


def atomic_write_json(path: Path, value: object) -> Path:
    return atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def friendly_failure(group: str, rerun: str, error: BaseException) -> str:
    detail = error_tail(str(error), "未提供详细错误。")
    detail = detail[:500] if detail else "未知错误。"
    return (
        f"模型组「{group}」下载失败：{detail}\n"
        f"请重新运行：{rerun}\n"
        "下载器会在服务器支持时从 .part 续传；不支持时会自动从头下载。"
    )


def error_tail(text: str, fallback: str = "运行依赖未提供详细错误。") -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][-300:] if lines else fallback
