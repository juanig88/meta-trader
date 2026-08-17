import json
from pathlib import Path

rows = json.loads(Path("docs/mejoras/bots/mql5_crawl/inbox_analysis.json").read_text(encoding="utf-8"))
print("files", len(rows))
ok = [r for r in rows if r.get("n_trades", 0) >= 40 and r.get("profit_factor")]
ok = sorted(ok, key=lambda r: (-(r.get("profit_factor") or 0), -(r.get("n_trades") or 0)))
for r in ok[:15]:
    tops = (r.get("top_symbols") or [])[:2]
    print(
        f"{r['file']}: n={r['n_trades']} WR={r.get('winrate_pct')} PF={r.get('profit_factor')} "
        f"hold={r.get('median_hold_min')} flags={r.get('flags')} top={tops}"
    )
print("--- interesting flags ---")
for r in rows:
    flags = r.get("flags") or []
    if any(x in f for f in flags for x in ("grid", "martingale", "high_wr", "fixed_sl", "ultra_short")):
        print(r["file"], flags, "n=", r.get("n_trades"))
