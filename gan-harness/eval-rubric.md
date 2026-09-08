# ISSUE 96 Guard Demo Rubric 📊
## Design 🎨 — weight 2
Shows elapsed-vs-cutoff at a glance; badge hierarchy matches existing pills; no clutter at 1280x720.
Score 0-10: 0 = unreadable or off-theme; 10 = instantly legible guard state matching dashboard voice.
## Originality 💡 — weight 2
Turns an invisible latch into a memorable moment without gimmicks; parity note is crisp, not pasted docs.
Score 0-10: 0 = generic status text; 10 = a demo strip a reviewer remembers and can re-explain.
## Craft 🧱 — weight 3
Inline CSS/JS matches `osc_dash.py` conventions; expose-only changes; seeded test is deterministic; no flakes.
Score 0-10: 0 = breaks style or suite; 10 = surgical diff, green suite, pixel-clean screenshot.
## Functionality ⚙️ — weight 3
Guard fires at >=10% and not below; zero orders on skip; payload + badge + caption all present live.
Score 0-10: 0 = guard misfires or badge missing; 10 = all 6 acceptance criteria pass via code + screenshot.
### Scoring ➗
- Weighted score = (Design*2 + Originality*2 + Craft*3 + Functionality*3) / 10. Pass threshold: 7.0.
- Fail below 7.0 or if any Functionality check scores 0 — fix guard before restyling.
