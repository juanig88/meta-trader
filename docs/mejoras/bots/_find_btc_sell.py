from pathlib import Path

keys = ("TRADE_OPEN", "TRADE_CLOSE", "ENTRY_ATTEMPT", '"decision": "SELL"', "EVAL_ENTRY", "SELL")
paths = [Path("trades.log")] + sorted(Path("logs").glob("trades_*.log"), reverse=True)[:8]
for p in paths:
    if not p.exists():
        continue
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if "BTCUSD" not in line:
            continue
        if any(k in line for k in ("TRADE_OPEN", "TRADE_CLOSE", "ENTRY_ATTEMPT", "EVAL_ENTRY")):
            print(p.name, line[:500])
        elif '"decision": "SELL"' in line or '"side": "SELL"' in line:
            print(p.name, line[:500])
