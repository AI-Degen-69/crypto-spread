# tasks/plan.md — Issue #214: the engine parity harness

- **Issue:** https://github.com/AI-Degen-69/crypto-spread/issues/214
- **Branch:** `add/214-engine-parity-harness`
- **Size tier:** **Standard** — one new test module, no production code touched.
  (It was scoped Large before the rule work was split out into #224-#233.)
- **Task type:** `Test infrastructure`
- **Stack:** Python 3, pytest. No new dependencies.
- **Verification:** targeted `pytest`, per `CONSTRAINTS.md` §1. No UI surface.
- **Definition of the rules being enforced:** `docs/engine-decision-rules.md`

---

## Locked interfaces (`tests/test_engine_parity.py`)

```python
def snaps_to_polls(snaps: list[dict]) -> list[tuple[float, dict]]:
    """Backtest snaps -> (now, poll_data) for LiveTraderEngine._update_market_strategy."""

def live_outcome(snaps: list[dict], params: BacktestParams) -> dict:
    """Run the live decision path in paper mode; return the SPEC §4 surface."""

def backtest_outcome(snaps: list[dict], params: BacktestParams) -> dict:
    """Run _simulate_window; return the same surface from WindowResult."""

def assert_parity(snaps: list[dict], params: BacktestParams) -> None:
    """Fail with the first disagreeing field and the tick it disagreed on."""
```

---

## Tasks

### T1 — `[Test/Harness]` The snap-to-poll adapter
- **Files:** `tests/test_engine_parity.py`
- **Build:** `snaps_to_polls`. A snap carries `cid`, `series`, `slug`, `duration`, `ts`,
  `start_ts`, `up_book`, `down_book`, `mid`, `tape_delta`. Live `poll_data` needs
  `market.{conditionId,slug,up_token,down_token,start_ts,end_ts}` plus `up_book`/`down_book`.
  Follow the existing fixtures in `tests/test_entry_timeout.py` (`_poll`, `_make_snap`) so one
  snap shape feeds both engines.
- **Verify:** `python -m pytest tests/test_engine_parity.py -q`

### T2 — `[Test/Harness]` Drive the live engine headlessly
- **Files:** `tests/test_engine_parity.py`
- **Build:** `live_outcome`. `LiveTraderEngine(load_persisted=False)`, `mode="paper"`,
  `is_running=True`, CLOB client mocked out — the pattern already used by
  `tests/test_entry_timeout.py:_late_start_engine`. Configure from the `BacktestParams` under
  test via `update_config`, feed each poll at its own `now`, then read the surface off
  `mstate`.
- **Verify:** `python -m pytest tests/test_engine_parity.py -q`

### T3 — `[Test/Harness]` The comparison
- **Files:** `tests/test_engine_parity.py`
- **Build:** `backtest_outcome` (project `WindowResult` onto the same keys) and
  `assert_parity`. The failure message must name the field, both values, and the tick index —
  a bare `assert a == b` on two dicts is not good enough to debug a 200-tick window.
- **Verify:** deliberately break one engine's input and confirm the message is readable.

### T4 — `[Test/Scenarios]` Seed the settled cases
- **Files:** `tests/test_engine_parity.py`
- **Build:** five scenarios, each asserting parity rather than a hard-coded price:
  1. balanced open, both legs fill, pair merges;
  2. opening quote anchored to the real mid (#206);
  3. pair-cost cap does not block quoting (#204);
  4. unpriceable leg skips the window (#207);
  5. stop measured from the entry price (#209).
- **Verify:** `python -m pytest tests/test_engine_parity.py -q`, under 5s.

### T5 — `[Docs]` Point the rules of record at both documents
- **Files:** `AGENTS.md`
- **Build:** name `docs/engine-decision-rules.md` as the definition of the strategy's decision
  rules, and `tests/test_engine_parity.py` as the gate that holds both engines to it. State the
  rule: a change to either engine's decision logic adds or updates a parity scenario in the
  same PR.
- **Verify:** `python -m pytest tests/test_docstrings.py -q`

---

## Commit plan

1. `add(tests): snap-to-poll adapter for the parity harness (#214)`
2. `add(tests): drive the live decision path headlessly in paper mode (#214)`
3. `add(tests): parity comparison with a readable failure diff (#214)`
4. `add(tests): seed parity scenarios for #204/#206/#207/#209 (#214)`
5. `docs(agents): name the decision-rules document and the parity gate (#214)`

---

## Follow-on issues from the 2026-09-16 rules session

The rules themselves were redefined with the operator and split out. Suggested order — the
invariants first, because every other rule reads the clock:

| # | issue | closes |
|---|---|---|
| #224 | Invariants: no invented numbers, one window clock | |
| #225 | Entry anchor: repriced until placed, two-sided mid only | |
| #226 | One fill rule; remove the `fill_model` knob | explains #205 |
| #227 | `max_pair_cost` caps the chase only | |
| #228 | `quote_range` replaces `entry_band` and `adverse_open` | #213, half of #208 |
| #229 | One dead zone at the end of the window | #211, rest of #208 |
| #230 | One stop threshold | |
| #231 | Time-proportional leg chase | #210 |
| #232 | `fresh_start` — no memory inside a window | #212 |
| #233 | Structural limits separated from tuning knobs | |

Open measurements, deliberately not decided by argument:

| # | question |
|---|---|
| #221 | Does a running backtest delay the live tick? |
| #222 | Dead zone: percent of window or fixed seconds? |
| #223 | Unpaired leg at expiry: close or hold? |
