"""One-off: export IC Markets MT5 symbol catalog to markdown + CSV."""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

env_path = Path(__file__).resolve().parents[4] / ".env"
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip())

import MetaTrader5 as mt5  # noqa: E402

TRADE_MODE = {
    0: "disabled",
    1: "longonly",
    2: "shortonly",
    3: "closeonly",
    4: "full",
}


def split_path(path: str) -> list[str]:
    if not path:
        return []
    # MT5 uses backslashes; normalize just in case
    return [p for p in path.replace("/", "\\").split("\\") if p]


def main() -> int:
    login = int(os.environ["MT5_LOGIN"])
    password = os.environ["MT5_PASSWORD"]
    server = os.environ["MT5_SERVER"]

    ok = mt5.initialize(login=login, password=password, server=server)
    if not ok:
        print("INIT_FAIL", mt5.last_error())
        return 1

    acc = mt5.account_info()
    symbols = mt5.symbols_get() or []

    rows: list[dict] = []
    by_type: dict[str, list[dict]] = defaultdict(list)

    for s in symbols:
        path = getattr(s, "path", "") or ""
        parts = split_path(path)
        tipo = parts[0] if parts else "Unknown"
        if parts and parts[-1] == s.name and len(parts) > 1:
            subtipo = " / ".join(parts[1:-1])
        elif len(parts) > 1:
            subtipo = " / ".join(parts[1:])
        else:
            subtipo = ""

        row = {
            "tipo": tipo,
            "subtipo": subtipo,
            "symbol": s.name,
            "description": (s.description or "").replace("|", "/").replace("\n", " ").strip(),
            "path": path,
            "currency_base": getattr(s, "currency_base", "") or "",
            "currency_profit": getattr(s, "currency_profit", "") or "",
            "trade_mode": TRADE_MODE.get(
                getattr(s, "trade_mode", None), str(getattr(s, "trade_mode", ""))
            ),
            "visible": bool(getattr(s, "visible", False)),
            "digits": getattr(s, "digits", ""),
            "spread": getattr(s, "spread", ""),
        }
        rows.append(row)
        by_type[tipo].append(row)

    rows.sort(key=lambda r: (r["tipo"].lower(), r["subtipo"].lower(), r["symbol"].lower()))
    for t in by_type:
        by_type[t].sort(key=lambda r: (r["subtipo"].lower(), r["symbol"].lower()))

    out_dir = Path(__file__).resolve().parent
    md_path = out_dir / "icmarkets_instrumentos.md"
    csv_path = out_dir / "icmarkets_instrumentos.csv"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    company = getattr(acc, "company", "") or ""
    acc_server = getattr(acc, "server", "") or server
    acc_login = getattr(acc, "login", "") or login

    lines: list[str] = [
        "# IC Markets — Catálogo de instrumentos (MT5)",
        "",
        "- **Fuente:** MetaTrader 5 API (`symbols_get`)",
        f"- **Broker / compañía:** {company}",
        f"- **Server:** {acc_server}",
        f"- **Cuenta (demo):** {acc_login}",
        f"- **Fecha de extracción:** {now}",
        f"- **Total de símbolos:** {len(rows)}",
        "",
        "> Inventario completo de instrumentos del terminal para esta cuenta. "
        "Columnas principales: **Tipo**, **Símbolo**, **Descripción**. "
        "Subtipo y trade mode van como contexto operativo.",
        "",
        "## Resumen por tipo",
        "",
        "| Tipo | Cantidad |",
        "|---|---:|",
    ]

    for tipo in sorted(by_type.keys(), key=lambda x: (-len(by_type[x]), x.lower())):
        lines.append(f"| {tipo} | {len(by_type[tipo])} |")

    lines.extend(
        [
            "",
            "CSV plano (mismo inventario): [`icmarkets_instrumentos.csv`](./icmarkets_instrumentos.csv).",
            "",
        ]
    )

    for tipo in sorted(by_type.keys(), key=str.lower):
        items = by_type[tipo]
        lines.append(f"## {tipo} ({len(items)})")
        lines.append("")
        lines.append("| Tipo | Subtipo | Símbolo | Descripción | Trade mode |")
        lines.append("|---|---|---|---|---|")
        for r in items:
            desc = r["description"] or "—"
            sub = r["subtipo"] or "—"
            lines.append(
                f"| {r['tipo']} | {sub} | `{r['symbol']}` | {desc} | {r['trade_mode']} |"
            )
        lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "tipo",
            "subtipo",
            "symbol",
            "description",
            "path",
            "currency_base",
            "currency_profit",
            "trade_mode",
            "visible",
            "digits",
            "spread",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fieldnames})

    print("WROTE", md_path)
    print("WROTE", csv_path)
    print("TOTAL", len(rows))
    print("TYPES", {k: len(v) for k, v in sorted(by_type.items(), key=lambda x: -len(x[1]))})

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
