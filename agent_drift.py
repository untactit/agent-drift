#!/usr/bin/env python3
"""agent-drift — find the instruction files your AI agents read, and the copies that disagree.

Coding agents read a file before they act: CLAUDE.md, AGENTS.md, .cursor/rules/*.mdc,
.github/copilot-instructions.md, and so on. On a team those files get copied — into other
repositories, onto other laptops, into a personal scratch directory. Copies get edited.
Nothing raises an error when they diverge; the agent just behaves differently over there.

This script finds every such file under the paths you give it, groups the ones that play the
same role, and tells you which groups have more than one distinct version.

    python3 agent_drift.py ~/work ~/side-projects
    python3 agent_drift.py . --json
    python3 agent_drift.py . --fail-on-drift        # for CI

No dependencies. Python 3.8+. Reads only; never writes to the files it scans.

MIT licensed. Written by the team building untactit (https://untactit.com).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

__version__ = "0.1.0"

# Tool -> glob patterns, relative to any directory in the scanned tree.
# Sources: each vendor's own documentation for where it looks for instructions.
PATTERNS = [
    ("claude-code", "CLAUDE.md"),
    ("claude-code", "CLAUDE.local.md"),
    ("claude-code", ".claude/skills/*/SKILL.md"),
    ("claude-code", ".claude/agents/*.md"),
    ("claude-code", ".claude/commands/*.md"),
    ("codex", "AGENTS.md"),
    ("cursor", ".cursorrules"),
    ("cursor", ".cursor/rules/*.mdc"),
    ("copilot", ".github/copilot-instructions.md"),
    ("copilot", ".github/instructions/*.instructions.md"),
    ("gemini", "GEMINI.md"),
    ("windsurf", ".windsurfrules"),
    ("windsurf", ".windsurf/rules/*.md"),
    ("cline", ".clinerules"),
    ("cline", ".clinerules/*.md"),
    ("aider", "CONVENTIONS.md"),
    ("continue", ".continuerules"),
    ("zed", ".rules"),
]

# Directories that are never worth walking into.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".terraform", ".gradle", ".idea", ".vscode-test", "Pods",
}

MAX_BYTES = 2 * 1024 * 1024  # instruction files are prose; anything larger is not one


# ── normalisation ────────────────────────────────────────────────────────────
# Two copies that differ only in trailing whitespace or line endings are not drift.
# Two copies that differ in a sentence are. Normalise the former away so the report
# only contains differences a human would care about.

_WS = re.compile(r"[ \t]+")
_BLANK = re.compile(r"\n{3,}")


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_WS.sub(" ", line).rstrip() for line in text.split("\n"))
    text = _BLANK.sub("\n\n", text)
    return text.strip()


def digest(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()[:12]


# ── discovery ────────────────────────────────────────────────────────────────

def _matches(rel_path: str, pattern: str) -> bool:
    """fnmatch, but '*' must not cross a directory boundary."""
    if "/" in pattern:
        parts_p = pattern.split("/")
        parts_r = rel_path.split("/")
        if len(parts_p) != len(parts_r):
            return False
        return all(fnmatch.fnmatch(r, p) for r, p in zip(parts_r, parts_p))
    return fnmatch.fnmatch(rel_path, pattern)


def scan(roots, follow_symlinks=False):
    """Yield (tool, pattern, absolute_path) for every instruction file found."""
    seen = set()
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.exists(root):
            print("skipped (does not exist): %s" % root, file=sys.stderr)
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                for tool, pattern in PATTERNS:
                    depth = pattern.count("/")
                    rel = os.path.join(*full.split(os.sep)[-(depth + 1):]) if depth else fn
                    rel = rel.replace(os.sep, "/")
                    if not _matches(rel, pattern):
                        continue
                    real = os.path.realpath(full)
                    if real in seen:
                        break
                    seen.add(real)
                    yield tool, pattern, full
                    break


def read(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# ── grouping ─────────────────────────────────────────────────────────────────
# Naive grouping by filename is wrong. Two CLAUDE.md files belonging to unrelated
# projects are not drift; they were never meant to match. What a person actually
# wants to know is: "these files are obviously the same document, and some copies
# have diverged." So group by content similarity, not by path.
#
# Identical content (after normalisation) is a duplicate.
# Highly similar but not identical is drift.

SHINGLE = 5


def tokens(text, k=SHINGLE):
    """Set of k-word shingles.

    A plain bag of words is not enough: two unrelated instruction files written for the
    same tool share most of their vocabulary and would look like the same document.
    Consecutive phrases do not collide that way - they only match when the wording
    actually matches, which is what "the same document, edited" looks like.
    """
    words = re.findall(r"[A-Za-z0-9_]+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a) + len(b) - inter)


def role_hint(tool, pattern, path):
    """Human-readable label for what kind of file this is."""
    if "*" not in pattern:
        return "%s:%s" % (tool, pattern)
    parts = path.replace(os.sep, "/").split("/")
    return "%s:%s" % (tool, "/".join(parts[-pattern.count("/") - 1:]))


def collect(roots, follow_symlinks=False, exclude=()):
    files = []
    for tool, pattern, path in scan(roots, follow_symlinks):
        if any(fnmatch.fnmatch(path, pat) for pat in exclude):
            continue
        text = read(path)
        if text is None or not text.strip():
            continue
        norm = normalise(text)
        files.append({
            "path": path, "tool": tool, "digest": digest(text),
            "bytes": len(text.encode("utf-8")), "lines": text.count("\n") + 1,
            "role": role_hint(tool, pattern, path), "_tokens": tokens(norm),
        })
    return files


def cluster(files, threshold=0.5, max_pairs=400000):
    """Group files that are the same document.

    Single-linkage would chain unrelated documents together: A resembles B, B resembles C,
    and C ends up in a cluster with A even though they share nothing. So each cluster keeps
    a representative - its largest member - and a file joins only if it resembles that
    representative directly. Clusters therefore stay coherent regardless of scan order.

    Only files from the same tool are compared, largest first.
    """
    by_tool = defaultdict(list)
    for f in files:
        by_tool[f["tool"]].append(f)

    clusters, pairs, truncated = [], 0, False
    for _, group in by_tool.items():
        group.sort(key=lambda f: -len(f["_tokens"]))
        reps = []                              # [(representative, [members])]
        for f in group:
            placed = False
            for rep, members in reps:
                if pairs >= max_pairs:
                    truncated = True
                    break
                pairs += 1
                if f["digest"] == rep["digest"] or jaccard(f["_tokens"], rep["_tokens"]) >= threshold:
                    members.append(f)
                    placed = True
                    break
            if not placed:
                reps.append((f, [f]))
        clusters.extend(members for _, members in reps)
    return clusters, truncated


def analyse(files, threshold=0.5):
    clusters, truncated = cluster(files, threshold)
    report = {"files": len(files), "clusters": len(clusters), "truncated": truncated,
              "drifted": [], "duplicated": [], "unique": 0}
    for group in clusters:
        versions = defaultdict(list)
        for f in group:
            versions[f["digest"]].append(f)
        label = sorted(set(f["role"] for f in group))
        if len(group) == 1:
            report["unique"] += 1
        elif len(versions) == 1:
            report["duplicated"].append({
                "label": label, "copies": len(group), "digest": group[0]["digest"],
                "paths": sorted(f["path"] for f in group)})
        else:
            report["drifted"].append({
                "label": label, "copies": len(group), "versions": [
                    {"digest": d, "count": len(v), "lines": v[0]["lines"],
                     "paths": sorted(x["path"] for x in v)}
                    for d, v in sorted(versions.items(), key=lambda kv: -len(kv[1]))]})
    report["drifted"].sort(key=lambda x: -x["copies"])
    report["duplicated"].sort(key=lambda x: -x["copies"])
    return report


# ── output ───────────────────────────────────────────────────────────────────

def shorten(path, home=None):
    home = home or os.path.expanduser("~")
    return path.replace(home, "~", 1) if path.startswith(home) else path


def render(report, verbose=False):
    out = []
    d, dup = report["drifted"], report["duplicated"]
    out.append("Scanned %d instruction files." % report["files"])
    if report["truncated"]:
        out.append("(Comparison was capped; run on a narrower path for a complete result.)")
    out.append("")

    if not d:
        out.append("No drift found.")
        if dup:
            out.append("%d document%s exist%s in more than one place, but every copy is identical."
                       % (len(dup), "" if len(dup) == 1 else "s", "s" if len(dup) == 1 else ""))
        return "\n".join(out)

    total = sum(len(x["versions"]) for x in d)
    out.append("DRIFT: %d document%s, %d distinct versions between them.\n"
               % (len(d), "" if len(d) == 1 else "s", total))

    for item in d:
        out.append("  %s" % " / ".join(item["label"][:3]))
        out.append("    %d copies, %d versions" % (item["copies"], len(item["versions"])))
        for v in item["versions"]:
            out.append("      %s  %d file%s, %d lines"
                       % (v["digest"], v["count"], "" if v["count"] == 1 else "s", v["lines"]))
            shown = v["paths"] if verbose else v["paths"][:2]
            for path in shown:
                out.append("        %s" % shorten(path))
            if len(v["paths"]) > len(shown):
                out.append("        ... %d more (--verbose)" % (len(v["paths"]) - len(shown)))
        out.append("")

    if dup:
        out.append("%d other document%s appear in several places and are identical everywhere."
                   % (len(dup), "" if len(dup) == 1 else "s"))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agent-drift",
        description="Find AI agent instruction files and the copies that disagree.",
        epilog="Reads only. Never modifies the files it scans.")
    ap.add_argument("paths", nargs="*", default=["."],
                    help="directories to scan (default: current directory)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--verbose", "-v", action="store_true", help="list every path")
    ap.add_argument("--fail-on-drift", action="store_true",
                    help="exit 1 when drift is found, for use in CI")
    ap.add_argument("--threshold", type=float, default=0.5, metavar="N",
                    help="similarity above which two files count as the same document "
                         "(0-1, default 0.5)")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="skip paths matching this glob; repeatable")
    ap.add_argument("--follow-symlinks", action="store_true",
                    help="descend into symlinked directories")
    ap.add_argument("--version", action="version", version="agent-drift %s" % __version__)
    args = ap.parse_args(argv)

    files = collect(args.paths or ["."], args.follow_symlinks, tuple(args.exclude))
    report = analyse(files, args.threshold)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render(report, args.verbose))

    return 1 if (args.fail_on_drift and report["drifted"]) else 0


if __name__ == "__main__":
    sys.exit(main())

