# crypto-spread

## Rules of record

- `AGENTS.md` — canonical project rules: stack, commands, repo structure, gotchas.
  Load it first; it wins on conflict with this file.
- `CONSTRAINTS.md` — the active issue's quality & architectural gates.
- `SPEC.md` — the active issue's specification, not the project architecture.

  Both are **per-issue working files**, rewritten by each issue's Station II
  and binding only while that issue's branch is live (`docs/git-workflow.md`
  §5). They currently carry issue #191, which is merged and closed — so nothing
  in them binds until the next issue claims them. Read the title line before
  trusting the contents.

This file only carries the Claude-facing additions (gbrain routing below); keep
project facts in `AGENTS.md` so the two do not drift.

## GBrain search guidance

This repo is indexed in gbrain as source `crypto-spread`, pinned by `.gbrain-source` in
the repo root, so the commands below route here without a `--source` flag.

**Prefer gbrain over Grep for structural questions** — who calls a symbol, where
it is defined, what it references, what it calls. One query beats opening every
file:

```bash
gbrain code-def <symbol>       # where it is defined
gbrain code-refs <symbol>      # every reference
gbrain code-callers <symbol>   # who calls it
gbrain code-callees <symbol>   # what it calls
gbrain query "<question>"      # semantic search over this repo
```

**Read `status` before trusting an empty result.** `count: 0` means "nothing
found" only when `status` is `ready`. `not_built` or `indexing` means the call
graph is still being built and the empty list proves nothing — fall back to Grep
and say that is what you did.

**Use Grep instead** for literal text, config values, comments, and anything
added since the last sync. The index refreshes on every commit (a `post-commit`
hook) and nightly at 03:00; uncommitted work in progress is not in it. To
refresh now:

```bash
gbrain sync --source crypto-spread --strategy code
```
