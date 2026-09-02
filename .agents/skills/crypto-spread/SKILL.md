---
name: crypto-spread-conventions
description: Development conventions and patterns for crypto-spread. Python project with conventional commits and pytest.
---

# Crypto Spread Conventions

> Generated from [AI-Degen-69/crypto-spread](https://github.com/AI-Degen-69/crypto-spread) on 2026-08-30

## Overview

This skill teaches Claude the development patterns and conventions used in crypto-spread.

## Tech Stack

- **Primary Language**: Python (FastAPI, Uvicorn, Requests, Pytest)
- **Architecture**: Hybrid module organization (scripts, backtest, server, strategy)
- **Test Location**: `tests/`

## When to Use This Skill

Activate this skill when:
- Making changes to this repository
- Adding new features following established patterns
- Writing tests that match project conventions
- Creating commits with proper message format

## Commit Conventions

Follow conventional commit message conventions.

### Commit Style: Conventional Commits

### Prefixes Used

- `feat`, `fix`, `chore`, `docs`, `test`

### Message Guidelines

- Keep first line concise and descriptive
- Use imperative mood ("Add feature" not "Added feature")

*Commit message example*

```text
feat(dash): add streaming upload and tick file manager (#17, #20)
```

## Architecture

### Project Structure: Single Package

This project uses **hybrid** module organization with `scripts/`, `strategy/`, `backtest/`, `server/`, and `tests/`.

### Guidelines

- Use Python 3.10+ standard patterns with type annotations
- Follow existing patterns when adding new code

## Code Style

### Language: Python

### Naming Conventions

| Element | Convention |
|---------|------------|
| Files | snake_case |
| Functions / Methods | snake_case |
| Classes | PascalCase |
| Constants | SCREAMING_SNAKE_CASE |

### Import Style

```python
# Absolute and relative module imports
from pathlib import Path
from strategy.series import SERIES
from server.osc_dash import app
```

## Testing

### Test Framework

- **Pytest**: Run tests via `python -m pytest -q`

### File Pattern: `test_*.py`

### Test Types

- **Unit tests**: Test individual algorithms and calculations (e.g. `test_backtest_engine.py`)
- **API & Integration tests**: Test FastAPI endpoints and collectors (e.g. `test_dashboard_spa.py`)

## Best Practices

Based on analysis of the codebase, follow these practices:

### Do

- Use conventional commit format (`feat:`, `fix:`, etc.)
- Follow `test_*.py` naming pattern in `tests/`
- Use snake_case for file and function names
- Write docstrings and type annotations for public functions

### Don't

- Don't write vague commit messages
- Don't skip tests for new features
- Don't block the async event loop with heavy synchronous disk/index operations

## Polymarket Protocol & Gas Rules

- **Gasless Operations**: Core trading operations on Polymarket are sponsored (gasless) when routed through the Polymarket Relayer and smart wallet flow:
  - Order creation and cancellation (CLOB EIP-712)
  - Token and operator approvals (`setupTradingApprovals()`)
  - Position merges (`mergePositions()` YES + NO `→` USDC)
  - Note: Direct un-relayed EOA transactions incur native gas.
- **Backtest Gas Parameter**: Default `merge_gas_usd` is 0.0 (reflecting sponsored relayer execution); non-zero values may be passed as an offline conservative buffer.

## Polymarket CLOB API & Docs Conventions

### Documentation MCP Tooling
- Official Docs MCP endpoint: `https://docs.polymarket.com/mcp`
- Stdio bridge configured in `C:/Users/Tiger/.gemini/config/sidecars/polymarket_docs_mcp.py` under server name `polymarket-docs`.
- Use docs queries (`query_docs_filesystem_polymarket_documentation` / `search_polymarket_documentation`) for canonical API references, request types, and endpoint signatures before implementing CLOB calls.

### CLOB Client Authentication & Rate-Limit Rules
- **Never derive keys in tight loops**: `POST /auth/api-key` and `GET /auth/derive-api-key` are heavily rate-limited. Always supply complete `ApiCreds(api_key, api_secret, api_passphrase)` loaded from `.env`.
- **Signature Types**: `1` for EOA private key signer, `2` for Polymarket Proxy, `3` for Safe multisig / email smart wallet.
- **Host Endpoints**:
  - CLOB API: `https://clob.polymarket.com`
  - Gamma API: `https://gamma-api.polymarket.com`
  - Data API: `https://data-api.polymarket.com`

### Order Operations Pattern
```python
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client_v2.order_builder.constants import BUY, SELL

# Create client with pre-derived credentials
client = ClobClient(
    host="https://clob.polymarket.com",
    key=os.environ["POLYGON_PRIVATE_KEY"],
    chain_id=137,
    signature_type=int(os.environ.get("SIGNATURE_TYPE", "1")),
    funder=os.environ.get("POLYGON_FUNDER_ADDRESS"),
)
client.set_api_creds(ApiCreds(
    api_key=os.environ["CLOB_API_KEY"],
    api_secret=os.environ["CLOB_API_SECRET"],
    api_passphrase=os.environ["CLOB_API_PASSPHRASE"],
))

# Place limit order
resp = client.create_and_post_order(OrderArgs(
    price=0.48,
    size=5,
    side=BUY,
    token_id="...",
    order_type=OrderType.GTC,
))
```

## Polymarket Real-Time Data Streams (RTDS), Market & User WebSockets

### 1. RTDS Reference Price Streams
- **Binance (`prices.crypto.binance`)**: `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt` (1s live ticks for leading signals / rapid stop-loss; BNB not supported).
- **Chainlink (`prices.crypto.chainlink`)**: `btc/usd`, `eth/usd`, `sol/usd`, `xrp/usd` (Oracle & TWAP references; BNB not supported on RTDS, use REST fallback).
- **Pyth Equity (`prices.equity.pyth`)**: Stocks, ETFs, Forex, Commodities. Emits a 2-minute historical snapshot on subscription to seed state, then streams live updates up to 5x/sec.
- **Comments (`comments`)**: Scoped to `parentEntityId` with `comment_created`, `comment_removed`, `reaction_created`, `reaction_removed`.
- **Python Usage**:
  ```python
  from polymarket import AsyncPublicClient
  from polymarket.streams import CryptoPricesSpec

  async with AsyncPublicClient() as client:
      async with await client.subscribe(
          CryptoPricesSpec(
              topic="prices.crypto.binance",
              symbols=["btcusdt", "ethusdt", "solusdt", "xrpusdt"],
          )
      ) as stream:
          async for event in stream:
              # event.payload.symbol, event.payload.value, event.payload.timestamp
              pass
  ```

### 2. CLOB Market Streams (`MarketSpec`)
- **Endpoint**: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- **Events & State Model**:
  - `book`: Full order book snapshot. Replaces local book state entirely (do not apply as delta).
  - `price_change`: Level mutations. Updates individual price levels from `price_changes` delta list.
  - `last_trade_price`: Tape trade execution updates.
  - `tick_size_change`: Dynamic tick size adjustments.
- **Custom Features (`custom_feature_enabled=True`)**: Emits `best_bid_ask`, `new_market`, and `market_resolved`.
- **Aggregation**: Multi-token subscription on a single connection; send text `PING` every 10s.

### 3. User Order & Trade Lifecycle Stream (`UserSpec`)
- **Python Usage (Authenticated)**:
  ```python
  import os
  from polymarket import AsyncSecureClient
  from polymarket.clob_types import ApiCreds
  from polymarket.streams import UserSpec

  creds = ApiCreds(
      api_key=os.environ["CLOB_API_KEY"],
      api_secret=os.environ["CLOB_API_SECRET"],
      api_passphrase=os.environ["CLOB_API_PASSPHRASE"],
  )
  async with AsyncSecureClient(
      key=os.environ["POLYGON_PRIVATE_KEY"],
      creds=creds,
      chain_id=137,
  ) as client:
      async with await client.subscribe(UserSpec()) as stream:
          async for event in stream:
              if event.type == "order":
                  # payload.order_event_type: PLACEMENT | UPDATE | CANCELLATION
                  # payload.status: LIVE | MATCHED | CANCELED | DELAYED | UNMATCHED
                  update_order_state(event.payload)
              elif event.type == "trade":
                  # payload.status: MATCHED → CONFIRMED
                  update_trade_state(event.payload)
  ```
- **Order Status Reducer Rules**:
  - `LIVE`: Order active on book; render in open orders table.
  - `DELAYED`: Order pending matching engine ingestion; mark as pending in UI.
  - `UNMATCHED`: Order rejected or resting without fill; update status or remove.
  - `MATCHED`: Fully filled; remove from open orders and transition to position state.
  - `CANCELED`: Canceled; prune from open orders table.
- **Sync & Reconnection Pattern**:
  1. **Buffer before snapshot**: Subscribe to stream and start buffering events.
  2. **Seed via REST**: Query REST open orders & positions; merge with buffered stream events.
  3. **Reconnect resync**: On reconnect, re-buffer and re-fetch REST snapshot before applying new live updates.


