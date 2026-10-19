#!/usr/bin/env python3
"""Independent publication hygiene checker for the Timing repository.

The checker answers one question: does the working tree contain anything that
must not be published?  It enumerates the files git would publish and reports
private/runtime roots, oversized files, binary media/model/archive artifacts
and credential material.

Enumeration
-----------
"git ls-files -z --cached --others --exclude-standard" runs through
subprocess with the repository root as the working directory.  "--cached"
lists every tracked entry; "--others --exclude-standard" adds untracked files
that are not ignored.  That union is exactly the set of files a commit or a
packaging step would pick up.  Only the index and the work tree are read: the
checker never writes to the repository, never touches git history and never
stages anything.

Consequences of that enumeration (documented scope)
---------------------------------------------------
* Ignored *untracked* files are out of scope by construction -- a downloaded
  model below models/ never becomes a candidate.
* A file that is ignored but *tracked* (force-added, for example a stray
  runtime capture below data/) is still reported, because "--cached" bypasses
  ignore rules.
* A listed path missing on disk (staged deletion) has no size and is not
  content-scanned.

Conservative by design
----------------------
* The clipped names "models" and "data" only match at the repository root, so
  a nested source package named models is not flagged.
* Files already blocked by a path rule, binary artifacts and files above the
  size limit are not content-scanned.
* Credential matching needs a long, non-placeholder literal.  Environment
  variable references, all-caps variable names, repeated filler, obvious dummy
  fixtures and .env.example-style templates are excluded on purpose.

Output
------
Findings render as "[severity] rule  path[:line]".  A matched credential is
reported by rule and line only -- the value is never printed or serialized.
Exit status is 1 when a blocker exists, 0 when the tree is clean and 2 for a
usage error or a path that is not a git work tree.

Rule names (stable, asserted by the tests)
-----------------------------------------
private-runtime-root:<name>        .venv, .runtime, .codegraph, node_modules at
                                   any depth; models and data at the repo root
temp-runtime-root:pytest-of-*      pytest-of-* directories
temp-runtime-root:torchinductor_*  torch inductor cache directories
planning-doc:<name>                .planning, task_plan.md, progress.md,
                                   findings.md, design-qa.md
large-file                         larger than 10 MiB
audio-artifact, media-artifact, model-artifact, archive-artifact
credential-file:<name>             .env, *.pem, private keys, ...
secret:<pattern>                   credential literals in file contents
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 10 * 1024 * 1024

# --------------------------------------------------------------------------- #
# Path rules
# --------------------------------------------------------------------------- #

# Unambiguous runtime/tooling directories, matched at any depth.
ANY_DEPTH_DIRS = {
    ".venv": "private-runtime-root:.venv",
    ".runtime": "private-runtime-root:.runtime",
    ".codegraph": "private-runtime-root:.codegraph",
    "node_modules": "private-runtime-root:node_modules",
    ".planning": "planning-doc:.planning",
}

# Words that are also plausible source directory names, so they only count as
# runtime roots at the top level of the repository.
REPOSITORY_ROOT_DIRS = {
    "models": "private-runtime-root:models",
    "data": "private-runtime-root:data",
}

TEMP_ROOT_DIRS = (
    (re.compile(r"pytest-of-", re.IGNORECASE), "temp-runtime-root:pytest-of-*"),
    (re.compile(r"torchinductor_", re.IGNORECASE), "temp-runtime-root:torchinductor_*"),
)

PLANNING_DOCUMENTS = {
    "task_plan.md": "planning-doc:task_plan.md",
    "progress.md": "planning-doc:progress.md",
    "findings.md": "planning-doc:findings.md",
    "design-qa.md": "planning-doc:design-qa.md",
}

AUDIO_EXTENSIONS = frozenset({
    ".wav", ".wave", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus",
    ".wma", ".aif", ".aiff", ".ape", ".wv", ".dsf", ".dff", ".caf", ".amr",
    ".au", ".ra", ".mka", ".mid", ".midi", ".m4b", ".aax",
})

MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg",
    ".mpeg",
})

MODEL_EXTENSIONS = frozenset({
    ".pt", ".pth", ".ckpt", ".safetensors", ".safetensor", ".bin", ".onnx",
    ".h5", ".pb", ".tflite", ".gguf", ".keras", ".mlmodel", ".npz", ".npy",
    ".msgpack", ".params", ".weights", ".model", ".ot",
})

ARCHIVE_EXTENSIONS = frozenset({
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz", ".7z",
    ".rar", ".zst", ".lz", ".lz4", ".cab", ".iso", ".whl", ".jar", ".dmg",
    ".pkg", ".deb", ".rpm", ".apk",
})

# Text-shaped names that are still never worth scanning for credentials.
NON_TEXT_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".avif", ".heic", ".psd", ".pdf", ".woff", ".woff2", ".ttf", ".otf",
    ".eot", ".exe", ".dll", ".so", ".dylib", ".pyd", ".pyc", ".pyo", ".class",
    ".o", ".a", ".lib", ".obj", ".sqlite", ".sqlite3", ".db", ".parquet",
    ".arrow", ".feather", ".pkl", ".pickle", ".wasm", ".node",
})

CREDENTIAL_FILE_EXTENSIONS = frozenset({
    "pem", "key", "p12", "pfx", "jks", "keystore", "ppk",
})

CREDENTIAL_FILE_NAMES = frozenset({
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".pgpass",
})

# .env.example and friends are templates, not credentials.
ALLOWED_ENV_TEMPLATE_SUFFIXES = frozenset({"example", "sample", "template", "dist"})

# --------------------------------------------------------------------------- #
# Content rules
# --------------------------------------------------------------------------- #

PREFIX_PATTERNS = (
    ("secret:aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("secret:github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("secret:anthropic-api-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("secret:openai-api-key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}\b")),
    ("secret:slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("secret:google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("secret:huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("secret:json-web-token", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("secret:bearer-token", re.compile(r"(?i)\bbearer[ \t]+[A-Za-z0-9._\-]{20,}")),
    ("secret:private-key-block", re.compile(r"-----[ \t]*BEGIN[ \t]+[A-Z ]*PRIVATE KEY-----")),
)

# api_key = "..." and "client_secret": "..." style literals.
ASSIGNMENT_KEYWORDS = re.compile(
    r"(?i)(?:api[_-]?key|apikey|secret[_-]?key|client[_-]?secret|secret"
    r"|access[_-]?token|auth[_-]?token|refresh[_-]?token|private[_-]?key"
    r"|password|passwd)"
)

QUOTED_VALUE = re.compile(
    r"""
    [ \t]*[:=][ \t]*
    (?P<quote>["'])(?P<value>[^"'\\\s]{20,})(?P=quote)
    """,
    re.VERBOSE,
)

# API_TOKEN=... lines, the shape of an .env file.  The value class is limited
# to token characters so that runtime expressions such as
# "TOKEN = secrets.token_urlsafe(32)" or "token = _STATE.set(True)" are not
# mistaken for credentials.  Only applied to short lines so that a huge
# single-line bundle cannot be scanned quadratically.
ENVIRONMENT_LINE = re.compile(
    r"""
    [ \t]*(?:export[ \t]+)?
    (?P<key>[A-Za-z0-9_]*(?:key|token|secret|password|passwd|pwd)[A-Za-z0-9_]*)
    [ \t]*[:=][ \t]*
    (?P<value>[A-Za-z0-9_\-+/=.]{20,})
    [ \t]*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

ENVIRONMENT_LINE_LIMIT = 8192

PLACEHOLDER_TOKENS = (
    "example", "sample", "dummy", "fake", "placeholder", "changeme",
    "change_me", "change-me", "redacted", "replace", "your_", "your-",
    "notreal", "not-real", "lorem", "hunter2", "xxxx", "abc123",
    "1234567890", "0000000000", "test",
)

_REPEATED_CHARACTERS = re.compile(r"(.)\1{7,}")
_ENVIRONMENT_REFERENCE = re.compile(r"^(?:\$|%|\{\{|env:|os\.environ|process\.env)")
_UPPERCASE_NAME = re.compile(r"[A-Z0-9_]+")

# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int | None = None
    severity: str = "blocker"

    def render(self) -> str:
        location = self.path if self.line is None else f"{self.path}:{self.line}"
        return f"[{self.severity}] {self.rule}  {location}"


@dataclass(frozen=True)
class Report:
    root: str
    candidate_count: int
    candidate_bytes: int
    findings: tuple[Finding, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict:
        return {
            "root": self.root,
            "candidate_count": self.candidate_count,
            "candidate_bytes": self.candidate_bytes,
            "clean": not self.findings,
            "findings": [
                {
                    "rule": finding.rule,
                    "path": finding.path,
                    "line": finding.line,
                    "severity": finding.severity,
                }
                for finding in self.findings
            ],
        }


# --------------------------------------------------------------------------- #
# Enumeration
# --------------------------------------------------------------------------- #


def resolve_repo_root(root: Path) -> Path:
    """Return the git work-tree root for root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(root),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message or "not a git work tree")
    return Path(result.stdout.decode("utf-8", "replace").strip())


def list_files(root: Path) -> list[str]:
    """Return the publishable file set: tracked plus untracked, non-ignored."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=str(root),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message or "git ls-files failed")
    names = result.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [name for name in names if name]


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def _extension(posix: str) -> str:
    name = posix.rsplit("/", 1)[-1].lower()
    _, dot, extension = name.rpartition(".")
    return f".{extension}" if dot else ""


def _directory_rule(segment: str, depth: int) -> str | None:
    lowered = segment.lower()
    rule = ANY_DEPTH_DIRS.get(lowered)
    if rule:
        return rule
    if depth == 0 and lowered in REPOSITORY_ROOT_DIRS:
        return REPOSITORY_ROOT_DIRS[lowered]
    for pattern, temp_rule in TEMP_ROOT_DIRS:
        if pattern.match(segment):
            return temp_rule
    return None


def _path_rules(posix: str) -> list[str]:
    segments = posix.split("/")
    rules: list[str] = []
    for depth, segment in enumerate(segments[:-1]):
        rule = _directory_rule(segment, depth)
        if rule:
            rules.append(rule)
    document_rule = PLANNING_DOCUMENTS.get(segments[-1])
    if document_rule:
        rules.append(document_rule)
    return rules


def _credential_file_rule(posix: str) -> str | None:
    name = posix.rsplit("/", 1)[-1].lower()
    if name == ".env":
        return "credential-file:.env"
    if name.startswith(".env."):
        suffix = name.split(".env.", 1)[1]
        if suffix in ALLOWED_ENV_TEMPLATE_SUFFIXES:
            return None
        return "credential-file:.env"
    _, dot, extension = name.rpartition(".")
    if dot and extension in CREDENTIAL_FILE_EXTENSIONS:
        return f"credential-file:.{extension}"
    if name in CREDENTIAL_FILE_NAMES:
        return f"credential-file:{name}"
    return None


def _artifact_rule(posix: str) -> str | None:
    extension = _extension(posix)
    if extension in AUDIO_EXTENSIONS:
        return "audio-artifact"
    if extension in MEDIA_EXTENSIONS:
        return "media-artifact"
    if extension in MODEL_EXTENSIONS:
        return "model-artifact"
    if extension in ARCHIVE_EXTENSIONS:
        return "archive-artifact"
    return None


def _is_binary_name(posix: str) -> bool:
    extension = _extension(posix)
    return extension in (
        AUDIO_EXTENSIONS
        | MEDIA_EXTENSIONS
        | MODEL_EXTENSIONS
        | ARCHIVE_EXTENSIONS
        | NON_TEXT_EXTENSIONS
    )


# --------------------------------------------------------------------------- #
# Credential content rules
# --------------------------------------------------------------------------- #


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    if any(token in lowered for token in PLACEHOLDER_TOKENS):
        return True
    return bool(_REPEATED_CHARACTERS.search(value))


def _is_secret_literal(value: str) -> bool:
    value = value.strip()
    if len(value) < 20:
        return False
    if _ENVIRONMENT_REFERENCE.match(value):
        return False
    if _UPPERCASE_NAME.fullmatch(value):
        return False
    if _looks_like_placeholder(value):
        return False
    if len(set(value)) <= 8:
        return False
    has_alpha = any(character.isalpha() for character in value)
    has_digit = any(character.isdigit() for character in value)
    has_symbol = any(not character.isalnum() for character in value)
    return has_alpha and (has_digit or has_symbol)


def _line_secret_rules(line: str) -> list[str]:
    rules: list[str] = []
    for rule, pattern in PREFIX_PATTERNS:
        matched = pattern.finditer(line)
        if any(not _looks_like_placeholder(match.group(0)) for match in matched):
            rules.append(rule)
    for match in ASSIGNMENT_KEYWORDS.finditer(line):
        quoted = QUOTED_VALUE.match(line[match.end():])
        if quoted and _is_secret_literal(quoted.group("value")):
            rules.append("secret:generic-assignment")
            break
    if len(line) <= ENVIRONMENT_LINE_LIMIT:
        environment = ENVIRONMENT_LINE.match(line)
        if environment and _is_secret_literal(environment.group("value")):
            rules.append("secret:generic-assignment")
    return rules


def _content_findings(path: Path, posix: str) -> list[Finding]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:8192]:
        return []
    findings: list[Finding] = []
    text = raw.decode("utf-8", "replace")
    for number, line in enumerate(text.splitlines(), start=1):
        for rule in _line_secret_rules(line):
            findings.append(Finding(rule, posix, number))
    return findings


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def scan(root: Path) -> Report:
    """Check a repository whose work-tree root is root (read-only)."""
    root = Path(root)
    entries = list_files(root)
    findings: list[Finding] = []
    total_bytes = 0
    for relative in entries:
        posix = relative.replace("\\", "/")
        try:
            size: int | None = (root / relative).stat().st_size
        except OSError:
            size = None
        if size is not None:
            total_bytes += size
        blocked = False
        for rule in _path_rules(posix):
            findings.append(Finding(rule, posix))
            blocked = True
        credential_rule = _credential_file_rule(posix)
        if credential_rule:
            findings.append(Finding(credential_rule, posix))
            blocked = True
        artifact_rule = _artifact_rule(posix)
        if artifact_rule:
            findings.append(Finding(artifact_rule, posix))
            blocked = True
        if size is not None and size > MAX_FILE_BYTES:
            findings.append(Finding("large-file", posix))
            blocked = True
        if blocked or size is None or _is_binary_name(posix):
            continue
        findings.extend(_content_findings(root / relative, posix))

    unique: list[Finding] = []
    seen: set[tuple[str, str, int | None]] = set()
    for finding in findings:
        key = (finding.path, finding.rule, finding.line)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    unique.sort(key=lambda finding: (finding.path, finding.line or 0, finding.rule))
    return Report(str(root), len(entries), total_bytes, tuple(unique))


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_publication",
        description="Report files that must not be published (read-only).",
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        default=None,
        help="absolute path of the repository to check (default: the current directory)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    arguments = parser.parse_args(argv)

    if arguments.root is None:
        root = Path.cwd()
    else:
        root = Path(arguments.root)
        if not root.is_absolute():
            parser.error("--root must be an absolute path")

    try:
        repository = resolve_repo_root(root)
    except (RuntimeError, OSError) as error:
        print(f"check_publication: {root} is not a git work tree ({error})", file=sys.stderr)
        return 2

    report = scan(repository)
    if arguments.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"root: {report.root}")
        print(
            f"source candidates: {report.candidate_count} files, "
            f"{_human_bytes(report.candidate_bytes)}"
        )
        if not report.findings:
            print("findings: 0")
            print("RESULT: CLEAN")
        else:
            print(f"findings: {len(report.findings)} blockers")
            for finding in report.findings:
                print("  " + finding.render())
            print("RESULT: BLOCKED")
    return 1 if report.findings else 0


if __name__ == "__main__":
    sys.exit(main())
