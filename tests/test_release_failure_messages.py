"""Operator-facing release failures preserve the cause and say what to run next."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "public_release_check.py"
PUBLISHER = ROOT / "scripts" / "publish.sh"


def run_scanner(deny_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--deny-file", str(deny_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_missing_deny_file_names_setup_and_exact_rerun(tmp_path: Path) -> None:
    deny_file = tmp_path / "missing-deny-file"
    result = run_scanner(deny_file)
    assert result.returncode == 2
    assert "private deny file is missing" in result.stdout
    assert "Next: copy .public-release-deny-patterns.example" in result.stdout
    assert f"--deny-file {deny_file}" in result.stdout


def test_invalid_regex_keeps_raw_cause_and_exact_rerun(tmp_path: Path) -> None:
    deny_file = tmp_path / "invalid-deny-file"
    deny_file.write_text("[\n", encoding="utf-8")
    result = run_scanner(deny_file)
    assert result.returncode == 2
    assert "invalid private deny regex on line 1" in result.stdout
    assert "unterminated character set" in result.stdout
    assert f"Next: correct {deny_file} at the reported line" in result.stdout
    assert f"--deny-file {deny_file}" in result.stdout


def test_findings_summary_names_history_boundary_and_exact_rerun(tmp_path: Path) -> None:
    deny_file = tmp_path / "matching-deny-file"
    deny_file.write_text("Agent Control Plane\n", encoding="utf-8")
    result = run_scanner(deny_file)
    assert result.returncode == 1
    assert "Release blocked:" in result.stdout
    assert "Next: correct every BLOCK location" in result.stdout
    assert "obtain owner approval before any history rewrite" in result.stdout
    assert f"--deny-file {deny_file}" in result.stdout


def publisher_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "publisher-fixture"
    scripts = repo / "scripts"
    commands = repo / "commands"
    scripts.mkdir(parents=True)
    commands.mkdir()
    shutil.copyfile(PUBLISHER, scripts / "publish.sh")
    (scripts / "publish.sh").chmod(0o755)
    (commands / "python3").write_text(
        "#!/bin/sh\nexit \"${STUB_SCAN_RC:-0}\"\n", encoding="utf-8"
    )
    (commands / "git").write_text(
        """#!/bin/bash
if [ "$1" = "symbolic-ref" ] && [ "${4:-}" = "HEAD" ]; then
  if [ "${STUB_BRANCH_RC:-0}" != "0" ]; then exit "$STUB_BRANCH_RC"; fi
  printf '%s\n' "${STUB_BRANCH:-agent/review}"
  exit 0
fi
if [ "$1" = "symbolic-ref" ] && [ "${4:-}" = "refs/remotes/origin/HEAD" ]; then
  if [ "${STUB_DEFAULT_RC:-0}" != "0" ]; then exit "$STUB_DEFAULT_RC"; fi
  printf '%s\n' "${STUB_DEFAULT:-origin/main}"
  exit 0
fi
if [ "$1" = "remote" ] && [ "$2" = "get-url" ]; then
  if [ "${STUB_REMOTE_RC:-0}" != "0" ]; then
    echo "fatal: No such remote 'origin'" >&2
    exit "$STUB_REMOTE_RC"
  fi
  printf '%s\n' 'https://example.invalid/repository.git'
  exit 0
fi
if [ "$1" = "push" ]; then
  if [ "${STUB_PUSH_RC:-0}" != "0" ]; then
    echo "fatal: simulated push failure" >&2
    exit "$STUB_PUSH_RC"
  fi
  exit 0
fi
echo "unexpected git invocation: $*" >&2
exit 97
""",
        encoding="utf-8",
    )
    (commands / "python3").chmod(0o755)
    (commands / "git").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{commands}:{env['PATH']}"
    return scripts / "publish.sh", env


def run_publisher(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), "--confirm"],
        cwd=script.parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_publisher_detached_branch_names_exact_recovery(tmp_path: Path) -> None:
    script, env = publisher_fixture(tmp_path)
    env["STUB_BRANCH_RC"] = "1"
    result = run_publisher(script, env)
    assert result.returncode == 2
    assert "checkout is detached" in result.stderr
    assert "git switch agent/<review-name>" in result.stderr
    assert "scripts/publish.sh --confirm" in result.stderr


def test_publisher_refuses_default_with_exact_branch_command(tmp_path: Path) -> None:
    script, env = publisher_fixture(tmp_path)
    env["STUB_BRANCH"] = "main"
    result = run_publisher(script, env)
    assert result.returncode == 2
    assert "Refusing to push the default branch 'main'" in result.stderr
    assert "git switch -c agent/<review-name>" in result.stderr


def test_publisher_missing_remote_preserves_raw_failure(tmp_path: Path) -> None:
    script, env = publisher_fixture(tmp_path)
    env["STUB_REMOTE_RC"] = "2"
    result = run_publisher(script, env)
    assert result.returncode == 2
    assert "fatal: No such remote 'origin'" in result.stderr
    assert "git remote add origin <repository-url>" in result.stderr
    assert "git remote -v" in result.stderr


def test_publisher_push_failure_preserves_raw_failure_and_rerun(tmp_path: Path) -> None:
    script, env = publisher_fixture(tmp_path)
    env["STUB_PUSH_RC"] = "23"
    result = run_publisher(script, env)
    assert result.returncode == 23
    assert "fatal: simulated push failure" in result.stderr
    assert "Push failed with exit 23" in result.stderr
    assert "scripts/publish.sh --confirm" in result.stderr
