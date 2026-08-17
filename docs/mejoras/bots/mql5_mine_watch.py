"""Re-corre pattern mining cada N minutos mientras crece positions_csv/."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSV_DIR = HERE / "mql5_crawl" / "positions_csv"


def main() -> int:
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    last_n = -1
    while True:
        n = len(list(CSV_DIR.glob("*.csv"))) if CSV_DIR.exists() else 0
        if n != last_n and n > 0:
            print(f"[{time.strftime('%H:%M:%S')}] csvs={n} -> mining")
            subprocess.run(
                [sys.executable, "-u", str(HERE / "mql5_pattern_mining.py"), "--min-trades", "60"],
                cwd=str(HERE.parent.parent.parent),
                check=False,
            )
            last_n = n
        else:
            print(f"[{time.strftime('%H:%M:%S')}] csvs={n} (no change)")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
