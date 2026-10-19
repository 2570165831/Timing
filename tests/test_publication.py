"""Offline tests for the independent publication hygiene checker.

Every test builds a disposable git repository below pytest's tmp_path, so the
real checkout is never touched and nothing reaches the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_publication.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_publication", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _run_git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, encoding="utf-8", errors="replace"
    )
    return completed.stdout


def _write(root: Path, name: str, text: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_bytes(root: Path, name: str, data: bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _scan(root: Path):
    return checker.scan(checker.resolve_repo_root(root))


def _rules(report):
    return {(finding.path, finding.rule) for finding in report.findings}


def _run_cli(root: Path, *extra: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run_git(root, "init", "-q")
    return root


def test_candidate_set_follows_git_ignore_rules(repo):
    _write(repo, ".gitignore", "models/\ndata/\n")
    _write(repo, "src/app.py", "print('hi')\n")
    _write(repo, "models/weights.bin", "x")
    _write(repo, "data/song.wav", "x")

    candidates = checker.list_files(repo)
    assert sorted(candidates) == [".gitignore", "src/app.py"]

    report = _scan(repo)
    assert report.candidate_count == 2
    assert report.findings == ()


def test_tracked_ignored_runtime_file_is_still_reported(repo):
    _write(repo, ".gitignore", "data/\n")
    _write(repo, "data/notes.txt", "forced runtime capture\n")
    _write(repo, "data/other.txt", "left untracked\n")
    _run_git(repo, "add", "-f", "data/notes.txt")

    candidates = checker.list_files(repo)
    assert "data/notes.txt" in candidates
    assert "data/other.txt" not in candidates

    assert ("data/notes.txt", "private-runtime-root:data") in _rules(_scan(repo))


def test_runtime_and_temporary_roots_are_detected(repo):
    expected = {
        ".venv/lib/python3.12/site-packages/pkg/__init__.py": "private-runtime-root:.venv",
        ".runtime/cache/blob.json": "private-runtime-root:.runtime",
        ".codegraph/index.db": "private-runtime-root:.codegraph",
        "node_modules/left-pad/index.js": "private-runtime-root:node_modules",
        "pytest-of-someone/pytest-3/test_x/out.txt": "temp-runtime-root:pytest-of-*",
        "torchinductor_someone/cache/triton/kernel.py": "temp-runtime-root:torchinductor_*",
        "models/config.json": "private-runtime-root:models",
        "data/meta.json": "private-runtime-root:data",
        ".planning/notes.md": "planning-doc:.planning",
    }
    for name in expected:
        _write(repo, name, "x\n")

    found = _rules(_scan(repo))
    for name, rule in expected.items():
        assert (name, rule) in found, name


def test_nested_models_directory_is_not_a_runtime_root(repo):
    _write(repo, "timing/models/loader.py", "VALUE = 1\n")
    assert _scan(repo).findings == ()


def test_local_planning_documents_are_blockers(repo):
    documents = ("task_plan.md", "progress.md", "findings.md", "design-qa.md")
    _write(repo, "README.md", "# readme\n")
    for name in documents:
        _write(repo, name, "# plan\n")

    found = _rules(_scan(repo))
    for name in documents:
        assert (name, "planning-doc:" + name) in found
    assert ("README.md", "planning-doc:README.md") not in found


def test_large_file_threshold_is_ten_mib(repo):
    _write_bytes(repo, "at_limit.txt", b"x" * (10 * 1024 * 1024))
    _write_bytes(repo, "over_limit.dat", b"x" * (10 * 1024 * 1024 + 1))

    found = _rules(_scan(repo))
    assert ("over_limit.dat", "large-file") in found
    assert ("at_limit.txt", "large-file") not in found


def test_audio_media_model_and_archive_artifacts_are_blockers(repo):
    _write_bytes(repo, "vocals.wav", b"RIFF")
    _write_bytes(repo, "stems/Song.FLAC", b"fLaC")
    _write_bytes(repo, "clips/take.mp4", b"\x00\x00")
    _write_bytes(repo, "voice/model.safetensors", b"x")
    _write_bytes(repo, "bundle/release.zip", b"PK\x03\x04")

    found = _rules(_scan(repo))
    assert ("vocals.wav", "audio-artifact") in found
    assert ("stems/Song.FLAC", "audio-artifact") in found
    assert ("clips/take.mp4", "media-artifact") in found
    assert ("voice/model.safetensors", "model-artifact") in found
    assert ("bundle/release.zip", "archive-artifact") in found


def test_credential_file_paths_are_flagged_but_templates_are_allowed(repo):
    flagged = {
        ".env": "credential-file:.env",
        "config/.env.production": "credential-file:.env",
        "web/.env.local": "credential-file:.env",
        "certs/server.pem": "credential-file:.pem",
        "keys/id_rsa": "credential-file:id_rsa",
    }
    for name in flagged:
        _write(repo, name, "placeholder\n")
    _write(repo, ".env.example", "OPENAI_API_KEY=\n")
    _write(repo, "config/.env.template", "TOKEN=\n")

    found = _rules(_scan(repo))
    for name, rule in flagged.items():
        assert (name, rule) in found, name
    assert (".env.example", "credential-file:.env") not in found
    assert ("config/.env.template", "credential-file:.env") not in found


def test_secret_literals_are_reported_without_the_value(repo):
    live_aws = "AKIA" + "J7QX2W9ZR4TMNPB6"
    live_github = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    live_jwt = ".".join(
        [
            "eyJ" + "hbGciOiJIUzI1NiJ9",
            "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0",
            "dBjftJeZ4CVP" + "mZv7W3Pq9HsK2",
        ]
    )
    _write(
        repo,
        "settings.py",
        "# comment\n"
        'aws = "' + live_aws + '"\n'
        'github = "' + live_github + '"\n'
        'jwt = "' + live_jwt + '"\n',
    )

    report = _scan(repo)
    assert [(finding.rule, finding.path, finding.line) for finding in report.findings] == [
        ("secret:aws-access-key-id", "settings.py", 2),
        ("secret:github-token", "settings.py", 3),
        ("secret:json-web-token", "settings.py", 4),
    ]
    serialized = json.dumps(report.as_dict())
    for value in (live_aws, live_github, live_jwt):
        assert value not in serialized


def test_quoted_and_environment_style_assignments_are_detected(repo):
    quoted = "aB3xK9mQ7pL2vN8rT4wZ6yH1"
    inline = "9f8A7b6C5d4E3f2G1h0I" + "jKlMnOpQrSt"
    _write(repo, "settings.py", 'api_key = "' + quoted + '"\n')
    _write(repo, "settings/sample.conf", "AWS_ACCESS_KEY_ID=" + inline + "\n")

    assert (
        ("settings.py", "secret:generic-assignment") in _rules(_scan(repo))
    )
    assert (
        ("settings/sample.conf", "secret:generic-assignment") in _rules(_scan(repo))
    )


def test_placeholders_and_environment_references_are_not_secrets(repo):
    dummy_aws = "AKIA" + "IOSFODNN7EXAMPLE"
    dummy_key = "sk-" + "test-4f9c2b7e1a8d3c6f5e0b"
    repeated = "a" * 32
    environment_reference = "$" + "{DB_PASSWORD}"
    _write(
        repo,
        "tests/fixtures.py",
        "import os\n"
        'API_KEY = os.getenv("OPENAI_API_KEY")\n'
        'AWS_EXAMPLE = "' + dummy_aws + '"\n'
        'MODEL_KEY = "' + dummy_key + '"\n'
        'TOKEN = "' + repeated + '"\n'
        'PASSWORD = "' + environment_reference + '"\n'
        'CLIENT_SECRET = "PLACEHOLDER_VALUE_FOR_TESTS"\n'
        'api_key = "your-api-key-here"\n',
    )

    assert _scan(repo).findings == ()


def test_runtime_expressions_are_not_mistaken_for_credentials(repo):
    _write(
        repo,
        "timing/engine.py",
        "TOKEN = secrets.token_urlsafe(32)\n"
        "token = _STATE.set(True)\n"
        "timestamp_token_id=self.model.config.timestamp_token_id,\n"
        "key = self.key(word, phones)\n",
    )

    assert _scan(repo).findings == ()


def test_report_counts_candidates_and_total_size(repo):
    _write(repo, "a.py", "x" * 10)
    _write(repo, "b/c.txt", "y" * 5)

    report = _scan(repo)
    assert report.candidate_count == 2
    assert report.candidate_bytes == 15
    assert report.blocked is False
    assert report.as_dict()["clean"] is True


def test_cli_exit_codes_and_json_report(repo):
    _write(repo, "src/app.py", "print('ok')\n")

    clean = _run_cli(repo)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "source candidates: 1 files" in clean.stdout
    assert "RESULT: CLEAN" in clean.stdout

    _write(repo, "task_plan.md", "# plan\n")
    blocked = _run_cli(repo)
    assert blocked.returncode == 1
    assert "planning-doc:task_plan.md" in blocked.stdout
    assert "RESULT: BLOCKED" in blocked.stdout

    payload = json.loads(_run_cli(repo, "--json").stdout)
    assert payload["clean"] is False
    assert payload["candidate_count"] == 2
    assert payload["findings"] == [
        {"rule": "planning-doc:task_plan.md", "path": "task_plan.md", "line": None, "severity": "blocker"}
    ]


def test_cli_rejects_relative_root_and_non_repository(repo, tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as error:
        checker.main(["--root", "relative/path"])
    assert error.value.code == 2

    assert checker.main(["--root", str(tmp_path / "does-not-exist")]) == 2

    # pytest's basetemp can live inside a git work tree, so block upward
    # discovery instead of assuming the directory is outside any repository.
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert checker.main(["--root", str(plain)]) == 2


def test_scan_leaves_the_repository_untouched(repo):
    _write(repo, "task_plan.md", "# plan\n")
    _write(repo, ".env", "PLACEHOLDER=1\n")
    _write(repo, "src/app.py", "print('ok')\n")
    before = _git_output(repo, "status", "--porcelain")

    assert _scan(repo).blocked is True

    assert _git_output(repo, "status", "--porcelain") == before
    assert not list(repo.glob(".git/index.lock"))


def test_checker_and_its_own_tests_are_clean_under_their_rules(repo):
    source_root = CHECKER_PATH.parents[1]
    for relative in ("scripts/check_publication.py", "tests/test_publication.py"):
        _write(repo, relative, (source_root / relative).read_text(encoding="utf-8"))

    report = _scan(repo)
    assert report.candidate_count == 2
    assert report.findings == ()
