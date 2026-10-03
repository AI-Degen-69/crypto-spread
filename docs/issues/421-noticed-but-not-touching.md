# Noticed-but-not-touching — Issue #421 (Station III)

Spotted while building #421. Station VI resolves rows recorded here; other
stations only append `open` rows.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | `strategy/config.py:MakerConfig` carries a full second set of live-looking defaults (`max_pair_cost = 0.995`, offset, exit thresholds, dead zone) that nothing consumes — `load()` is the only reader, and neither the trading engine nor the backtest engine constructs it. Its `0.995` is what misled #421's own issue body into citing a "live default" that does not exist | III | `strategy/config.py:17,649,776,832` (definition + defaults + sole `load()` caller); zero importers outside the module | open | — |

## Candidates considered and kept without a row

- The HTML fallback `value="99"` on `#cockpitPairCost` (`server/osc_dash.py:6300`)
  stays a literal. It is the pre-hydration value for a payload with no
  `params.max_pair_cost`; removing it leaves the field empty, which
  `validateCentsInput()` rejects and Apply blocks on. Deliberate, not an
  oversight — #421's acceptance criteria keep it.
- `updateCockpitParamsLockUI()` (`server/osc_dash.py:12092-12112`) and
  `validateCockpitInputs()` (`:12597`) already list `cockpitPairCost`. Neither
  needed a change for #421; recorded so the next reader does not re-check them.