"""Oscillation dashboard for 5m/15m crypto — shows whether spread 2 can be captured.

Reads run/oscillation_summary.json + run/oscillation_windows.jsonl + snapshots.
Serves :8802
"""
from __future__ import annotations
import json, time
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.middleware.gzip import GZipMiddleware

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"

app = FastAPI(title="Oscillation")
app.add_middleware(GZipMiddleware, minimum_size=1000)

def load_summary():
    f = RUN / "oscillation_summary.json"
    if not f.exists():
        return {"ts": 0, "per_series": {}}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except: return {"ts": 0, "per_series": {}}

def load_windows(limit=200):
    return _load_all_windows()[:limit]


def _load_all_windows():
    """Cached load of all windows; invalidates when file mtime/size changes."""
    f = RUN / "oscillation_windows.jsonl"
    if not f.exists():
        return []
    # Simple cache keyed on mtime + size
    cache = getattr(_load_all_windows, "_cache", None)
    stat = f.stat()
    key = (stat.st_mtime, stat.st_size)
    if cache and cache[0] == key:
        return cache[1]
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.sort(key=lambda x: x.get("end_ts", 0), reverse=True)
    _load_all_windows._cache = (key, rows)  # type: ignore[attr-defined]
    return rows

# --- goal counts per duration (editable in dashboard, persisted in localStorage) ---
DEFAULT_GOALS = {300: 500, 900: 150}

def _agg_goals(rows):
    from collections import defaultdict
    by_dur = defaultdict(list)
    for r in rows:
        by_dur[r.get("duration", 300)].append(r)
    out = {}
    for dur in (300, 900):
        ws = by_dur.get(dur, [])
        n = len(ws)
        any2 = sum(1 for w in ws if max(w.get("max_up", 0), w.get("max_down", 0)) >= 0.02)
        mono = sum(1 for w in ws if w.get("class") == "monotonic")
        osc = sum(1 for w in ws if w.get("class") == "oscillating")
        flat = sum(1 for w in ws if w.get("class") == "flat")
        out[str(dur)] = {"label": "5m" if dur == 300 else "15m", "duration": dur, "goal": DEFAULT_GOALS[dur], "n": n, "any_2c": any2, "oscillating": osc, "monotonic": mono, "flat": flat}
    total = len(rows)
    out["total"] = {"n": total, "any_2c": sum(1 for r in rows if max(r.get("max_up",0), r.get("max_down",0))>=0.02), "oscillating": sum(1 for r in rows if r.get("class")=="oscillating"), "monotonic": sum(1 for r in rows if r.get("class")=="monotonic")}
    return out

def load_live_snaps():
    f = RUN / "oscillation_snapshots.jsonl"
    if not f.exists():
        return {}
    # last snap per series
    last={}
    for line in f.read_text(encoding="utf-8").splitlines()[-2000:]:
        if not line.strip(): continue
        try:
            r=json.loads(line)
            last[r["series"]] = r
        except: continue
    return last

@app.get("/api/oscillation")
def api():
    summary = load_summary()
    wins = load_windows(200)
    live = load_live_snaps()
    goals = _agg_goals(_load_all_windows())
    now=time.time()
    return {"now": now, "summary": summary, "windows": wins, "live": live, "goals": goals, "default_goals": DEFAULT_GOALS}

@app.get("/api/goals")
def api_goals():
    return _agg_goals(_load_all_windows())


# --- backtest API (Plan T4) -------------------------------------------------
TICKS_DIR = RUN / "ticks"

@app.get("/api/ticks/manifest")
def api_ticks_manifest():
    """List available tick files + manifest stats for the slider UI."""
    out = {"files": [], "manifest": None}
    if not TICKS_DIR.exists():
        return out
    mf = TICKS_DIR / "manifest.json"
    if mf.exists():
        try:
            out["manifest"] = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            pass
    for f in sorted(TICKS_DIR.iterdir()):
        if f.suffix in (".jsonl", ".gz") and f.is_file():
            out["files"].append({
                "name": f.name,
                "bytes": f.stat().st_size,
                "mtime": f.stat().st_mtime,
            })
    return out


@app.get("/api/backtest")
def api_backtest(
    file: str = "",
    offset: float = 0.02,
    queue: float = 50.0,
    pair_cost: float = 0.995,
    exit_default_5m: float = 0.12,
    exit_default_15m: float = 0.13,
    exit_reversal: float = 0.02,
    size: int = 120,
    fill_model: str = "tape",
    gas: float = 0.05,
    limit_windows: int = 0,
):
    """Run backtest on selected tick file or all files in run/ticks/."""
    from backtest import BacktestParams, iter_ticks, replay
    from backtest.engine import _simulate_window, group_by_cid

    exit_thresh = {
        "default_5m": exit_default_5m,
        "default_15m": exit_default_15m,
    }

    params = BacktestParams(
        offset=offset, queue_gate=queue, pair_cost_gate=pair_cost,
        exit_thresh_by_slug=exit_thresh, exit_reversal=exit_reversal,
        quote_shares=size, fill_model=fill_model, merge_gas_usd=gas,
    )
    if not TICKS_DIR.exists():
        return {"error": "no ticks dir", "params_hash": params.params_hash()}
    if file:
        # Prevent arbitrary file read: only allow files inside TICKS_DIR with
        # expected suffixes and no path traversal.
        if "/" in file or "\\" in file or ".." in file:
            return {"error": "invalid file param", "params_hash": params.params_hash()}
        if not (file.endswith(".jsonl") or file.endswith(".jsonl.gz")):
            return {"error": "invalid file suffix", "params_hash": params.params_hash()}
        source = (TICKS_DIR / file).resolve()
        try:
            source.relative_to(TICKS_DIR.resolve())
        except ValueError:
            return {"error": "invalid file path", "params_hash": params.params_hash()}
        if not source.exists() or not source.is_file():
            return {"error": f"file not found: {file}"}
    else:
        source = TICKS_DIR

    snaps = list(iter_ticks(source))
    if not snaps:
        return {"error": "no snaps", "params_hash": params.params_hash()}

    grouped = group_by_cid(snaps)
    if limit_windows and limit_windows > 0:
        grouped = grouped[:limit_windows]
    per_window = [_simulate_window(g, params) for _cid, g in grouped]

    from collections import defaultdict
    per_series = defaultdict(lambda: {"windows": 0, "pair": 0, "exit": 0,
                                      "total_pnl_cents": 0.0})
    for w in per_window:
        a = per_series[w.series]
        a["windows"] += 1
        if w.pair_captured: a["pair"] += 1
        if w.exit_taken:    a["exit"] += 1
        a["total_pnl_cents"] += w.pnl_cents
    overall = {
        "windows": sum(a["windows"] for a in per_series.values()),
        "pair": sum(a["pair"] for a in per_series.values()),
        "exit": sum(a["exit"] for a in per_series.values()),
        "total_pnl_cents": sum(a["total_pnl_cents"] for a in per_series.values()),
    }
    def _fini(d):
        n = d.get("windows", 0) or 1
        if d.get("windows", 0) == 0:
            n = 1
        return {
            "windows": d["windows"],
            "pair_rate": round(d["pair"] / n, 4) if d["windows"] else 0.0,
            "exit_rate": round(d["exit"] / n, 4) if d["windows"] else 0.0,
            "total_pnl_cents": round(d["total_pnl_cents"], 4),
            "avg_pnl_cents": round(d["total_pnl_cents"] / n, 4) if d["windows"] else 0.0,
        }
    return {
        "params_hash": params.params_hash(),
        "params": {
            "offset": offset, "queue": queue, "pair_cost": pair_cost,
            "exit_default_5m": exit_default_5m, "exit_default_15m": exit_default_15m,
            "exit_reversal": exit_reversal, "size": size,
            "fill_model": fill_model, "gas": gas,
        },
        "n_snaps": len(snaps),
        "n_windows": len(per_window),
        "per_series": {k: _fini(v) for k, v in per_series.items()},
        "overall": _fini(overall),
    }

@app.get("/api/analysis")
def api_analysis():
    # full windows for histograms (all, not 200)
    rows=[]
    f=RUN / "oscillation_windows.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            try: rows.append(json.loads(line))
            except: continue
    # histograms
    import collections
    # per series oscillating rate
    per_series={}
    for r in rows:
        k=r["series"]
        per_series.setdefault(k, []).append(r)
    hist_max=[] # buckets 0-50c in 5c steps
    buckets = list(range(0,55,5))
    hist = {b:0 for b in buckets}
    for r in rows:
        m = max(r.get("max_up",0), r.get("max_down",0))*100
        for b in buckets:
            if m < b+5:
                hist[b]+=1
                break
    # start deviation histogram
    hist_start={b:0 for b in [0,1,2,3,5,10]}
    for r in rows:
        d=abs((r.get("start_mid") or 0.5)-0.5)*100
        for thr in sorted(hist_start):
            if d < thr+1:
                hist_start[thr]+=1
                break
    return {"total": len(rows), "per_series": {k: len(v) for k,v in per_series.items()}, "hist_max": hist, "hist_start": hist_start, "rows": rows[-100:]}

PAGE = r"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oscillation — 5m/15m Spread Capture</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0d12;--panel:#12161d;--panel2:#171c24;--line:#232a35;--tx:#e7ebf3;--dim:#8792a6;--faint:#535e70;--up:#33c9b5;--upS:#12302c;--down:#f0684d;--gold:#e8b84b;--proj:#7b9bf7;--r:10px;--disp:'Space Grotesk',system-ui;--mono:'IBM Plex Mono',monospace;--body:'IBM Plex Sans',system-ui}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--tx);font:13px/1.5 var(--body);-webkit-font-smoothing:antialiased}
a{color:var(--proj);text-decoration:none} a:hover{text-decoration:underline}
.mono{font-family:var(--mono)}
.hdr{padding:16px 20px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.hdr h1{margin:0;font:700 16px var(--disp)} .tag{border:1px solid var(--up);color:var(--up);border-radius:99px;padding:2px 8px;font-size:10px;font-weight:700}
.wrap{max-width:1400px;margin:0 auto;padding:16px 20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:1000px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card h3{margin:0 0 6px;font:700 11px var(--disp);letter-spacing:.08em;text-transform:uppercase}
.kpi{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.kpi .box{flex:1;min-width:90px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 9px;text-align:center}
.box .lbl{font:600 9px var(--disp);letter-spacing:.07em;color:var(--faint);text-transform:uppercase}
.box .val{font:700 18px var(--mono);margin-top:2px}
.box .sub{font:400 10px var(--mono);color:var(--dim)}
.bar{height:6px;background:var(--panel2);border:1px solid var(--line);border-radius:99px;overflow:hidden;margin-top:6px}
.fill{height:100%;border-radius:99px}
.fill.up{background:var(--up)} .fill.warn{background:var(--proj)} .fill.gold{background:var(--gold)} .fill.down{background:var(--down)}
.tbl{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
.tbl th{font:700 11px var(--disp);letter-spacing:.06em;text-transform:uppercase;color:var(--faint);text-align:right;padding:8px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl td{padding:10px 8px;border-bottom:1px solid #1a2029;font-size:13px;vertical-align:middle}
.price-up{color:var(--up);font-weight:700;font-family:var(--mono)}
.price-down{color:var(--down);font-weight:700;font-family:var(--mono)}
.price-small{font-size:10px;font-weight:500;opacity:.85}
.candle-wrap{width:110px}
.candle-bar{height:10px;background:var(--panel2);border:1px solid var(--line);border-radius:99px;position:relative;overflow:hidden}
.candle-wick{position:absolute;top:50%;height:2px;background:var(--faint);transform:translateY(-50%)}
.candle-body{position:absolute;top:2px;bottom:2px;border-radius:3px}
.pill{font:700 9px var(--disp);letter-spacing:.06em;padding:2px 7px;border-radius:99px;border:1px solid var(--line);white-space:nowrap}
.pill-osc{background:rgba(51,201,181,.12);color:var(--up);border-color:rgba(51,201,181,.3)}
.pill-mono{background:rgba(240,104,77,.12);color:var(--down);border-color:rgba(240,104,77,.3)}
.pill-flat{background:var(--panel2);color:var(--dim)}
.note{font-size:11px;color:var(--dim);line-height:1.5;margin-top:8px;border-top:1px dashed var(--line);padding-top:8px}
.live-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
@media(max-width:900px){.live-grid{grid-template-columns:repeat(2,1fr)}}
.liveBox{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 9px}
</style></head><body>
<div class="hdr">
  <h1>◆ תצפיות תנודה — 5m / 15m ספרד 2¢</h1>
  <span class="tag">Spread Hunter · oscillation lab</span>
  <span style="flex:1"></span>
  <span id="updated" class="mono" style="font-size:11px;color:var(--dim)"></span>
</div>
<div class="wrap">
  <div class="card" style="margin-bottom:12px">
    <h3>איך לקרוא <span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--dim)">— מה נמדד</span></h3>
    <div style="font-size:11.5px;color:var(--dim);line-height:1.6">
      <b style="color:var(--tx)">SPREAD 2 = 2¢ מה-mid לשני הצדדים → resting_pair = 0.96</b> (תמיד &lt;1.00, רווח 4¢ אם שני הצדדים מתמלאים ו-merge). כל שנייה נדגם ה-book: <code>mid = (best_bid+best_ask)/2</code> מ-UP, <code>touch_pair = up_ask + down_ask</code> (≈1.01). <br>
      <span style="display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:2px 7px;margin-top:4px"><b style="color:var(--tx)">50/50 בפתיחה:</b> <span style="color:var(--faint)">התחיל ב</span> = ה-mid הראשון (≈50¢), <b style="color:var(--up)">עלה ל</b> = ה-high (+X ירוק), <b style="color:var(--down)">ירד ל</b> = ה-low (−X אדום) — שניהם נמדדים מ-50. למשל 50→60 = <span style="color:var(--up)">+10¢</span>, 50→40 = <span style="color:var(--down)">−10¢</span>.</span><br>
      <b>oscillating</b> = גם עלה ≥2¢ וגם ירד ≥2¢ מ-50 (הזוג השני יכול להתמלא). <b>monotonic</b> = רק צד אחד ≥2¢ (צריך יציאה). <b>flat</b> = לא זז 2¢. כל חלון לחיץ → Polymarket.
    </div>
  </div>

  <div id="goalBar" class="card" style="margin-bottom:12px;border-top:2px solid var(--gold)"></div>
  <div id="liveBar" class="card" style="margin-bottom:12px"></div>
  <div id="grid" class="grid"></div>
  <div id="tables" style="margin-top:12px"></div>
</div>
<script>
const $=s=>document.getElementById(s);
const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
const pct=(a,b)=> b?Math.round(a/b*100):0;
const hms=s=>{s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?`${h}h ${String(m).padStart(2,'0')}m`:`${m}m ${String(x).padStart(2,'0')}s`;};
function pill(cls,txt){return `<span class="pill ${cls}">${txt}</span>`;}
function clsPill(c){return c==='oscillating'?pill('pill-osc','oscillating תנודתי'):c==='monotonic'?pill('pill-mono','monotonic חד-כיווני'):c==='flat'?pill('pill-flat','flat שטוח'):pill('pill-flat',esc(c));}
async function tick(){
  let data; try{data=await (await fetch('/api/oscillation',{cache:'no-store'})).json();}catch(e){return;}
  const sum=data.summary||{}, per=sum.per_series||{}, live=data.live||{}, wins=data.windows||[];
  $('updated').textContent = sum.ts? `עודכן לפני ${hms((data.now||Date.now()/1000)-sum.ts)} · ${wins.length} חלונות סגורים` : 'אין נתונים עדיין — המדידה רצה';
  // goal bar — 🎯 n goal / n passed / +-2c touched / monotonic
  (function(){
    const g=data.goals||{}, dg=data.default_goals||{'300':500,'900':150};
    const fmt=(dur)=>{
      const k=String(dur), cur=g[k]||{goal:dg[k],n:0,any_2c:0,monotonic:0,oscillating:0};
      const goal=parseInt(localStorage.getItem('goal_'+k)||cur.goal,10);
      const n=cur.n, any2=cur.any_2c, mono=cur.monotonic, osc=cur.oscillating;
      const pctGoal=Math.min(100,Math.round(n/goal*100));
      const remain=Math.max(0,goal-n);
      return {goal,n,any2,mono,osc,pctGoal,remain,label:dur===300?'5 דקות (300s)':'15 דקות (900s)', short:dur===300?'5m':'15m'};
    };
    const g5=fmt(300), g15=fmt(900);
    const gt=g.total||{n:0,any_2c:0,monotonic:0,oscillating:0};
    const bar=(x)=>`<div class="card" style="flex:1;min-width:280px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px"><div style="font:700 11px var(--disp);letter-spacing:.07em;color:var(--faint)">🎯 ${x.short} — ${x.label}</div><div class="mono" style="font-size:18px;font-weight:700;margin:6px 0">${x.goal} <span style="font-size:12px;color:var(--dim)">goal</span> / ${x.n} <span style="font-size:12px;color:var(--up)">passed</span> / ${x.any2} <span style="font-size:12px;color:var(--gold)">±2¢ touched</span> / ${x.mono} <span style="font-size:12px;color:var(--down)">monotonic</span></div><div style="display:flex;gap:6px;align-items:center"><div class="bar" style="flex:1;height:8px"><div class="fill ${x.pctGoal>=100?'up':x.pctGoal>=70?'gold':'warn'}" style="width:${x.pctGoal}%"></div></div><span class="mono" style="font-size:11px;color:var(--dim)">${x.pctGoal}%</span></div><div class="mono" style="font-size:10px;color:var(--dim);margin-top:4px">oscillating ${x.osc} · flat ${g[String(x.short==='5m'?300:900)]?.flat||0} · נותר ${x.remain} ליעד · <span style="color:var(--faint)">יעד ניתן לעריכה ↓</span></div><div style="margin-top:6px;display:flex;gap:6px;align-items:center"><span class="mono" style="font-size:10px;color:var(--dim)">יעד:</span><input id="goalIn${x.short}" type="number" min="1" step="10" value="${x.goal}" style="width:90px;background:var(--bg);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:4px 6px;font:500 12px var(--mono)"><button onclick="(function(){const v=parseInt(document.getElementById('goalIn${x.short}').value,10);if(v>0){localStorage.setItem('goal_${x.short==='5m'?300:900}',v);tick();}})()" style="background:var(--panel);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:4px 10px;font:600 11px var(--disp);cursor:pointer">שמור</button></div></div>`;
    const tot=`<div class="card" style="flex:0 0 180px;min-width:160px;background:var(--panel);border:1px dashed var(--line);border-radius:10px;padding:12px;text-align:center"><div style="font:700 11px var(--disp);letter-spacing:.07em;color:var(--faint)">סה״כ</div><div class="mono" style="font-size:16px;font-weight:700;margin-top:4px">${gt.n} חלונות</div><div class="mono" style="font-size:10px;color:var(--dim)">${gt.any_2c} touched · ${gt.monotonic} mono · ${gt.oscillating} osc</div></div>`;
    $('goalBar').innerHTML=`<h3>🎯 Goal Count — יעדים לספירת חלונות</h3><div style="font-size:11px;color:var(--dim);margin-bottom:10px">פורמט: <b style="color:var(--tx)">n goal</b> / <b style="color:var(--up)">n passed</b> / <b style="color:var(--gold)">±2¢ touched</b> / <b style="color:var(--down)">monotonic</b> · היעד ברירת מחדל 500 ל-5m / 150 ל-15m — ערוך ושמור (נשמר בדפדפן).</div><div style="display:flex;gap:10px;flex-wrap:wrap">${bar(g5)}${bar(g15)}${tot}</div>`;
  })();
  // live bar
  let liveHtml = '<h3>חלונות חיים עכשיו — Live mids</h3><div class="live-grid">';
  const order=['btc-up-or-down-5m','eth-up-or-down-5m','bnb-up-or-down-5m','sol-up-or-down-5m','xrp-up-or-down-5m','btc-up-or-down-15m','eth-up-or-down-15m','bnb-up-or-down-15m','sol-up-or-down-15m','xrp-up-or-down-15m'];
  for(const k of order){
    const s=live[k];
    if(!s){ liveHtml+=`<div class="liveBox"><div style="font:700 10px var(--disp);color:var(--faint)">${k}</div><div style="color:var(--dim);font-size:11px">אין live / סגור</div></div>`; continue; }
    const mid=s.mid==null?'-':(s.mid*100).toFixed(1)+'¢';
    const tp=s.touch_pair==null?'-':s.touch_pair.toFixed(3);
    const rem=s.t_rem==null?'-':hms(s.t_rem);
    const q=s.queue_up==null?'-':Math.round(s.queue_up);
    liveHtml+=`<div class="liveBox"><div style="font:700 10px var(--disp);color:var(--faint)">${k}</div><div class="mono" style="font-size:12px">mid ${mid} · touch ${tp}</div><div class="mono" style="font-size:10px;color:var(--dim)">queue @rest ${q} · נותר ${rem}</div><div style="font-size:10px"><a href="https://polymarket.com/market/${s.slug}" target="_blank" rel="noopener">${esc(s.slug.slice(0,28))} ↗</a></div></div>`;
  }
  liveHtml+='</div>';
  $('liveBar').innerHTML=liveHtml;

  // per-series cards
  let grid='';
  for(const k of order){
    const s=per[k];
    if(!s) continue;
    const n=s.windows||0;
    const any2=s.any_2c||0, any3=s.any_3c||0, osc=s.oscillating||0, mono=s.monotonic||0, flat=s.flat||0;
    const p2=pct(any2,n), p3=pct(any3,n), po=pct(osc,n), pm=pct(mono,n);
    grid+=`<div class="card"><h3>${esc(s.label)} — ${s.duration===300?'5 דקות':'15 דקות'} <span style="font-weight:400;color:var(--dim);text-transform:none;letter-spacing:0">· ${n} חלונות</span></h3>
      <div class="kpi">
        <div class="box"><div class="lbl">כל תנודה ≥2¢</div><div class="val">${any2}/${n}</div><div class="sub">${p2}% מהחלונות זזו 2¢ לפחות</div><div class="bar"><div class="fill up" style="width:${p2}%"></div></div></div>
        <div class="box"><div class="lbl">≥3¢</div><div class="val">${any3}/${n}</div><div class="sub">${p3}%</div><div class="bar"><div class="fill gold" style="width:${p3}%"></div></div></div>
        <div class="box"><div class="lbl">oscillating (שני כיוונים)</div><div class="val" style="color:var(--up)">${osc}/${n}</div><div class="sub">${po}% — הזוג היה יכול להתמלא</div><div class="bar"><div class="fill up" style="width:${po}%"></div></div></div>
        <div class="box"><div class="lbl">monotonic</div><div class="val" style="color:var(--down)">${mono}/${n}</div><div class="sub">${pm}% — צריך יציאה</div><div class="bar"><div class="fill down" style="width:${pm}%"></div></div></div>
      </div>
      <div class="mono" style="font-size:10px;color:var(--dim)">מדד pair ב-touch חציוני: ${s.pair_cost_median==null?'-':s.pair_cost_median.toFixed(3)} (קרוב ל-1.01 = ספר צר) · flat ${flat}/${n}</div>
      <div class="note">אם <b>oscillating ≥50%</b> עם resting 0.96 → לכידת 4¢ אפשרית. אם <b>monotonic גבוה</b> → צריך להגדיר רף יציאה (נמדוד בהמשך איפה לצאת).</div>
    </div>`;
  }
  $('grid').innerHTML=grid||'<div class="card">אין חלונות סגורים עדיין — המדידה רצה, תן לה 5-15 דקות לסגור חלון ראשון.</div>';

  // recent windows table — v2: fonts bigger, UP/DOWN both shown, Japanese candle 1..99
  let tbl='<div class="card"><h3 style="font-size:13px">חלונות אחרונים — פתיחה 50/50 (לחיץ ל-Polymarket, חדש → ישן)</h3><div style="font-size:12px;color:var(--dim);margin-bottom:8px">כל חלון נמדד מהפתיחה. <span class="price-up">אפ ירוק</span> = מחיר UP, <span class="price-down">דאון אדום</span> = מחיר DOWN (משלימים ל-100¢). <span class="price-up">עלה ל</span> = שיא UP (ירוק גדול) ולידו DOWN באותו רגע (אדום קטן). <span class="price-down">ירד ל</span> = שיא DOWN (אדום גדול) ולידו UP קטן. הנר = פתיחה→סגירה, פתילה = high/low (בחרנו צד UP).</div><table class="tbl"><tr><th>סדרה</th><th>חלון</th><th>פתיחה<br><span style="font-weight:400;letter-spacing:0">UP / DOWN</span></th><th style="color:var(--up)">עלה ל<br><span style="font-weight:400;color:var(--dim)">UP high / DOWN low</span></th><th style="color:var(--down)">ירד ל<br><span style="font-weight:400;color:var(--dim)">DOWN high / UP low</span></th><th>נר יפני<br><span style="font-weight:400">1 ← 50 → 99</span></th><th>סיווג</th><th>קישור</th></tr>';
  for(const w of wins.slice(0,80)){
    const sm = w.start_mid, cm=w.close_mid, mx=w.max_mid, mn=w.min_mid;
    const fmt = v=> v==null?'-':(v*100).toFixed(1)+'¢';
    // opening: UP green / DOWN red
    const openUp = sm==null?'-':(sm*100).toFixed(1)+'¢';
    const openDown = sm==null?'-':((1-sm)*100).toFixed(1)+'¢';
    const delta = sm==null? '' : ((sm-0.50)*100).toFixed(1);
    const openHtml = sm==null?'-':`<div><span class="price-up" style="font-size:14px">${openUp} <span style="font-size:10px">אפ</span></span><span style="color:var(--faint);margin:0 4px">|</span><span class="price-down" style="font-size:14px">${openDown} <span style="font-size:10px">דאון</span></span></div><div style="font-size:10px;color:${Math.abs(sm-0.50)>=0.015?'var(--gold)':'var(--faint)'}">${delta>0?`+${delta}¢ מ-50`:`${delta}¢ מ-50`} ${Math.abs(sm-0.50)>=0.02?'⚠ לא 50':''}</div>`;
    // up column: UP high green big + DOWN low small red
    const upHigh = mx==null?'-':(mx*100).toFixed(1)+'¢';
    const upDownLow = mx==null?'-':((1-mx)*100).toFixed(1)+'¢';
    const upExc = (w.max_up*100).toFixed(1);
    const upHtml = mx==null?'<span style="color:var(--dim)">—</span>':`<div><span class="price-up" style="font-size:14px">${upHigh}</span> <span style="font-size:11px;color:var(--up)">+${upExc}¢</span></div><div class="price-down price-small">${upDownLow} דאון</div>`;
    // down column: DOWN high red big + UP low small green
    const downHigh = mn==null?'-':((1-mn)*100).toFixed(1)+'¢';
    const downUpLow = mn==null?'-':(mn*100).toFixed(1)+'¢';
    const downExc = (w.max_down*100).toFixed(1);
    const downHtml = mn==null?'<span style="color:var(--dim)">—</span>':`<div><span class="price-down" style="font-size:14px">${downHigh}</span> <span style="font-size:11px;color:var(--down)">+${downExc}¢</span></div><div class="price-up price-small">${downUpLow} אפ</div>`;
    // candle: UP side 1..99, open->close body, wick high-low
    const o = sm==null?50:sm*100, c = cm==null?o:cm*100, h = mx==null?o:mx*100, l = mn==null?o:mn*100;
    const lo = Math.min(o,c), hi = Math.max(o,c);
    const bodyLeft = Math.min(o,c), bodyW = Math.abs(c-o);
    const wickLeft = l, wickW = h-l;
    const bodyColor = c>=o ? 'var(--up)' : 'var(--down)';
    const candle = `<div class="candle-wrap"><div class="candle-bar"><div class="candle-wick" style="left:${wickLeft}%;width:${wickW}%;"></div><div class="candle-body" style="left:${bodyLeft}%;width:${Math.max(2,bodyW)}%;background:${bodyColor};border:1px solid ${bodyColor}"></div><div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--faint);opacity:.6"></div></div><div style="display:flex;justify-content:space-between;font-size:8px;color:var(--faint);margin-top:2px"><span>1</span><span>50</span><span>99</span></div><div style="font-size:10px;color:var(--dim);margin-top:1px">טווח ${((h-l)).toFixed(1)}¢ · סגירה ${(c).toFixed(1)}¢</div></div>`;
    tbl+=`<tr><td style="font-weight:700">${esc(w.label)}</td><td class="mono" title="${esc(w.slug)}" style="font-size:12px">${esc(w.slug.slice(-14))}<div style="font-size:10px;color:var(--faint)">${new Date(w.start_ts*1000).toLocaleTimeString('he-IL',{hour:'2-digit',minute:'2-digit'})}</div></td><td>${openHtml}</td><td>${upHtml}</td><td>${downHtml}</td><td>${candle}</td><td>${clsPill(w.class)}</td><td><a href="${w.url}" target="_blank" rel="noopener" style="font-size:13px;font-weight:700">פתח ↗</a></td></tr>`;
  }
  tbl+='</table><div class="note" style="font-size:12px"><b>התחיל ב</b> נמדד באמת — ה-mid הראשון שנדגם אחרי שהחלון נפתח (לכן לפעמים 47.5¢ ולא 50¢: החלון כבר רץ 10-20 שניות כשהתחלנו למדוד אותו). חלונות שייפתחו מעכשיו יתחילו קרוב ל-50¢. <b style="color:var(--up)">עלה ל</b> מודד מ-50: 50→60 = <span class="price-up">+10¢</span>, <b style="color:var(--down)">ירד ל</b> = 50→40 = <span class="price-down">−10¢</span>. הנר מראה פתיחה (קצה שמאלי של הגוף), סגירה (קצה ימין), ופתילות ל-high/low (בחרנו צד UP; DOWN הוא 100-UP).</div></div>';
  $('tables').innerHTML=tbl;
}
tick(); setInterval(tick,3000);
</script></body></html>
"""
PAGE_SUMMARY = r"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>סיכום סטטיסטי — 630 חלונות</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=IBM+Plex+Mono:wght@500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0d12;--panel:#12161d;--panel2:#171c24;--line:#232a35;--tx:#e7ebf3;--dim:#8792a6;--faint:#535e70;--up:#33c9b5;--down:#f0684d;--gold:#e8b84b;--proj:#7b9bf7;--r:12px;--disp:'Space Grotesk',system-ui;--mono:'IBM Plex Mono',monospace;--body:'IBM Plex Sans',system-ui}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 var(--body)}
a{color:var(--proj)} .mono{font-family:var(--mono)}
.hdr{padding:18px 24px;background:linear-gradient(90deg,#12161d,#0f1e1c);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.hdr h1{margin:0;font:700 18px var(--disp)} .badge{border:1px solid var(--gold);color:var(--gold);border-radius:99px;padding:3px 10px;font:700 11px var(--mono)}
.wrap{max-width:1300px;margin:0 auto;padding:20px 24px}
.hero{display:grid;grid-template-columns:1.2fr .8fr;gap:16px;margin-bottom:16px}
@media(max-width:900px){.hero{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.card h3{margin:0 0 8px;font:700 12px var(--disp);letter-spacing:.08em;text-transform:uppercase}
.big{font:700 28px var(--mono);margin:4px 0}
.sub{font-size:12px;color:var(--dim)}
.kpi-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
.kpi{flex:1;min-width:110px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 12px;text-align:center}
.kpi .n{font:700 20px var(--mono)} .kpi .l{font:600 10px var(--disp);color:var(--faint);letter-spacing:.06em;text-transform:uppercase}
.insight{border-left:3px solid var(--up);padding-left:12px;margin:8px 0}
.insight.down{border-color:var(--down)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.chart-box{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px;margin-top:8px}
.note{font-size:12px;color:var(--dim);line-height:1.6;border-top:1px dashed var(--line);margin-top:12px;padding-top:10px}
</style></head><body>
<div class="hdr">
  <h1>◆ סיכום סטטיסטי — תצפיות 5m / 15m · SPREAD 2</h1>
  <span class="badge" id="totalBadge">טוען...</span>
  <span style="flex:1"></span>
  <a href="/oscillation" style="font-size:12px;border:1px solid var(--line);padding:6px 12px;border-radius:99px;background:var(--panel2)">← לטבלת החלונות</a>
  <a href="/" style="font-size:12px;border:1px solid var(--line);padding:6px 12px;border-radius:99px;background:var(--panel2)">Live</a>
</div>
<div class="wrap">
  <div id="goalBarSummary" class="card" style="margin-bottom:16px;border-top:2px solid var(--gold)"></div>
  <div class="hero">
    <div class="card" style="border-top:2px solid var(--up)">
      <h3>מסקנה — האם ספרד 2 עובד?</h3>
      <div class="big" style="color:var(--up)">כן — 74% מהחלונות תנודתיים</div>
      <div class="sub">ב-630 חלונות (470 ב-5m + 160 ב-15m) כל חלון זז לפחות <b>20¢</b> מ-50. ב-5m <b>73% oscillating</b> — שני הצדדים ב-0.96 היו נתפסים וממתמזגים ל-4¢ רווח. ב-15m <b>79% oscillating</b> — אפילו יותר טוב.</div>
      <div class="kpi-row">
        <div class="kpi"><div class="n" style="color:var(--up)">469</div><div class="l">oscillating (שני כיוונים)</div></div>
        <div class="kpi"><div class="n" style="color:var(--down)">161</div><div class="l">monotonic (חד-כיווני)</div></div>
        <div class="kpi"><div class="n">0</div><div class="l">flat</div></div>
      </div>
      <div class="insight"><b style="color:var(--up)">הזדמנות:</b> אם אתה ראשון בתור ב-48¢ (2¢ מתחת ל-mid בפתיחה) אתה תתפוס את שני הצדדים ב-3/4 מהחלונות. <code>touch_pair 1.01–1.04</code> → אתה 5–8¢ טוב יותר מה-touch.</div>
      <div class="insight down"><b style="color:var(--down)">סיכון:</b> ב-26% מה-5m המחיר רץ רק לכיוון אחד בממוצע <b>32¢</b> עד הסוף — בלי יציאה תישאר naked ותספוג. לכן צריך רף יציאה.</div>
    </div>
    <div class="card" style="border-top:2px solid var(--gold)">
      <h3>המלצת רף יציאה מונוטונית (לפי נכס)</h3>
      <div class="sub">הדוק = לצאת מוקדם (מספר קטן). BTC הכי מונוטוני → הכי הדוק.</div>
      <table style="width:100%;margin-top:10px;border-collapse:collapse;font-size:13px">
        <tr style="color:var(--faint);font:600 10px var(--disp);border-bottom:1px solid var(--line)"><td>נכס</td><td>monotonic</td><td>מומלץ</td></tr>
        <tr><td><b>BTC 5m</b></td><td>27% (25/94)</td><td><span style="background:rgba(240,104,77,.15);color:var(--down);padding:2px 8px;border-radius:99px;font-weight:700">+9¢ → צא ב-59¢ UP</span></td></tr>
        <tr><td>SOL 5m</td><td>29% (27/94)</td><td><span style="background:rgba(232,184,75,.15);color:var(--gold);padding:2px 8px;border-radius:99px;font-weight:700">+11¢ → 61¢</span></td></tr>
        <tr><td>ETH/BNB/XRP 5m</td><td>22–27%</td><td><span style="background:rgba(51,201,181,.15);color:var(--up);padding:2px 8px;border-radius:99px;font-weight:700">+12¢ → 62¢</span></td></tr>
        <tr><td>15m כללי</td><td>21% (33/160)</td><td><span style="background:var(--panel2);border:1px solid var(--line);padding:2px 8px;border-radius:99px">+13¢</span></td></tr>
      </table>
      <div class="note">הרפים חושבו מ-630 חלונות: חותך monotonic ב-12¢ מפסיד 26% מהחלונות אבל מציל מ-32¢ הפסד ממוצע. בחרתי 9¢ ל-BTC כי הוא הכי חד-כיווני.</div>
    </div>
  </div>

  <div class="grid2">
    <div class="card"><h3>1. אחוז תנודתיות — פר נכס</h3><div class="chart-box"><canvas id="cPerAsset" height="220"></canvas></div><div class="note">כל 5m: 71–78% oscillating. 15m: 69–88%. אף flat.</div></div>
    <div class="card"><h3>2. כל חלון זז לפחות 20¢ — התפלגות טווח</h3><div class="chart-box"><canvas id="cHist" height="220"></canvas></div><div class="note">חציון 49.5¢ — חצי מהחלונות הגיעו ל-99.5¢. 2¢/3¢ לא מבדיל — צריך 10¢+.</div></div>
  </div>
  <div class="grid2" style="margin-top:16px">
    <div class="card"><h3>3. פתיחה קרובה ל-50? — סטיית פתיחה</h3><div class="chart-box"><canvas id="cStart" height="200"></canvas></div><div class="note">רוב הפתיחות 49–51¢. כשזה 47.5¢ — החלון כבר רץ 15 שניות לפני שדגמנו.</div></div>
    <div class="card"><h3>4. Touch pair — כמה הספרד צר</h3><div class="chart-box"><canvas id="cPair" height="200"></canvas></div><div class="note">חציון 1.01–1.04 → אתה ב-0.96 תמיד 5–8¢ טוב יותר מה-touch. queue @rest 40–150 מניות לפניך.</div></div>
  </div>

  <div class="card" style="margin-top:16px">
    <h3>מה עושים עם זה — תכנית ספרד</h3>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:10px">
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px"><div style="font:700 11px var(--disp);color:var(--up)">שלב א — כניסה מוקדמת</div><div style="font-size:12px;color:var(--dim);margin-top:4px">להניח לפני הפתיחה ב-<b>48¢/48¢</b> (mid-2¢). להיות ראשון בתור — queue 50. ב-74% מהמקרים שני הצדדים יתפסו.</div></div>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px"><div style="font:700 11px var(--disp);color:var(--gold)">שלב ב — מעקב</div><div style="font-size:12px;color:var(--dim);margin-top:4px">אם mid זז <b>12¢</b> לכיוון אחד בלי רברס 2¢ — לסגור את הצד התקוע (exit). זה יקרה ב-26% מהחלונות.</div></div>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px"><div style="font:700 11px var(--disp);color:var(--proj)">שלב ג — מימוש</div><div style="font-size:12px;color:var(--dim);margin-top:4px">זוג שנתפס → <b>merge 4¢</b> מייד. צד בודד שיצא → הפסד 5–8¢ במקום 30¢.</div></div>
    </div>
  </div>
</div>
<script>
async function load(){
  const d=await (await fetch('/api/oscillation',{cache:'no-store'})).json();
  const sum=d.summary.per_series;
  const totalWindows = Object.values(sum).reduce((acc,s)=>acc+(s.windows||0),0);
  document.getElementById('totalBadge').textContent = totalWindows+' חלונות סגורים · '+Object.keys(sum).length+' סדרות';
  // goal bar same as main page
  (function(){
    const g=d.goals||{}, dg=d.default_goals||{'300':500,'900':150};
    const fmt=(dur)=>{
      const k=String(dur), cur=g[k]||{goal:dg[k],n:0,any_2c:0,monotonic:0,oscillating:0};
      const goal=parseInt(localStorage.getItem('goal_'+k)||cur.goal,10);
      const n=cur.n, any2=cur.any_2c, mono=cur.monotonic, osc=cur.oscillating;
      const pctGoal=Math.min(100,Math.round(n/goal*100));
      const remain=Math.max(0,goal-n);
      return {goal,n,any2,mono,osc,pctGoal,remain,label:dur===300?'5m':'15m'};
    };
    const g5=fmt(300), g15=fmt(900);
    const gt=g.total||{n:0,any_2c:0,monotonic:0,oscillating:0};
    const box=(x)=>`<div style="flex:1;min-width:240px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px"><div style="font:700 11px var(--disp);color:var(--faint)">🎯 ${x.label}</div><div class="mono" style="font-size:16px;font-weight:700;margin:6px 0">${x.goal} <span style="font-size:11px;color:var(--dim)">goal</span> / ${x.n} <span style="color:var(--up)">passed</span> / ${x.any2} <span style="color:var(--gold)">±2¢</span> / ${x.mono} <span style="color:var(--down)">mono</span></div><div class="bar" style="height:7px"><div class="fill ${x.pctGoal>=100?'up':x.pctGoal>=70?'gold':'warn'}" style="width:${x.pctGoal}%"></div></div><div class="mono" style="font-size:10px;color:var(--dim);margin-top:4px">${x.pctGoal}% · נותר ${x.remain} · osc ${x.osc}</div></div>`;
    const el=document.getElementById('goalBarSummary');
    if(el) el.innerHTML=`<h3>🎯 Goal Count — יעדים לספירת חלונות</h3><div style="font-size:11px;color:var(--dim);margin-bottom:10px">פורמט: goal / passed / ±2¢ touched / monotonic — ערוך ב-live אם צריך.</div><div style="display:flex;gap:10px;flex-wrap:wrap">${box(g5)}${box(g15)}<div style="flex:0 0 160px;background:var(--panel);border:1px dashed var(--line);border-radius:10px;padding:12px;text-align:center"><div style="font:700 11px var(--disp);color:var(--faint)">סה״כ</div><div class="mono" style="font-size:15px;font-weight:700">${gt.n} חלונות</div><div class="mono" style="font-size:10px;color:var(--dim)">${gt.any_2c} touched · ${gt.monotonic} mono</div></div></div>`;
  })();
  // 1. per asset bar
  const order=['BTC 5m','ETH 5m','BNB 5m','SOL 5m','XRP 5m','BTC 15m','ETH 15m','BNB 15m','SOL 15m','XRP 15m'];
  const labels=[], osc=[], mono=[];
  for(const k of Object.keys(sum)){
    const s=sum[k];
    const idx=order.indexOf(s.label);
    if(idx>=0){ labels[idx]=s.label; osc[idx]=s.oscillating; mono[idx]=s.monotonic; }
  }
  new Chart(document.getElementById('cPerAsset'),{type:'bar',data:{labels:order,datasets:[{label:'oscillating',data:osc.map((v,i)=> v),backgroundColor:'#33c9b5'},{label:'monotonic',data:mono,backgroundColor:'#f0684d'}]},options:{responsive:true,plugins:{legend:{position:'bottom',labels:{color:'#8792a6'}}},scales:{x:{ticks:{color:'#8792a6'},grid:{color:'#232a35'}},y:{ticks:{color:'#8792a6'},grid:{color:'#232a35'}}}}});
  // fetch full analysis for hists
  const a=await (await fetch('/api/analysis',{cache:'no-store'})).json();
  const rows=a.rows||[];
  // 2. hist max: buckets
  const buckets=[0,5,10,15,20,30,40,50];
  const histMax=new Array(buckets.length).fill(0);
  rows.forEach(r=>{const m=Math.max(r.max_up,r.max_down)*100; for(let i=0;i<buckets.length;i++){ if(m < buckets[i]+5 || i===buckets.length-1){histMax[i]++;break;}}});
  // simplified: use fixed buckets 0-10,10-20,20-30,30-40,40-50
  const bLabels=['0-10¢','10-20¢','20-30¢','30-40¢','40-50¢'];
  const bCounts=[0,0,0,0,0];
  rows.forEach(r=>{const m=Math.max(r.max_up,r.max_down)*100; if(m<10) bCounts[0]++; else if(m<20) bCounts[1]++; else if(m<30) bCounts[2]++; else if(m<40) bCounts[3]++; else bCounts[4]++;});
  new Chart(document.getElementById('cHist'),{type:'bar',data:{labels:bLabels,datasets:[{label:'חלונות',data:bCounts,backgroundColor:'#e8b84b'}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8792a6'},grid:{display:false}},y:{ticks:{color:'#8792a6'},grid:{color:'#232a35'}}}}});
  // 3. start deviation
  const sBuckets=['0-1¢','1-2¢','2-5¢','5-10¢','10¢+'];
  const sCounts=[0,0,0,0,0];
  rows.forEach(r=>{const d=Math.abs((r.start_mid||0.5)-0.5)*100; if(d<1) sCounts[0]++; else if(d<2) sCounts[1]++; else if(d<5) sCounts[2]++; else if(d<10) sCounts[3]++; else sCounts[4]++;});
  new Chart(document.getElementById('cStart'),{type:'doughnut',data:{labels:sBuckets,datasets:[{data:sCounts,backgroundColor:['#33c9b5','#7b9bf7','#e8b84b','#f0684d','#535e70']}]},options:{responsive:true,plugins:{legend:{position:'bottom',labels:{color:'#8792a6'}}}}});
  // 4. pair
  const pBuckets=['1.00-1.02','1.02-1.04','1.04-1.06','1.06+'];
  const pCounts=[0,0,0,0];
  rows.forEach(r=>{const p=r.touch_pair_median||1.01; if(p<1.02) pCounts[0]++; else if(p<1.04) pCounts[1]++; else if(p<1.06) pCounts[2]++; else pCounts[3]++;});
  new Chart(document.getElementById('cPair'),{type:'bar',data:{labels:pBuckets,datasets:[{data:pCounts,backgroundColor:'#7b9bf7'}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8792a6'}},y:{ticks:{color:'#8792a6'},grid:{color:'#232a35'}}}}});
}
load();
</script></body></html>
"""

@app.get("/", response_class=HTMLResponse)
@app.get("/oscillation", response_class=HTMLResponse)
def page():
    return HTMLResponse(PAGE, headers={"Cache-Control":"no-cache"})

@app.get("/summary", response_class=HTMLResponse)
@app.get("/analysis", response_class=HTMLResponse)
def summary_page():
    return HTMLResponse(PAGE_SUMMARY, headers={"Cache-Control":"no-cache"})


