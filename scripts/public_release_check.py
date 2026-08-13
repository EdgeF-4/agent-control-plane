#!/usr/bin/env python3
"""Fail closed when public release material contains private indicators.

The checker emits locations and rule names, never matched values or context.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    private: bool = False


SKIP_PATHS = {"scripts/public_release_check.py"}


class ActionableArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage()
        self.exit(
            2,
            "error: "
            f"{message}. Next: provide the deny file and rerun: "
            "python3 scripts/public_release_check.py --deny-file "
            ".public-release-deny-patterns\n",
        )


def run_git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raw = decode(result.stderr).strip() or "git returned no diagnostic"
        command = shlex.join(["git", "-C", str(repo), *args])
        raise RuntimeError(f"{command} failed with exit {result.returncode}: {raw}")
    return result.stdout


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def built_in_rules() -> list[Rule]:
    return [
        Rule(
            "automated-authorship-framing",
            re.compile(
                r"\b(?:AI[- ]generated|agent[- ]built|built (?:by|with) (?:an? )?"
                r"(?:AI|agent)|generated (?:by|with) (?:AI|an? agent)|as an AI|"
                r"language model|vibe[- ]cod(?:e|ed|ing))\b",
                re.I,
            ),
        ),
        Rule(
            "private-key-block",
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.I),
        ),
        Rule(
            "assigned-high-entropy-secret",
            re.compile(
                r"(?im)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
                r"client[_-]?secret|password|passwd|secret|private[_-]?key)\b"
                r"\s*[:=]\s*[\"']?(?!\$\{|\$[A-Z_]|<|your[_ -]|example|dummy|"
                r"test|change|replace|none|null|os\.environ|getenv|process\.env)"
                r"[A-Za-z0-9_./+=:@-]{24,}"
            ),
        ),
        Rule(
            "user-home-path",
            re.compile(r"(?<![A-Za-z0-9_])/(?:root|home/[^/\s]+)(?:/[A-Za-z0-9_.@+~-]+)+"),
        ),
        Rule(
            "private-network-address",
            re.compile(
                r"(?<![0-9.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
                r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![0-9.])"
            ),
        ),
        Rule(
            "internal-domain",
            re.compile(
                r"\b(?:[a-z0-9-]+\.)+(?:internal|intra|lan|local)\b(?!\.[a-z0-9])",
                re.I,
            ),
        ),
        Rule(
            "public-personal-email",
            re.compile(
                r"\b[A-Z0-9._%+-]+@"
                r"(?!users\.noreply\.github\.com\b)"
                r"(?![A-Z0-9.-]+\.(?:test|example|invalid|localhost)\b)"
                r"[A-Z0-9.-]+\.[A-Z]{2,}\b",
                re.I,
            ),
        ),
    ]


def load_private_rules(path: Path) -> list[Rule]:
    rules: list[Rule] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        try:
            compiled = re.compile(value, re.I)
        except re.error as error:
            raise ValueError(f"invalid private deny regex on line {number}: {error}") from error
        rules.append(Rule(f"private-deny-line-{number}", compiled, private=True))
    if not rules:
        raise ValueError("the private deny file contains no active patterns")
    return rules


def scan_text(
    findings: set[tuple[str, str, int, str]],
    source: str,
    location: str,
    data: str,
    rules: list[Rule],
) -> None:
    location_is_private = any(
        candidate.private and candidate.pattern.search(location) for candidate in rules
    )
    for rule in rules:
        for match in rule.pattern.finditer(data):
            line = data.count("\n", 0, match.start()) + 1
            safe_location = "[private location]" if location_is_private else location
            findings.add((source, safe_location, line, rule.name))


def scan_path_and_data(
    findings: set[tuple[str, str, int, str]],
    source: str,
    path: str,
    data: bytes,
    rules: list[Rule],
) -> None:
    if path in SKIP_PATHS:
        return
    scan_text(findings, f"{source}-path", path, path, rules)
    scan_text(findings, source, path, decode(data), rules)


def scan_worktree(repo: Path, findings: set[tuple[str, str, int, str]], rules: list[Rule]) -> None:
    paths = decode(
        run_git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    ).split("\x00")
    for relative in filter(None, paths):
        path = repo / relative
        if path.is_file() or path.is_symlink():
            data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
            scan_path_and_data(findings, "working-tree", relative, data, rules)


def scan_history(repo: Path, findings: set[tuple[str, str, int, str]], rules: list[Rule]) -> None:
    blob_cache: dict[str, bytes | None] = {}
    scanned_content: set[str] = set()
    for row in decode(run_git(repo, "rev-list", "--objects", "--all")).splitlines():
        object_id, _, path = row.partition(" ")
        if not object_id:
            continue
        if object_id not in blob_cache:
            if decode(run_git(repo, "cat-file", "-t", object_id)).strip() == "blob":
                blob_cache[object_id] = run_git(repo, "cat-file", "blob", object_id)
            else:
                blob_cache[object_id] = None
        data = blob_cache[object_id]
        if data is None:
            continue
        location = path or f"object:{object_id[:12]}"
        scan_text(findings, "history-path", location, location, rules)
        if object_id not in scanned_content:
            scanned_content.add(object_id)
            if path not in SKIP_PATHS:
                scan_text(findings, "history-blob", location, decode(data), rules)


def scan_metadata(repo: Path, findings: set[tuple[str, str, int, str]], rules: list[Rule]) -> None:
    log = decode(
        run_git(
            repo,
            "log",
            "--all",
            "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%s%x00%b%x1e",
        )
    )
    for record in log.split("\x1e"):
        fields = record.strip("\n").split("\x00", 6)
        if len(fields) != 7:
            continue
        commit_id, *content = fields
        scan_text(
            findings,
            "commit-metadata",
            f"commit:{commit_id[:12]}",
            "\n".join(content),
            rules,
        )
    refs = decode(
        run_git(
            repo,
            "for-each-ref",
            "--format=%(refname)%00%(objectname)%00%(contents)%1e",
            "refs/heads",
            "refs/remotes",
            "refs/tags",
        )
    )
    for record in refs.split("\x1e"):
        fields = record.strip("\n").split("\x00", 2)
        if len(fields) != 3:
            continue
        refname, object_id, content = fields
        scan_text(findings, "git-ref", refname, refname + "\n" + content, rules)


def main() -> int:
    parser = ActionableArgumentParser()
    parser.add_argument("--deny-file", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    rerun = shlex.join(
        ["python3", "scripts/public_release_check.py", "--deny-file", str(args.deny_file)]
    )

    if not args.deny_file.is_file():
        print("BLOCK configuration: private deny file is missing")
        print(
            "Next: copy .public-release-deny-patterns.example to "
            f"{args.deny_file}, add the owner-approved private patterns, then rerun: {rerun}"
        )
        return 2
    try:
        rules = built_in_rules() + load_private_rules(args.deny_file)
    except ValueError as error:
        print(f"BLOCK configuration: {error}")
        print(
            f"Next: correct {args.deny_file} at the reported line or add at least "
            f"one active pattern, then rerun: {rerun}"
        )
        return 2
    except OSError as error:
        print(f"BLOCK configuration: cannot read {args.deny_file}: {error}")
        print(
            f"Next: correct the file path or read permission, then rerun: {rerun}"
        )
        return 2

    findings: set[tuple[str, str, int, str]] = set()
    try:
        scan_worktree(repo, findings, rules)
        scan_history(repo, findings, rules)
        scan_metadata(repo, findings, rules)
    except (OSError, RuntimeError) as error:
        print(f"BLOCK scan: {error}")
        print(
            "Next: correct the reported repository, permission, or object error, "
            f"then rerun: {rerun}"
        )
        return 2
    for source, location, line, rule in sorted(findings):
        print(f"BLOCK {source}:{location}:{line}: {rule}")
    if findings:
        print(f"Release blocked: {len(findings)} finding(s). Matched values were not printed.")
        print(
            "Next: correct every BLOCK location in the current files. If a finding "
            "is in reachable history, stop and obtain owner approval before any "
            f"history rewrite. Then rerun: {rerun}"
        )
        return 1
    print("Release check passed across tracked files and all reachable Git history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
