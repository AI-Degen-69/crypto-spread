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
