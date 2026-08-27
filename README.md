# crypto-spread — 5m/15m SPREAD 2 Bot (Independent Lab)

**מטרה:** לתפוס ספרדים ב-`BTC/ETH/BNB/SOL/XRP 5m+15m` עם `SPREAD 2 → resting_pair 0.96` (4¢ רווח לזוג + merge), לא פרסי venue.

**מה הועבר מ-spread-hunter:**
- `scripts/measure_5m_oscillation.py` — מודד כל שנייה 10 סדרות, כותב `run/oscillation_*.jsonl`
- `server/osc_dash.py` — :8802 Live + /summary עם גרפים
- `strategy/markets.py` — fetch live 5m/15m via `gamma-api /events?series_slug`
- `run/` — 635 חלונות היסטוריים (82 oscillating 74% ב-5m, touch_pair 1.01)

**ממצא (635 חלונות):**
- כל חלון זז ≥20¢ (חציון 49.5¢) — 2¢ לא מבדיל
- 5m: 73% oscillating (שני הצדדים יתפסו), 27% monotonic → צריך רף יציאה
- מומלץ: BTC 5m +9¢, SOL +11¢, ETH/BNB/XRP +12¢, 15m +13¢

**הרצה עצמאית:**
```powershell
python -m scripts.measure_5m_oscillation   # אוסף
python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802  # דשבורד
```

**הבא:** לבנות בוט עצמאי — config חדש (SPREAD 2 כיעד), quotes `mid-2¢` בשני הצדדים, queue gate 50, pair_cost <0.995, ויציאה מונוטונית לפי הרף פר נכס. הפרויקט מופרד לחלוטין מ-spread-hunter.

**תיקיות מופרדות:**
- `C:\Users\Tiger\Agents\Projects\AI Trading\spread-hunter` — הבוט המקורי (rewards)
- `C:\Users\Tiger\Agents\Projects\AI Trading\crypto-spread` — כאן (ספרד 5m)
