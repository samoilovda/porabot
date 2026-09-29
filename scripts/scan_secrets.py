#!/usr/bin/env python3
"""Scan a git repo's *full history* (all local branches/remotes) for leaked
secrets — the way the actual porabot BOT_TOKEN leak (README.md, commit
9f524f8, 2026-03-28) got in and sat there for ~4 months.

Why this exists instead of just running gitleaks: gitleaks' default
ruleset does NOT catch a bare Telegram bot token (`\\d{8,10}:[A-Za-z0-9_-]{35}`)
sitting in a markdown table — verified by running it against porabot's own
history, which came back clean. `--baseline`-free, dependency-free (stdlib
only) so it needs no install beyond Python 3, and its rules are tuned to
what actually leaked here plus the other common secret shapes.

It checks three things across every commit reachable from ANY ref (not
just HEAD — a secret added on a branch that's since been rebased away or a
value that was later deleted from the file, as happened here, still shows
up in `git log --all -p`):

  1. Known secret *formats* (Telegram bot token, AWS keys, GitHub PAT,
     Slack/OpenAI/Stripe/Google keys, JWTs, PEM private key headers) in any
     added line.
  2. `KEY=value` / `key = "value"` assignments — quoted or bare .env style —
     where the key name looks secret-ish (TOKEN, API_KEY, SECRET,
     PASSWORD, ...) and the value isn't an obvious placeholder.
  3. Filenames that look like committed secrets (.env, *.pem, id_rsa,
     credentials.json, ...) — both in each branch tip's current tree AND
     anywhere in history (a file added then later deleted still counts).

Every match is redacted to `first6...last4` before being printed — this
tool reports *that and where* something leaked, never the secret itself.

Usage:
    python3 scripts/scan_secrets.py                  # full history, all refs (slow, thorough)
    python3 scripts/scan_secrets.py /path/to/other/repo
    python3 scripts/scan_secrets.py --json            # machine-readable
    python3 scripts/scan_secrets.py --no-history      # only files at HEAD, no git log crawl
                                                       # (fast; misses anything removed from
                                                       # the tree since it was committed)

Exit code is 1 if anything was found (so it's usable as a CI gate), 0 if
clean. False positives happen (test fixtures, variable names like
`api_key = settings.api_key`) — read the `preview` field before panicking.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

KNOWN_FORMAT_PATTERNS = {
    "telegram-bot-token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "aws-access-key-id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "aws-secret-like": re.compile(
        r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"
    ),
    "github-pat": re.compile(
        r"\bghp_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    ),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "openai-key": re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{20,}\b"),
    "stripe-live-key": re.compile(r"\b(sk|pk)_live_[A-Za-z0-9]{16,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "generic-jwt": re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    ),
    "private-key-header": re.compile(
        r"-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"
    ),
    "quoted-secret-assignment": re.compile(
        r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token"
        r"|client[_-]?secret|password|db[_-]?pass)\b\s*[:=]\s*"
        r"['\"][^'\"\s]{12,}['\"]"
    ),
}

UNQUOTED_ASSIGN = re.compile(
    r"(?i)^\s*([A-Z0-9_]*(TOKEN|API_KEY|SECRET|PASSWORD|ACCESS_KEY"
    r"|PRIVATE_KEY|DSN|CONN(ECTION)?_STRING)\w*)\s*=\s*(\S{10,})\s*$"
)

PLACEHOLDER_HINTS = re.compile(
    r"(?i)your[_-]?|example|placeholder|xxxx|changeme|<.*>|\{\{.*\}\}"
    r"|dummy|fake|test[_-]?key|sample|replace_with|000000|1234567890"
    r"|^\$\{|^%|user:password"
    # aiogram's own canonical example bot token (used verbatim across its
    # docs and this repo's tests) and the bare "test-token" conftest.py
    # injects for every test run — both look secret-shaped but never are.
    r"|123456:ABC-DEF|^test-token$"
)

SUSPICIOUS_FILENAMES = re.compile(
    r"(^|/)(\.env(\..+)?|.*\.pem|.*\.key|id_rsa|id_dsa|id_ecdsa|id_ed25519"
    r"|.*service[_-]?account.*\.json|credentials\.json|\.npmrc|\.pypirc"
    r"|.*\.p12|.*\.pfx)$",
    re.IGNORECASE,
)
SAFE_ENV_SUFFIX = re.compile(
    r"\.env\.(example|sample|template|dist)$", re.IGNORECASE
)


def run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, errors="replace"
    )


def mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 10:
        return "(redacted-short)"
    return f"{value[:6]}...{value[-4:]}"


def scan_filenames(path: str, no_history: bool) -> list[dict]:
    findings = []
    seen = set()

    def check_tree(names: list[str], ref: str):
        for name in names:
            if SAFE_ENV_SUFFIX.search(name) or not SUSPICIOUS_FILENAMES.search(name):
                continue
            key = (name, ref)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                {"type": "suspicious-filename", "file": name, "ref": ref}
            )

    r = run(["git", "ls-tree", "-r", "--name-only", "--full-tree", "HEAD"], path)
    check_tree(r.stdout.splitlines(), "HEAD")

    if no_history:
        return findings

    r = run(["git", "branch", "-r", "--format=%(refname:short)"], path)
    for branch in (b.strip() for b in r.stdout.splitlines() if b.strip()):
        rr = run(["git", "ls-tree", "-r", "--name-only", "--full-tree", branch], path)
        check_tree(rr.stdout.splitlines(), branch)

    # files added then later deleted don't show up in any tip's tree
    r = run(
        ["git", "log", "--all", "--diff-filter=A", "--name-only",
         "--pretty=format:%x01%H"],
        path,
    )
    commit = None
    for line in r.stdout.splitlines():
        if line.startswith("\x01"):
            commit = line[1:]
            continue
        name = line.strip()
        if not name or SAFE_ENV_SUFFIX.search(name):
            continue
        if SUSPICIOUS_FILENAMES.search(name):
            key = (name, "history")
            if key not in seen:
                seen.add(key)
                findings.append(
                    {"type": "suspicious-filename-in-history", "file": name, "commit": commit}
                )
    return findings


_PLACEHOLDER_WINDOW = 20


def _redact_preview(content: str, start: int, end: int) -> str:
    """Replace the matched span with its masked form before truncating, so
    the preview we print/log never contains the raw secret."""
    return (content[:start] + mask(content[start:end]) + content[end:]).strip()[:100]


def _match_content(content: str, current_file: str, extra: dict) -> list[dict]:
    findings = []

    for rule, pattern in KNOWN_FORMAT_PATTERNS.items():
        m = pattern.search(content)
        if not m:
            continue
        start, end = m.span()
        window = content[max(0, start - _PLACEHOLDER_WINDOW):end + _PLACEHOLDER_WINDOW]
        if PLACEHOLDER_HINTS.search(window):
            continue
        findings.append({
            "type": rule, "file": current_file, "match": mask(m.group(0)),
            "preview": _redact_preview(content, start, end), **extra,
        })

    m = UNQUOTED_ASSIGN.match(content)
    if m and len(m.group(4)) >= 12 and "(" not in content and ")" not in content:
        start, end = m.span(4)
        window = content[max(0, start - _PLACEHOLDER_WINDOW):end + _PLACEHOLDER_WINDOW]
        if not PLACEHOLDER_HINTS.search(window):
            findings.append({
                "type": "unquoted-secret-assignment", "file": current_file,
                "key": m.group(1), "match": mask(m.group(4)),
                "preview": _redact_preview(content, start, end), **extra,
            })
    return findings


def scan_content_current(path: str) -> list[dict]:
    """Fast path: only the files currently checked out at HEAD, no git log
    crawl at all. Good enough for a pre-commit hook; misses anything only
    present in history (use the default full scan for that)."""
    findings = []
    r = run(["git", "ls-tree", "-r", "--name-only", "--full-tree", "HEAD"], path)
    for name in r.stdout.splitlines():
        full_path = os.path.join(path, name)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    findings.extend(_match_content(line.rstrip("\n"), name, {"ref": "HEAD"}))
        except OSError:
            continue
    return findings


def scan_content_history(path: str) -> list[dict]:
    """Scans every added line in every commit reachable from any ref (all
    local branches/remotes) — this is what catches a secret that was
    committed and later removed, which a HEAD-only scan would miss."""
    findings = []
    r = run(
        ["git", "log", "--all", "-p", "--no-color", "--unified=0",
         "--pretty=format:%x01COMMIT%x01%H%x01%ai%x01%an"],
        path,
    )
    commit = date = author = current_file = None
    for line in r.stdout.splitlines():
        if line.startswith("\x01COMMIT\x01"):
            _, _, commit, date, author = line.split("\x01")
            continue
        if line.startswith("+++ "):
            current_file = line[6:] if line[4:6] == "b/" else line[4:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        findings.extend(_match_content(
            line[1:], current_file,
            {"commit": commit, "date": date, "author": author},
        ))
    return findings


def scan_repo(path: str, no_history: bool) -> list[dict]:
    if not os.path.isdir(os.path.join(path, ".git")):
        raise SystemExit(f"error: {path!r} is not a git repository (no .git dir)")
    findings = scan_filenames(path, no_history)
    findings += scan_content_current(path) if no_history else scan_content_history(path)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="path to the git repo to scan (default: .)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    parser.add_argument(
        "--no-history", action="store_true",
        help="only scan files as they are at HEAD, skip the full git-log crawl (fast, less thorough)",
    )
    args = parser.parse_args()

    findings = scan_repo(args.path, args.no_history)

    if args.json:
        print(json.dumps(findings, indent=2))
    elif not findings:
        print(f"clean: no suspected secrets in {args.path}")
    else:
        print(f"{len(findings)} finding(s) in {args.path} — verify each before acting, false positives are common:\n")
        for f in findings:
            print(f"[{f['type']}] {f.get('file')}")
            if f.get("commit"):
                print(f"  commit: {f['commit'][:10]}  date: {f.get('date', '')}")
            if f.get("ref"):
                print(f"  ref: {f['ref']}")
            if f.get("match"):
                print(f"  value: {f['match']}")
            if f.get("preview"):
                print(f"  line: {f['preview']}")
            print()

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
