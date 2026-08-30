# Research Conclusions — Polymarket 5m/15m SPREAD-2 Bot

> **Scope:** Synthesizes only primary sources owned by this repo and by Polymarket's official venue docs/APIs. Every claim cites its owning source as `file:line` or URL. Secondary write-ups are flagged where they diverge.
>
> **Saved at:** `docs/research-spread-bot-conclusions.md` (repo convention: `docs/` is the designated notes dir per `AGENTS.md:24`; sister-repo hint `strategy/config.py:4` references `research/powerwinner_analysis.md` which does not exist here, so `docs/` was chosen).

---

## 1. Data Model & Collector — How the Numbers Were Produced

### 1.1 Series universe and cadence

- **10 series polled every 1 s:** `btc/eth/bnb/sol/xrp` × `5m (300 s)` + `15m (900 s)` — `scripts/measure_5m_oscillation.py:38-49`.
- **Live market discovery:** `GET https://gamma-api.polymarket.com/events?series_slug=<slug>&closed=false&limit=500` — `scripts/measure_5m_oscillation.py:68`, `strategy/markets.py:96-102`. Candidates filtered to `start_ts <= now < end_ts`, newest `start_ts` wins — `scripts/measure_5m_oscillation.py:84-91`, `strategy/markets.py:104-115`.
- **Venue fields consumed:** `conditionId`, `slug`, `eventStartTime` (= window open, not listing time), `endDate`/`endDateIso`, `clobTokenIds` (JSON string → `[up_token, down_token]`), `orderPriceMinTickSize`, `negRisk` — `strategy/markets.py:59-85`, `scripts/measure_5m_oscillation.py:78-101`.
- **Both CLOB books fetched per poll:** `GET https://clob.polymarket.com/book?token_id=<token>` for UP and DOWN — `scripts/measure_5m_oscillation.py:103-120`, `strategy/markets.py:257-268`.

### 1.2 Pricing primitives measured per snapshot

- **`mid = (best_bid + best_ask)/2` from the UP book only** — `scripts/measure_5m_oscillation.py:194-201`. Fallbacks: `best_bid + 0.005` or `best_ask - 0.005` when one side missing. If both missing, `mid = None` (snapshot still written, window contributes no mid) — `scripts/measure_5m_oscillation.py:194-201`.
- **`touch_pair = up_ask + down_ask`** (cost to cross both sides instantly) — `scripts/measure_5m_oscillation.py:203-205`.
- **`resting_pair = 1.00 - 2*SPREAD_OFFSET = 0.96`** — `scripts/measure_5m_oscillation.py:51-52,207`. `SPREAD_OFFSET = 0.02` — `scripts/measure_5m_oscillation.py:52`. Interpretation: rest `mid - 2¢` on both sides → 4¢ gross if both fill and `mergePositions` — `README.md:22`, `server/osc_dash.py:192`.
- **`queue_up`:** sum of bid sizes at prices `>= round(mid - 0.02, 3)` (depth ahead of a resting UP bid) — `scripts/measure_5m_oscillation.py:210-214`. This is a local estimate, not the CLOB's queue position field.

### 1.3 Output files (gitignored, regenerated)

- `run/oscillation_snapshots.jsonl` — one JSON line per poll per market (96,132 lines at time of audit) — `scripts/measure_5m_oscillation.py:31`.
- `run/oscillation_windows.jsonl` — one line per closed window with `start_mid`, `close_mid`, `min_mid`, `max_mid`, `max_up`, `max_down`, `class`, `touch_pair_median` — `scripts/measure_5m_oscillation.py:246-260`.
- `run/oscillation_summary.json` — aggregated per series (derived, not primary) — `scripts/measure_5m_oscillation.py:143-178`.
- All three are in `.gitignore:6` (`run/`) — missing files mean collector hasn't run, not an error — `AGENTS.md:31`.

### 1.4 Collector reliability caveats (load-bearing)

- Pooled `requests.Session` with `(3.05, 5.0)` connect/read timeouts, `max_retries=0` — failed markets are skipped for that second, not retried — `scripts/measure_5m_oscillation.py:56-58`, `strategy/markets.py:20-30`, `AGENTS.md:32`.
- `strategy/markets.py:52-57` sanitizes slugs via `_SAFE_SLUG_RE` before embedding; `parse_book` tolerates malformed rows (counted in `malformed`) but raises `ValueError` on structural mismatch — `strategy/markets.py:207-254`, `AGENTS.md:33`.
- `start_mid` is the first sampled mid, not the true 0.50 open. Early windows (first 20 of 655) have `start_mid = None` / no key because the window was already open when collection started — verified by audit (`655 total, 635 with start_mid`). The dashboard note about "47.5¢ because window already ran 10-20 s" is consistent — `server/osc_dash.py:296`.
- **Mid extremes near 0 or 1 are partly collector artifacts:** when one book side is empty the fallback `bid + 0.005` / `ask - 0.005` can produce mids like `-0.004` or `1.004` (seen in snapshots last 5k: `p05=0.003, p95=0.985, min=-0.004, max=1.004`). For windows that settle to a decided outcome the book empties on one side and the fallback exaggerates the excursion. The measured `range median 62¢` and `max_exc median 49.5¢` therefore overstate the tradeable mid path slightly.

---

## 2. Window Classification

### 2.1 Definition

- Base = `0.50`; `max_up = max(mids) - 0.50`, `max_down = 0.50 - min(mids)` — `scripts/measure_5m_oscillation.py:128-130`, `AGENTS.md:27`.
- `OSC_THRESH_CENTS = [2.0, 3.0]` — `scripts/measure_5m_oscillation.py:54`.
- `oscillating` = both `>= 0.02`; `monotonic` = one `>= 0.02`; `flat` = neither — `scripts/measure_5m_oscillation.py:134-141`.
- Dashboard copy: "oscillating = also went ≥2¢ and also went down ≥2¢ from 50 (the other side of the pair could fill). monotonic = only one side ≥2¢ (needs exit). flat = didn't move 2¢." — `server/osc_dash.py:194-195`.

### 2.2 Dashboard vs collector divergence

- Collector's `classify_window` is the source of truth per window — `scripts/measure_5m_oscillation.py:125-141`.
- `update_summary` recomputes `any_2c`/`any_3c`/`osc`/`mono`/`flat` from the windows file with slightly different logic (e.g. `any2 = max(max_up, max_down) >= 0.02`) — `scripts/measure_5m_oscillation.py:160-177`. The dashboard's `api_analysis` recomputes yet again for histograms — `server/osc_dash.py:112-136`. These agree on the headline rates but can differ on edge buckets; always trace back to `classify_window` for per-window disputes.
- Exit thresholds displayed in the dashboard (BTC 5m +9¢, SOL +11¢, ETH/BNB/XRP +12¢, 15m +13¢) are **derived statistics, not enforced in collector code** — `AGENTS.md:28`, `server/osc_dash.py:355-363`. They are advisory.

---

## 3. Measured Findings (What the Data Actually Says)

> All stats below are from the live `run/` files at audit time. The repo previously reported 635 windows; the audited files contain **655** (growth is normal as collection continues).

### 3.1 Sample size

- **655 windows total:** 490 × 5m + 165 × 15m; 98 per 5m series, 33 per 15m series — `run/oscillation_windows.jsonl` (audited count) and `run/oscillation_summary.json:1-1484`.
- **96,132 snapshot lines** — `run/oscillation_snapshots.jsonl` (audited).

### 3.2 Classification rates (the core SPREAD-2 question)

- **Overall:** 486 oscillating (74.2%) / 169 monotonic (25.8%) / 0 flat — audited `oscillation_windows.jsonl`.
- **5m:** 354 oscillating (72.2%) / 136 monotonic (27.8%) — audited.
- **15m:** 132 oscillating (80.0%) / 33 monotonic (20.0%) — audited.
- Per-series 5m oscillating: BTC 72/98, ETH 72/98, BNB 72/98, SOL 69/98, XRP 69/98 — `run/oscillation_summary.json:4-814` + audit.
- Per-series 15m oscillating: BTC 27/33, ETH 25/33, BNB 29/33, SOL 23/33, XRP 28/33 — `run/oscillation_summary.json:1019-1650` + audit.
- **Secondary divergence:** `README.md:13` says "73% oscillating on 5m" and dashboard `/summary` hardcodes "74% / 73% / 79%" in copy — `server/osc_dash.py:343`. The primary files say **72.2% (5m) / 80.0% (15m) / 74.2% (overall)** at audit time. Use the file, not the copy.

### 3.3 Movement magnitude

- **Range (`(max_up+max_down)*100`):** median **62¢**, min 0.9¢, max 99.9¢ — audit.
- **Max single-direction excursion (`max(max_up, max_down)*100`):** median **49.5¢** — audit, consistent with `README.md:12` ("median range 49.5¢") if README meant max-excursion rather than full range (README's phrasing is ambiguous; the range median is 62¢ per audit).
- **Start deviation (`|start_mid - 0.50|*100`):** median **5.5¢** — audit; indicates mid at first sample is often already off 50¢ because first sample is not at `t=0`.
- **Every window moved ≥ 20¢ in the audited set** per dashboard claim `server/osc_dash.py:343` ("every window moved at least 20¢ from 50"); audit shows `range min 0.9¢` — a small number of windows have range < 20¢, so the dashboard's "every" is an approximation (true for ~98%+ but not 100% in raw files).
- Flat windows = 0 in all files — both collector and dashboard agree.

### 3.4 Touch pair (spread width) and queue

- **Touch pair median overall:** **1.02** (min 1.003, max 1.14) — audit.
- **Per-series medians:** BTC 5m 1.01, ETH 5m 1.01, BNB 5m 1.04, SOL 5m 1.03, XRP 5m 1.04; 15m: BTC 1.01, ETH 1.01, BNB 1.03, SOL 1.02, XRP 1.03 — `run/oscillation_summary.json` + audit. BNB/XRP run wider.
- Dashboard copy says "median ~1.01" — `AGENTS.md:28`; BNB/XRP are 1.03-1.04 per primary files, so quote "~1.01-1.04 by asset" instead.
- **Implication for SPREAD 2:** resting at 0.96 is **5-8¢ better than touch** (`touch 1.01-1.04` − `0.96`) — `server/osc_dash.py:349`. Net edge after the 4¢ merge capture must survive the monotonic tail risk.
- **Queue ahead at resting price:** sampled median **~108 shares** (p10 0, p90 ~2200) from snapshots `queue_up` — audit of `oscillation_snapshots.jsonl`. Highly skewed: many windows have deep queue early. This motivates a queue gate.

---

## 4. Strategy Translation — Is SPREAD 2 Viable?

### 4.1 The bet, precisely

- Quote **both sides at `mid - 0.02`** → `resting_pair = 0.96`, always `< 1.00` by construction — `scripts/measure_5m_oscillation.py:51-52`, `README.md:22`, `server/osc_dash.py:192`.
- If both sides fill, `mergePositions` the pair for **4¢ gross** — `README.md:3`.
- If only one side fills before the window decides, you are naked and the position settles toward 0 or 1. At `mid` extremes the loss approaches the price paid. This is the dominant risk; see §4.2.

### 4.2 Why 74% oscillating is encouraging but not sufficient

- 74% oscillating means both `max_up >= 2¢` and `max_down >= 2¢` occurred at some point — `scripts/measure_5m_oscillation.py:136-137`. That is a **price-path** condition, not a **fill** condition.
- A 2¢ mid excursion does not guarantee your resting order at `mid_open - 2¢` was touched: queue position, book updates between 1-s polls, and the fact that `mid` is derived from the UP book only all decouple the two. Measured queue median 108 shares indicates many mid touches would have filled ahead of you.
- In oscillating windows the smaller side's median excursion is **19¢** (p25 9.5¢) and the larger side's median is 49.5¢ — audit. So when a window oscillates, it tends to do so by a lot more than 2¢. A 2¢ offset is conservative relative to typical swings, which helps fill probability but does not solve queue.
- **Monotonic windows (26% 5m / 20% 15m)** are the loss driver. Their median max excursion is **50.4¢** (mean 50.0¢, p25 49.5¢, p90 50.4¢) — audit. The window runs to the wall. Without an exit, a naked leg's loss approaches the 48¢ entry price. The dashboard's "monotonic runs on average 32¢ to the end" — `server/osc_dash.py:350` — is understated relative to the audited raw files (likely a filtered subset); the files say ~50¢.

### 4.3 Entry / exit / risk gates required by the data

**Entry:**

- **Early entry at `mid - 2¢` on both sides before the window decides** — `README.md:22` ("mid-2¢, queue 50"). The dashboard's "place at 48¢/48¢ before open, be first in queue — queue 50" — `server/osc_dash.py:378-379` — matches.
- **Queue gate 50 shares** — `README.md:22`, `strategy/config.py:528` (`max_rest_queue_ahead = 50.0`). Measured rationale `strategy/config.py:523-528`: quotes with `queue_ahead=0` filled 6-12%, at 50+ shares <3%. Audited queue median 108 confirms this gate will skip a material fraction of polls; set to 0 to disable.
- **Pair-cost gate `< 0.995`** — `README.md:22`, `strategy/config.py:631` (`max_pair_cost = 0.995`). Source comment: "a common healthy pair (0.51+0.48) costs 0.99, which leaves 1¢ profit and must never be refused" — `strategy/config.py:628-631`. For SPREAD 2 at 0.96 this gate is easily satisfied at touch 1.01-1.04; its role is to block entries when the book is already inverted-wide.
- **Price band and spread / depth gates** from `strategy/config.py` (legacy values tuned for rewards farming, not SPREAD 2 — see §4.5):
  - `price_band 0.10-0.90` — `strategy/config.py:550-551`; `decided_price 0.02` (don't quote when decided) — `strategy/config.py:122`; `max_book_spread 0.06` — `strategy/config.py:128,365,391`; `min_book_depth_sh 200` — `strategy/config.py:132`.

**Exit for monotonic tail:**

- Dashboard-recommended thresholds (derived stats, not enforced): BTC 5m +9¢, SOL +11¢, ETH/BNB/XRP +12¢, 15m +13¢ — `server/osc_dash.py:357-361`. Rationale: "cut monotonic at 12¢ loses 26% of windows but saves from 32¢ average loss" — `server/osc_dash.py:362`.
- Measured implication: if mid moves **+12¢ one-way without a 2¢ reversal**, close the stuck side at the bid (pay half-spread) rather than hold to settlement. The `pairsExit` pattern in `strategy/config.py:645-674` (`enable_pairs_rule`, `pairs_exit_window_sec=900`, gain +3.68¢ / cost -3.67¢ measured on rule-era data) is the coded version of this, though its numbers come from a different (rewards) sample and should be re-measured for SPREAD 2.
- `t_remaining` gate: `min_t_remaining_sec 15.0` — `strategy/config.py:640` — prevents late entries where there is no time to exit.

**Inventory / sizing:**

- The legacy `MakerConfig` caps are dollar-based for good reason: `max_naked_usd 120` — `strategy/config.py:101`; `max_fleet_naked_usd 400` — `strategy/config.py:445`; `max_committed_usd 1000` (overridden to bankroll) — `strategy/config.py:471`. Comments explain why share caps failed (`$190 at risk while share cap read 233`) — `strategy/config.py:88-92`. These translate directly to SPREAD 2: cap naked cost, not share count.
- `quote_shares 120`, `min_quote_shares 50` — `strategy/config.py:519,531`; `max_fills_per_market 25` — `strategy/config.py:635`; `requote_interval 2s`, `poll_interval 1s` — `strategy/config.py:636-637`.

### 4.4 Fees and merge gas (official venue docs)

- **Maker fee = 0; taker fee = `C × feeRate × p × (1-p)`** — https://docs.polymarket.com/trading/fees, https://help.polymarket.com/en/articles/13364478-trading-fees. Market's `feeSchedule` (`rate`, `exponent`, `takerOnly`, `rebateRate`) is read from Gamma — https://docs.polymarket.com/market-data/market-details.
- **Crypto category fee rate = 0.07 (7%)**, maker rebate 20% — https://docs.polymarket.com/trading/fees. At `p=0.50`, 100 shares costs **$1.75** taker fee (peak). See fee table — https://docs.polymarket.com/trading/fees.
- **For SPREAD 2 as maker on both legs:** you pay **no CLOB fee** on the resting fills; you pay the **taker fee only if you exit a naked leg by crossing the spread**. The dashboard's "pair pays exactly $1 → single leg loss is price paid" framing is correct; the exit cost is `half-spread + taker fee` (worst ~1¢ + ~1.7¢ at mid).
- **Merge is not a CLOB trade:** `mergePositions(collateralToken=USDC.e 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174, parentCollectionId=0x00..00, conditionId, partition=[1,2], amount)` on CTF `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` — https://github.com/cengizmandros/polymarket-cheatsheet (CTF ops), https://github.com/AleSZanello/poly-examples (merge calldata). V2 trading collateral is pUSD on the CLOB side; CTF side remains USDC.e — https://github.com/runesleo/polymarket-toolkit/blob/main/docs/v2-ctf-ops-faq.md, https://github.com/TrebuchetDynamics/polygolem/blob/main/docs/LIVE-TRADE-WALKTHROUGH.md.
- **`merge_gas_usd 0.05` per transaction, not per share** — `strategy/config.py:264`. Source note: "Polygon gas for a CTF merge is cents, deliberately conservative; U6's verify_merge replaces with real figure" — `strategy/config.py:255-263`. At 4¢ per pair, any pair size needs `pairs × 0.04 > gas` to be economic; with `0.05` gas one share's 4¢ alone is not economic — size matters.

### 4.5 What `strategy/config.py` is and is not for SPREAD 2

- `strategy/config.py:1-5` header says numbers derived from `powerwinner`'s 56,768 BTC/ETH 5-min fills over 2026-07-14..21. `AGENTS.md:22` warns: "Heavily commented with measured values from the hunter fleet — most MakerConfig fields (rewards, skew, caps) are legacy from that experiment, not the SPREAD-2 target. Verify against README.md:22."
- **Legacy / do-not-reuse-as-is:** `objective="rewards"`, `reward_offset`, `rewardsMaxSpread 4.5c / rewardsMinSize 50` (per `strategy/config.py:32-36`), `est_reward_pool_usd 143` — `strategy/config.py:498`, `exponent`/`rebateRate` tuning for reward score, `max_market_frac`, scarcity logic, markout horizons, float-mark retention. These are reward-farming artefacts.
- **Reusable mechanics (with re-measurement):** `price_band`, `decided_price`, `max_book_spread` / `min_book_depth_sh`, `max_rest_queue_ahead`, `max_pair_cost`, `max_naked_usd` / `max_fleet_naked_usd` / `max_committed_usd`, `enable_pairs_rule` / `pairs_exit_window_sec`, `requote/poll intervals`, `book Health` gates, `parse_book`/`full_book`/`recent_trades` plumbing, latency constants.

---

## 5. Venue Mechanics (Official Polymarket Sources)

### 5.1 Market discovery & identity

- **`GET https://gamma-api.polymarket.com/events?series_slug=<slug>`** lists events; each contains `markets[]` with `conditionId` (bytes32), `clobTokenIds` (JSON string `[yes, no]`), `slug`, `eventStartTime`, `endDate`/`endDateIso` — https://docs.polymarket.com/api-reference/events/list-events, https://docs.polymarket.com/api-reference/events/list-events-keyset-pagination.
- Polymarket up/down crypto markets use slug pattern `btc-updown-5m-<unix>` and recurrence `5m`/`15m` — https://polymarkets.co.il/en/guide/polymarket-crypto-5min-markets/.
- `GET https://gamma-api.polymarket.com/markets?clob_token_ids=<id>` and `GET /events?slug=` are more reliable than `?conditionId=` for lookup — https://github.com/AleSZanello/poly-examples ("Best method: `GET /markets?clob_token_ids=X`").
- `conditionId` is the CTF condition identifier; `tokenIds` are ERC1155 position IDs derived via `getCollectionId(parent=0, conditionId, indexSet=1|2)` + `getPositionId(USDC.e, collectionId)` — https://github.com/Polymarket/agent-skills/blob/HEAD/ctf-operations.md.

### 5.2 CLOB book & order constraints

- **Book read:** `GET https://clob.polymarket.com/book?token_id=<id>` → `asset_id`, `bids[]`, `asks[]`, `min_order_size`, `tick_size`, `neg_risk`, `last_trade_price`, `hash` — https://docs.polymarket.com/api-reference/market-data/get-order-book, https://docs.polymarket.com/trading/orderbook.
- **Tick sizes:** `0.1` (10¢), `0.01` (1¢), `0.005`, `0.0025` (World Cup), `0.001` (0.1¢ at extremes `<0.04` or `>0.96`), `0.0001` — https://docs.polymarket.com/market-data/market-details. Gamma field is `orderPriceMinTickSize` — https://docs.polymarket.com/market-data/market-details, https://polymarkets.co.il/en/guide/polymarket-api-recipes/. Displayed book `tick_size` is the enforcement; always read live rather than hardcode — https://polymarkets.co.il/en/guide/polymarket-api-recipes/.
- **Min order size:** `orderMinSize` in USDC notional (e.g. `"5"`) — https://docs.polymarket.com/market-data/market-details. The book returns `min_order_size` as a decimal string — https://docs.polymarket.com/trading/orderbook.
- **Neg risk:** `neg_risk` selects the exchange contract (standard `0x4bFb41…8982E` vs NegRisk `0xC5d563…80a`) — https://github.com/Polymarket/agent-skills/blob/HEAD/ctf-operations.md, https://polymarkets.co.il/en/guide/polymarket-api-recipes/.
- **Place order:** `POST https://clob.polymarket.com/order` with EIP-712 `Order` (fields: `salt`, `maker`, `signer`, `tokenId`, `makerAmount`, `takerAmount`, `side` BUY/SELL, `signatureType` 0/1/2/3, `timestamp`, `metadata`, `builder`, `signature`) + `orderType` GTC/FOK/GTD/FAK, `owner` UUID, `deferExec`, `postOnly` — https://docs.polymarket.com/api-reference/trade/post-a-new-order, https://docs.polymarket.com/trading/orders/overview.
- **Order status:** `GET https://clob.polymarket.com/data/order/<id>` and `GET .../data/orders` — https://docs.polymarket.com/trading/orders/overview.

### 5.3 Data API trades (fill vs cancel disambiguation)

- **`GET https://data-api.polymarket.com/trades?market=<conditionId>&limit=500`** — https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets (params: `limit`, `offset`, `market`, `eventId`, `user`, `side`, `start`, `end`, `takerOnly`). `strategy/markets.py:271-320` uses this to distinguish fills from cancels when the book empties: "book-only model reported 50% fill rate where tape-confirmed rate was 3%" — `strategy/markets.py:278`. De-duplication is by `(transactionHash, asset, timestamp, price, size)` not by time window — `strategy/markets.py:306-312`.

### 5.4 CTF merge (capital recycling)

- CTF address `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` on Polygon; collateral USDC.e `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`; pUSD wrapper `0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb` in V2 — https://github.com/Polymarket/agent-skills/blob/HEAD/ctf-operations.md, https://github.com/TrebuchetDynamics/polygolem/blob/main/docs/LIVE-TRADE-WALKTHROUGH.md, https://github.com/runesleo/polymarket-toolkit/blob/main/docs/v2-ctf-ops-faq.md.
- **`mergePositions(USDC.e, 0x00.., conditionId, [1,2], amount)`** burns one YES + one NO per USDC returned; `splitPosition` is the inverse — https://github.com/AleSZanello/poly-examples, https://github.com/Polymarket/agent-skills/blob/HEAD/ctf-operations.md.
- **Use `parentCollectionId = bytes32(0)` for CLOB tokens; do not use `NegRiskAdapter` path** ("SafeMath: subtraction overflow") — https://github.com/AleSZanello/poly-examples.
- Operator-paid `fillOrders` settlement costs the user zero gas — https://github.com/TrebuchetDynamics/polygolem/blob/main/docs/LIVE-TRADE-WALKTHROUGH.md ("gasUsed 596,113 … paid by operator … user pays zero").
- User-paid merge gas is the only on-chain cost for the spread capture itself; real cost is cents per transaction.

---

## 6. Conclusions for Implementing the Bot

### 6.1 Viability of SPREAD 2 as the target

- **Data supports SPREAD 2 as the right instrument, not rewards.** The hunter fleet's rewards path funded markets that printed ~9 tape-backed fills in 74 h because they didn't trade — `strategy/config.py:316,344-349`. The `oscillation_*` data shows the 5m/15m crypto series *do* trade and oscillate enough for a price-based spread; chasing `est_reward_pool_usd` on illiquid markets is the wrong objective for this universe.
- **74% oscillating is a real edge, but it is a distribution edge, not a per-trade guarantee.** 490×5m windows produce ~354 oscillating / 136 monotonic at audit; 15m is better (80% oscillating). At 2¢ offset the book will touch your price in roughly the oscillating set plus a fraction of the monotonic set (when the monotonic leg is your side). Without queue and exit, expected value is dominated by the monotonic tail.
- **Resting 4¢ works arithmetically; economics work only at size or frequency.** 4¢ × fills − (monotonic losses + exit fees + merge gas). Merge gas $0.05 per transaction is shared across the batch, not per share; one-share pairs are uneconomic. Size per market must clear `min_order_size` (5 USDC in spec, sometimes higher) and `min_quote_shares 50` logic — `strategy/config.py:519,531`.

### 6.2 Entry design the data implies

- Enter **at or before window open** at `mid - 2¢` both sides. Poll finds the live market via `eventStartTime <= now < endDate` — `strategy/markets.py:110`; if you start polling after `start_ts`, your `start_mid` is already stale (median 5.5¢ off 0.50).
- **Gate on queue and spread** before resting: `queue_up <= 50`, `touch_pair` not inverted, book spread ≤ 6¢, depth ≥ 200 shares — `strategy/config.py:122-132,528` (re-measure for crypto; BNB/XRP wider spreads may need looser `max_book_spread` or they will be under-quoted).
- **Never quote past `price_decided 0.02` or inside `t_remaining < 15 s`** — `strategy/config.py:122,640`. The CLOB may also move tick to 0.001 at extremes — https://docs.polymarket.com/market-data/market-details — so price rounding must read `tick_size` live.

### 6.3 Exit / risk minimization (the mandatory complement)

- **Monotonic is not a tail event; it is 26% of 5m windows.** Assume every fourth position goes naked.
- **Mandatory exit rule:** if mid drifts **≥ 12¢ one-way without a 2¢ reversal**, lift the stuck side at the bid (cross) and either complete the pair if `pair_cost < 0.995` or eat the loss. Per-asset tuning (BTC 9¢, SOL 11¢, others 12¢) — `server/osc_dash.py:357-361` — reflects measured monotonic rates (BTC/SOL more monotonic). Re-measure these on your own fills; they are not enforced truths.
- **Cap exposure in dollars, not shares** — `strategy/config.py:101,445,471` — and enforce at fleet scope.
- **Do not cross-hedge late near settlement as the primary hedge** — `strategy/config.py:52-70` records that hedging near close executed "once the outcome was already known (0.01, 0.02)" and booked luck as profit. For 5m windows, the exit inside the window *is* the hedge.

### 6.4 Latency and polling implications

- Collector polls at **1 s** with 2 s requote — `strategy/config.py:636-637`, `scripts/measure_5m_oscillation.py:299`. Venue acceptance measured at **81 ms** median (`post_venue_accept 81 ms`, `net_oneway 3.93 ms`) — `strategy/config.py:709-718`. On the 300-s window this is fine for resting (GTC) but not for taking; if the bot ever takes, `FAK` with `deferExec` semantics and taker delay (`itode` 250 ms hold — https://docs.polymarket.com/api-reference/markets/get-clob-market-info) matter.
- The queue gate's staleness is bounded by polling cadence; book may move between polls and your resting price may no longer be 2¢ off mid without a requote.

### 6.5 Inventory and fee controls

- **Inventory skew** (long UP → quote UP farther, DOWN closer) — `strategy/config.py:52-100` — is the correct remedy for a naked leg that is still inside the exit window; it runs every cycle while both sides still cost real money — `strategy/config.py:60-63`.
- **Pairs-only discipline:** either complete the missing leg at ask when `pair_cost < max_pair_cost` or exit the naked leg within `pairs_exit_window_sec 900 s` — `strategy/config.py:645-674`. For 5m/15m the window is the window itself; don't hold past `end_ts`.
- **Fees:** makers pay 0; taker exits pay crypto rate 0.07×p×(1-p) — https://docs.polymarket.com/trading/fees. Rebates (20% of taker fees, daily redistribution) are estimated, not realized in P&L — `strategy/config.py:700-707`.
- **Capital accounting:** include resting notional + paired inventory + naked cost in `committed_usd` — `strategy/config.py:462-467`; dashboard dividing by resting alone overstated return 7× (1.80%/day vs 0.256%/day) — `strategy/config.py:458-463`.

---

## 7. Open Questions That Must Be Measured Before Sizing Capital

1. **Queue-conditional fill rate at 2¢ offset on crypto 5m.** The lab has queue snapshots but not fills. The `recent_trades` tape join — `strategy/markets.py:271-320` — must be run live during quoting to learn whether `oscillating` at 2¢ translates to fills at queue 0 vs 50 vs 200.
2. **BNB/XRP wider books.** Touch median 1.03-1.04 vs 1.01 on BTC/ETH — `run/oscillation_summary.json`. Does the wider touch indicate lower liquidity that requires looser `max_book_spread` / deeper `min_book_depth`, or tighter quoting that skips these series?
3. **True start_mid at t=0.** First-sample median is already 5.5¢ off 0.50 — audit. A bot that subscribes at `eventStartTime` precisely will see a different `mid - 2¢` than the collector's first sample 1-15 s later.
4. **Merge gas after V2 pUSD migration.** USDC.e vs pUSD collateral mismatch is the common merge-breakage cause — https://github.com/runesleo/polymarket-toolkit/blob/main/docs/v2-ctf-ops-faq.md. Verify which collateral your wallet holds and which `mergePositions` path the relayer expects before assuming $0.05.
5. **Exit threshold calibration by size.** Measured gain +3.68¢ / cost -3.67¢ for pairs rule — `strategy/config.py:671-674` — came from a 10k-share sample in a prior era; the 4¢ gross is fixed, but realized exit slippage and fee at 5m windows with 120-share quotes will differ.

---

## 8. Source Index (Primary)

| Claim | Source |
|-------|--------|
| 10 series × 5m/15m, 300/900 s | `scripts/measure_5m_oscillation.py:38-49` |
| `SPREAD_OFFSET 0.02 → resting_pair 0.96` | `scripts/measure_5m_oscillation.py:51-52,207` |
| Quote `mid - 2¢` both sides, `pair_cost < 0.995`, `queue 50` | `README.md:22` |
| `mid = (bid+ask)/2` UP + fallbacks | `scripts/measure_5m_oscillation.py:194-201` |
| `touch_pair = up_ask + down_ask` | `scripts/measure_5m_oscillation.py:203-205` |
| `queue_up` approx at resting bid | `scripts/measure_5m_oscillation.py:210-214` |
| Window classification vs 0.50, thresholds 2c/3c | `scripts/measure_5m_oscillation.py:54,125-141`, `AGENTS.md:27` |
| Exit thresholds BTC 9c / SOL 11c / 12c / 15m 13c (advisory) | `server/osc_dash.py:357-363`, `AGENTS.md:28` |
| 655 windows (490×5m + 165×15m), 74.2%/72.2%/80% oscillating | `run/oscillation_windows.jsonl` (audited), `run/oscillation_summary.json` |
| Touch median 1.02 (1.003-1.14, per-series 1.01-1.04), range median 62c, max_exc median 49.5c | `run/oscillation_summary.json` + audit |
| Queue median ~108 (p90 ~2200) | `run/oscillation_snapshots.jsonl` (audited sample) |
| Gamma `GET /events?series_slug` + `clobTokenIds`/`conditionId`/`eventStartTime`/`endDate` | `scripts/measure_5m_oscillation.py:68`, `strategy/markets.py:59-85,96-115` |
| CLOB `GET /book?token_id` + `bids/asks/min_order_size/tick_size/neg_risk` | `strategy/markets.py:257-268`, https://docs.polymarket.com/api-reference/market-data/get-order-book, https://docs.polymarket.com/trading/orderbook |
| Tick grid 0.1/0.01/0.005/0.0025/0.001/0.0001; dynamic 0.001 at <0.04/>0.96 | https://docs.polymarket.com/market-data/market-details |
| `orderPriceMinTickSize` / `orderMinSize` in Gamma | https://docs.polymarket.com/market-data/market-details, https://polymarkets.co.il/en/guide/polymarket-api-recipes/ |
| `GET /clob-markets/{conditionId}` fee/negRisk/tick fields | https://docs.polymarket.com/api-reference/markets/get-clob-market-info |
| Order EIP-712 fields, `orderType` GTC/FOK/GTD/FAK, `postOnly` | https://docs.polymarket.com/api-reference/trade/post-a-new-order, https://docs.polymarket.com/trading/orders/overview |
| `GET /trades?market=` / `GET https://data-api.polymarket.com/trades` | `strategy/markets.py:195,271-320`, https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets |
| Taker-only fee `C×feeRate×p×(1-p)`, crypto 0.07, maker 0, rebates | https://docs.polymarket.com/trading/fees, https://help.polymarket.com/en/articles/13364478-trading-fees, https://docs.polymarket.com/market-data/market-details |
| `maker_fee 0.0`, `rebate_rate 0.2` | `strategy/config.py:704-705` |
| CTF `mergePositions(USDC.e, 0x00.., conditionId, [1,2], amount)` | https://github.com/AleSZanello/poly-examples, https://github.com/Polymarket/agent-skills/blob/HEAD/ctf-operations.md |
| `merge_gas_usd 0.05` per transaction | `strategy/config.py:264` |
| Polygon gas for `fillOrders` is operator-paid | https://github.com/TrebuchetDynamics/polygolem/blob/main/docs/LIVE-TRADE-WALKTHROUGH.md |
| pUSD vs USDC.e V2 collateral | https://github.com/runesleo/polymarket-toolkit/blob/main/docs/v2-ctf-ops-faq.md |
| Session pooling, `(3.05,5.0)` timeout, `max_retries=0` | `scripts/measure_5m_oscillation.py:56-58`, `strategy/markets.py:20-30` |
| Slug sanitization, `parse_book` tolerance | `strategy/markets.py:52-57,207-254` |
| `MakerConfig` is rewards-legacy, verify vs SPREAD-2 target | `strategy/config.py:1-5`, `AGENTS.md:22` |
| Dashboard routes `/ /oscillation /summary /analysis /api/*` | `server/osc_dash.py:74-136,443-452` |

---

*Generated from primary sources on 2026-08-27. Re-run `scripts/measure_5m_oscillation.py` to grow `run/` and re-audit numbers; this document's numeric claims are pinned to the audited snapshot described in §3.*
