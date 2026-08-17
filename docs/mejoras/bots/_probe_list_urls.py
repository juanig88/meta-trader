import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}
urls = [
    "https://www.mql5.com/en/signals/mt5",
    "https://www.mql5.com/en/signals/mt5?order=growth",
    "https://www.mql5.com/en/signals/mt5?order=subscribers",
    "https://www.mql5.com/en/signals/mt5?order=equity",
    "https://www.mql5.com/en/signals/mt5?page=2",
    "https://www.mql5.com/en/signals/mt5/page2",
    "https://www.mql5.com/en/signals?terminal=mt5",
    "https://www.mql5.com/en/signals/mt5?direction=1",
    "https://www.mql5.com/en/signals/mt5?sort=2",
]
for u in urls:
    try:
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode("utf-8", "replace")
            ids = sorted({int(x) for x in re.findall(r"/signals/(\d{5,})", html)})
            print("OK", r.geturl(), "ids", len(ids), "sample", ids[:8])
            for L in sorted(set(re.findall(r'href="(/en/signals[^"]+)"', html))):
                if any(k in L for k in ("order", "page", "filter", "sort", "mt5/")):
                    print(" ", L)
    except Exception as e:
        print("FAIL", u, e)
