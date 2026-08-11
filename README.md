# agent-drift

Find the instruction files your AI agents read, and the copies that no longer agree.

```
$ python3 agent_drift.py ~/work

Scanned 47 instruction files.

DRIFT: 2 documents, 5 distinct versions between them.

  claude-code:CLAUDE.md
    6 copies, 3 versions
      9b01aeaa204d  3 files, 406 lines
        ~/work/api/CLAUDE.md
        ~/work/web/CLAUDE.md
      2dc3c616c279  2 files, 411 lines
        ~/work/infra/CLAUDE.md
      ...
```

## Why

Coding agents read a file before they act — `CLAUDE.md`, `AGENTS.md`,
`.cursor/rules/*.mdc`, `.github/copilot-instructions.md`, and their equivalents in
other tools. On a team those files get copied: into another repository, onto another
laptop, into someone's scratch directory. Then a copy gets edited.

Nothing raises an error when the copies diverge. The agent simply behaves differently
over there — different defaults, a rule someone removed, a convention that only half the
team's agents know about. It surfaces as "it works on my machine" with no stack trace to
follow.

This script finds those files and tells you which ones have drifted apart.

## Install

There is nothing to install. One file, no dependencies, Python 3.8+.

```bash
curl -O https://raw.githubusercontent.com/untactit/agent-drift/main/agent_drift.py
python3 agent_drift.py .
```

## Usage

```bash
python3 agent_drift.py                       # current directory
python3 agent_drift.py ~/work ~/side         # several roots
python3 agent_drift.py . --json              # machine-readable
python3 agent_drift.py . --verbose           # every path, not just the first few
python3 agent_drift.py . --exclude '*/vendor/*' --exclude '*/backup/*'
python3 agent_drift.py . --fail-on-drift     # exit 1 when drift exists
```

`--fail-on-drift` is meant for CI. Add it to a scheduled job and the build turns red the
day two copies of your team's instructions stop matching.

## What it looks for

| Tool | Paths |
| --- | --- |
| Claude Code | `CLAUDE.md`, `CLAUDE.local.md`, `.claude/skills/*/SKILL.md`, `.claude/agents/*.md`, `.claude/commands/*.md` |
| Codex and the AGENTS.md convention | `AGENTS.md` |
| Cursor | `.cursorrules`, `.cursor/rules/*.mdc` |
| GitHub Copilot | `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md` |
| Gemini | `GEMINI.md` |
| Windsurf | `.windsurfrules`, `.windsurf/rules/*.md` |
| Cline | `.clinerules`, `.clinerules/*.md` |
| Aider | `CONVENTIONS.md` |
| Continue | `.continuerules` |
| Zed | `.rules` |

Missing one? Open an issue or a pull request — the list is a constant at the top of the file.

## How it decides two files are "the same document"

Grouping by filename would be wrong. Two `CLAUDE.md` files in unrelated projects were never
meant to match, and reporting them as drift is noise.

So the comparison is by content:

1. **Normalise** — line endings, trailing whitespace and runs of blank lines are collapsed.
   A copy that differs only in whitespace is not drift.
2. **Shingle** — each file becomes the set of its five-word phrases. A plain bag of words
   would group unrelated files written for the same tool, because they share vocabulary.
   Consecutive phrases only match when the wording actually matches.
3. **Cluster** — files join a cluster when their phrase overlap with that cluster's largest
   member reaches the threshold (Jaccard ≥ 0.5, tunable with `--threshold`). Comparing
   against a representative rather than any member keeps unrelated documents from chaining
   into one cluster through a series of weak links.
4. **Report** — a cluster with one distinct version is a duplicate. A cluster with several
   distinct versions is drift.

Only files from the same tool are compared, and comparison is capped so that a large tree
does not turn into a quadratic scan.

## Guarantees

- **Read only.** The script opens files for reading and never writes to them.
- **No network.** Nothing leaves your machine.
- **No dependencies.** Standard library only.

## Output

`--json` emits the full report, including every path, so you can post-process it:

```json
{
  "files": 47,
  "clusters": 31,
  "drifted": [
    {
      "label": ["claude-code:CLAUDE.md"],
      "copies": 6,
      "versions": [
        { "digest": "9b01aeaa204d", "count": 3, "lines": 406, "paths": ["..."] }
      ]
    }
  ],
  "duplicated": [],
  "unique": 29
}
```

## After you find drift

Finding it is the easy half. Keeping it from coming back means removing the reason copies
exist at all: a single reviewed source that reaches every machine without anyone pasting
anything.

That is the problem [untactit](https://untactit.com) is built for. This script is useful on
its own and carries no dependency on it.

## Licence

MIT. See [LICENSE](LICENSE).

## Related

[agent-fanout](https://github.com/untactit/agent-fanout) — the other half: keep one `AGENTS.md` and generate the file each tool reads, so the copies never diverge to begin with.
