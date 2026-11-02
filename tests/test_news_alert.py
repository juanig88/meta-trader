"""News alerts: RSS parse, freshness/relevance filter, model verdict parsing, fallback, text."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading.domain.news_alert import (
    DOWN, NEUTRAL, UP, Headline, Verdict, alert_text, fresh_relevant, keyword_verdict, parse_model_verdicts, parse_rss,
)

NOW = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Binance hack: $6.3B stolen</title><link>https://x/1</link>
<pubDate>Fri, 30 Oct 2026 17:50:00 +0000</pubDate><description>&lt;p&gt;Exploit hits exchange&lt;/p&gt;</description></item>
<item><title>Celebrity buys a yacht</title><link>https://x/2</link><pubDate>Fri, 30 Oct 2026 17:55:00 +0000</pubDate></item>
<item><title>Bitcoin ETF inflows hit record</title><link>https://x/3</link><pubDate>Thu, 29 Oct 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""


def test_parse_and_filter_fresh_relevant():
    items = parse_rss("coindesk", RSS)
    assert len(items) == 3 and items[0].summary == "Exploit hits exchange"
    keep = fresh_relevant(items, seen=set(), now=NOW, max_age_min=90)
    assert [h.title for h in keep] == ["Binance hack: $6.3B stolen"]  # yacht irrelevant, ETF too old
    assert fresh_relevant(items, seen={items[0].key}, now=NOW, max_age_min=90) == []


def test_keyword_fallback():
    hack = Headline("x", "Exchange hack drains wallets", "l", NOW)
    etf = Headline("x", "SEC approves new bitcoin ETF", "l2", NOW)
    assert keyword_verdict(hack).impact == DOWN
    assert keyword_verdict(etf).impact == UP
    assert keyword_verdict(Headline("x", "Bitcoin conference opens", "l3", NOW)).impact == NEUTRAL


def test_parse_model_verdicts_tolerates_fences_and_junk():
    text = '```json\n[{"id":"a","impacto":"baja","fuerza":5,"por_que":"hackeo"},' \
           '{"id":"zzz","impacto":"sube"},{"id":"b","impacto":"quizas"}]\n```'
    out = parse_model_verdicts(text, keys=["a", "b"])
    assert list(out) == ["a"] and out["a"].strength == 3 and out["a"].by_model
    assert parse_model_verdicts("no json here", keys=["a"]) == {}


def test_alert_text():
    h = Headline("decrypt", "Binance hack", "https://x/1", NOW - timedelta(minutes=5))
    txt = alert_text([(h, Verdict(h.key, DOWN, 3, "salida de fondos", True))], btc_price=77382.0)
    assert "probablemente BAJA" in txt and "77,382" in txt and "no es señal" in txt
