import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


STATE_PATH = Path("bot_state.json")


def get_bot_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def render_page(state: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    running = bool(state.get("running"))
    mode = state.get("mode", "n/a")
    pnl_today = state.get("pnl_today", 0.0)

    pnl_color = "#16a34a" if isinstance(pnl_today, (int, float)) and pnl_today >= 0 else "#dc2626"
    running_color = "#16a34a" if running else "#dc2626"

    symbols = state.get("symbols", [])
    last_trade_time = state.get("last_trade_time", {})

    rows = []
    for sym in symbols:
        t = last_trade_time.get(sym, "—")
        rows.append(
            f"<tr><td>{_html_escape(str(sym))}</td><td>{_html_escape(str(t))}</td></tr>"
        )

    rows_html = "\n".join(rows) if rows else "<tr><td colspan='2'>No data</td></tr>"

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="refresh" content="5" />
    <title>MT5 Bot Dashboard</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; background: #0b1220; color: #e5e7eb; }}
      .card {{ background: #0f172a; border: 1px solid #1f2a44; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
      .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
      h1 {{ margin: 0 0 12px 0; font-size: 18px; }}
      h2 {{ margin: 0 0 10px 0; font-size: 14px; color: #cbd5e1; }}
      .kv {{ display: grid; grid-template-columns: 200px 1fr; gap: 8px 12px; }}
      .muted {{ color: #94a3b8; }}
      table {{ width: 100%; border-collapse: collapse; }}
      td, th {{ padding: 10px 8px; border-bottom: 1px solid #1f2a44; text-align: left; }}
      .pill {{ display: inline-block; padding: 2px 10px; border-radius: 999px; background: #111827; border: 1px solid #1f2a44; }}
      a {{ color: #93c5fd; text-decoration: none; }}
    </style>
  </head>
  <body>
    <div class="card">
      <h1>MT5 Bot Dashboard</h1>
      <div class="muted">Última actualización: {now} (auto refresh 5s)</div>
      <div class="muted" style="margin-top:8px;">
        Backtests / scans / métricas en dinero: usá <code>py .\\flask_dashboard.py</code> y abrí <code>/backtests</code>.
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>🤖 Estado del bot</h2>
        <div class="kv">
          <div class="muted">Running</div><div style="color:{running_color}">{running}</div>
          <div class="muted">Mode</div><div><span class="pill">{_html_escape(str(mode))}</span></div>
          <div class="muted">PnL hoy</div><div style="color:{pnl_color}">{_html_escape(str(pnl_today))}</div>
          <div class="muted">Open positions</div><div>{_html_escape(str(state.get("open_positions_total", "n/a")))}</div>
          <div class="muted">Daily loss limit</div><div>{_html_escape(str(state.get("daily_loss_limit", "n/a")))}</div>
          <div class="muted">Limit reached</div><div>{_html_escape(str(state.get("daily_loss_limit_reached", "n/a")))}</div>
        </div>
      </div>

      <div class="card">
        <h2>⏱️ Cooldown / last trade</h2>
        <table>
          <thead><tr><th>Symbol</th><th>Last trade time</th></tr></thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h2>ℹ️ Tips</h2>
      <div class="muted">
        - El dashboard lee <code>bot_state.json</code> (lo escribe el bot).<br/>
        - Logs estructurados quedan en <code>trades.log</code>.
      </div>
    </div>
  </body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return

        state = get_bot_state()
        body = render_page(state).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = "127.0.0.1"
    port = 8000
    httpd = HTTPServer((host, port), Handler)
    print(f"Dashboard running on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()

