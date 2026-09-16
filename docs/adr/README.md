# Architecture Decision Records

Decisions about the *shape* of the system — why there are two engines, which settings are
settings at all, how parameters are classed. Each one records the alternatives that were
rejected and why, which is the part that is otherwise lost.

**Decisions about what the strategy *does*** — triggers, thresholds, what happens on a fill —
live in [`../engine-decision-rules.md`](../engine-decision-rules.md), not here.

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-parity-harness-over-shared-module.md) | A parity harness, not a shared decision module | accepted | 2026-09-16 |
| [0002](0002-single-hard-coded-fill-rule.md) | One hard-coded fill rule; `fill_model` is not a knob | accepted | 2026-09-16 |
| [0003](0003-structural-limits-separate-from-tuning-knobs.md) | Structural limits are a separate parameter class from tuning knobs | accepted | 2026-09-16 |

## Writing one

Copy [`template.md`](template.md). Number it next in sequence. Keep it readable in two minutes:
if the Context section runs past ten lines, it is too long.

An ADR without rejected alternatives is not an ADR — "we just picked it" is not a rationale, and
the next person needs to know what was already considered.

## Status lifecycle

```
proposed -> accepted -> [deprecated | superseded by ADR-NNNN]
```

A superseded ADR always links its replacement. Nothing is deleted.
