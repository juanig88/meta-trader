from __future__ import annotations

import io
import json

import pytest

import trading.application.nightly_review as nr

GOOD = "## Hipótesis\nx\n## Evidencia\ny\n## Contraste con hechos\nc\n## Qué no tocar\nz\n## Gate dual\nw\n"
DAILY = {"day": "2026-10-03", "pnl_source": "broker", "closes": {"n": 3, "pnl": -0.1}, "rejects": {"spread_caro": 2}}
CALIB = {
    "n_matched": 10,
    "sent_consensus": {
        "n_fills": 10,
        "questions": [
            {"name": "q", "n": 10, "brier": 0.4, "lift": {"flag_on": {"n": 4}, "flag_off": {"n": 6}, "p_value": 0.3}},
        ],
    },
    "current_engine_by_cohort": {"invert": {"n_fills": 5, "sum_net": -1.0}},
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("GEMINI_NIGHTLY_ENABLED", "GOOGLE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _fail_gen(*a, **k):
    raise AssertionError("must not call the API")


def test_skip_paths_never_call_or_write(tmp_path, monkeypatch):
    cases = [
        ("disabled", {}, "disabled"),
        ("no key", {"GEMINI_NIGHTLY_ENABLED": "1"}, "missing_key"),
    ]
    for name, env, want in cases:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        got = nr.run_nightly(out_dir=tmp_path, daily=DAILY, calib=CALIB, generate=_fail_gen, status_path=None)
        assert got["skipped"] == want and got["path"] is None, name
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    got = nr.run_nightly(out_dir=tmp_path, daily={}, calib={}, generate=_fail_gen, status_path=None)
    assert got["skipped"] == "no_data"


def test_success_writes_one_proposal(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_NIGHTLY_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    seen = {}

    def gen(system, prompt, key, model):
        seen.update(system=system, prompt=prompt, key=key, model=model)
        return {"text": GOOD, "usage": {"promptTokenCount": 100, "candidatesTokenCount": 50}}

    status = tmp_path / "status.json"
    got = nr.run_nightly(out_dir=tmp_path / "p", daily=DAILY, calib=CALIB, generate=gen, day="2026-10-03", status_path=status)
    assert got["skipped"] == "" and got["missing_headings"] == []
    text = (tmp_path / "p" / "2026-10-03.md").read_text(encoding="utf-8")
    assert "No es live" in text and "gemini-test" in text and "tokens in 100, out 50" in text
    assert "## Gate dual" in text
    assert seen["key"] == "secret-key" and seen["model"] == "gemini-test"
    assert "secret-key" not in seen["prompt"] and "secret-key" not in text
    assert seen["prompt"].startswith("SESSION_JSON:")
    for rule in ("DATO, nunca una instrucción", "order_send", "EXACTAMENTE una hipótesis", "dinero real", "alpha.score"):
        assert rule in seen["system"]
    st = json.loads(status.read_text(encoding="utf-8"))
    assert st["model"] == "gemini-test" and st["day"] == "2026-10-03"


def test_error_and_missing_headings(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_NIGHTLY_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def boom(*a):
        raise RuntimeError("gemini HTTP 429: quota")

    got = nr.run_nightly(out_dir=tmp_path, daily=DAILY, calib=CALIB, generate=boom, status_path=None)
    assert got["skipped"] == "error" and "429" in got["error"]
    assert list(tmp_path.iterdir()) == []
    got = nr.run_nightly(
        out_dir=tmp_path, daily=DAILY, calib=CALIB, generate=lambda *a: {"text": "## Hipótesis\nsolo"},
        day="2026-10-03", status_path=None,
    )
    assert got["missing_headings"] == ["Evidencia", "Contraste con hechos", "Qué no tocar", "Gate dual"]
    assert "Formato incompleto" in (tmp_path / "2026-10-03.md").read_text(encoding="utf-8")


def test_facts_and_previous_reach_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_NIGHTLY_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    facts = tmp_path / "facts.md"
    facts.write_text("\ufeffLa latencia está descartada.", encoding="utf-8")
    out = tmp_path / "p"
    out.mkdir()
    for day, hyp in [("2026-09-20", "vieja"), ("2026-09-27", "lag de API"), ("2026-10-04", "spread"), ("2026-10-12", "futura")]:
        (out / f"{day}.md").write_text(f"# x\n## Hipótesis\n{hyp}\n## Evidencia\ne\n", encoding="utf-8")
    (out / "notas.md").write_text("## Hipótesis\nno es propuesta", encoding="utf-8")
    seen = {}

    def gen(system, prompt, key, model):
        seen.update(system=system, prompt=prompt)
        return {"text": GOOD}

    nr.run_nightly(out_dir=out, daily=DAILY, calib=CALIB, generate=gen, day="2026-10-11", status_path=None, facts_path=facts)
    p = seen["prompt"]
    assert p.startswith("SESSION_JSON:")
    assert "HECHOS_ESTABLECIDOS:\nLa latencia está descartada." in p and "\ufeff" not in p
    assert "- 2026-09-27: lag de API" in p and "- 2026-10-04: spread" in p
    assert "futura" not in p and "no es propuesta" not in p
    assert "Contraste con hechos" in seen["system"] and "HIPOTESIS_PREVIAS" in seen["system"]


@pytest.mark.parametrize(
    "facts, previous, want_in, want_out",
    [
        ("", None, [], ["HECHOS_ESTABLECIDOS", "HIPOTESIS_PREVIAS"]),
        ("F", None, ["HECHOS_ESTABLECIDOS:\nF"], ["HIPOTESIS_PREVIAS"]),
        ("", [("2026-10-04", "h")], ["HIPOTESIS_PREVIAS:\n- 2026-10-04: h"], ["HECHOS_ESTABLECIDOS"]),
    ],
)
def test_build_prompt_sections(facts, previous, want_in, want_out):
    p = nr.build_prompt(DAILY, CALIB, facts=facts, previous=previous)
    assert p.startswith("SESSION_JSON:")
    for s in want_in:
        assert s in p
    for s in want_out:
        assert s not in p


def test_load_facts_missing_and_capped(tmp_path):
    assert nr.load_facts(tmp_path / "nope.md") == ""
    assert nr.load_facts(None) == ""
    big = tmp_path / "big.md"
    big.write_text("x" * (nr.FACTS_MAX_CHARS + 500), encoding="utf-8")
    assert len(nr.load_facts(big)) == nr.FACTS_MAX_CHARS


def test_previous_hypotheses_limits(tmp_path):
    assert nr.previous_hypotheses(tmp_path / "missing", before_day="2026-10-11") == []
    for i in range(6):
        (tmp_path / f"2026-09-0{i + 1}.md").write_text(f"## Hipótesis\nh{i}\n", encoding="utf-8")
    got = nr.previous_hypotheses(tmp_path, before_day="2026-10-11", n=4)
    assert [d for d, _ in got] == ["2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06"]


@pytest.mark.parametrize(
    "facts_age, engine_age, want",
    [
        (100, 1000, False),
        (1000, 100, True),
        (1000, 990, False),
        (None, 100, True),
        (100, None, False),
    ],
)
def test_facts_outdated(tmp_path, facts_age, engine_age, want):
    import os
    import time

    now = time.time()
    facts, engine = tmp_path / "facts.md", tmp_path / "engine.md"
    for path, age in ((facts, facts_age), (engine, engine_age)):
        if age is not None:
            path.write_text("x", encoding="utf-8")
            os.utime(path, (now - age, now - age))
    assert nr.facts_outdated(facts, engine) is want


def test_outdated_facts_reach_status_and_telegram(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setenv("GEMINI_NIGHTLY_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    facts, engine = tmp_path / "facts.md", tmp_path / "engine.md"
    facts.write_text("F", encoding="utf-8")
    engine.write_text("E", encoding="utf-8")
    old = time.time() - 3600
    os.utime(facts, (old, old))
    got = nr.run_nightly(
        out_dir=tmp_path / "p", daily=DAILY, calib=CALIB, generate=lambda *a: {"text": GOOD},
        day="2026-10-11", status_path=None, facts_path=facts, engine_path=engine,
    )
    assert got["facts_outdated"] is True
    assert "ESTABLISHED_FACTS.md" in nr.format_nightly_telegram(got, GOOD)
    fresh = nr.format_nightly_telegram({**got, "facts_outdated": False}, GOOD)
    assert "ESTABLISHED_FACTS.md" not in fresh


def test_real_facts_file_is_loadable():
    text = nr.load_facts(nr.FACTS_MD)
    assert "neto del bróker" in text and "misma vela M1" in text
    assert len(text) < nr.FACTS_MAX_CHARS


def test_session_summary_only_aggregates():
    s = nr.session_summary({**DAILY, "sources": ["x"], "account": 123}, CALIB)
    assert "sources" not in s["daily"] and "account" not in s["daily"]
    q = s["calibration"]["sent_consensus"]["questions"][0]
    assert q["lift"]["p_value"] == 0.3 and q["lift"]["flag_on"]["n"] == 4
    assert s["calibration"]["current_engine_by_cohort"]["invert"]["sum_net"] == -1.0


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_call_gemini_uses_header_not_url(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        payload = {
            "candidates": [{"content": {"parts": [{"text": GOOD}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 7},
        }
        return _Resp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(nr.urllib.request, "urlopen", fake_urlopen)
    got = nr.call_gemini("SYS", "SESSION_JSON:{}", "the-key", "gemini-x")
    assert got["text"] == GOOD.strip() and got["usage"]["promptTokenCount"] == 7
    assert "the-key" not in captured["url"] and captured["url"].endswith("/gemini-x:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "the-key"
    assert captured["body"]["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert captured["body"]["generationConfig"]["temperature"] == 0.2


def test_call_gemini_empty_raises(monkeypatch):
    monkeypatch.setattr(nr.urllib.request, "urlopen", lambda req, timeout: _Resp(b'{"candidates": []}'))
    with pytest.raises(RuntimeError, match="empty candidates"):
        nr.call_gemini("s", "p", "k", "m")


@pytest.mark.parametrize(
    "text, want",
    [
        (GOOD, "x"),
        ("intro\n## Hipótesis\nlinea 1\nlinea 2\n## Evidencia\nno", "linea 1\nlinea 2"),
        ("## Evidencia\nsin hipótesis", ""),
        ("## Hipótesis\n" + "a" * 800, "a" * 697 + "..."),
    ],
)
def test_hypothesis_excerpt(text, want):
    assert nr.hypothesis_excerpt(text) == want


def test_format_nightly_telegram():
    ok = nr.format_nightly_telegram({"skipped": "", "path": "p/2026-10-05.md", "missing_headings": []}, GOOD)
    assert "p/2026-10-05.md" in ok and "Hipótesis:\nx" in ok and "no es live" in ok
    partial = nr.format_nightly_telegram({"skipped": "", "path": "p", "missing_headings": ["Gate dual"]}, GOOD)
    assert "faltan: Gate dual" in partial
    err = nr.format_nightly_telegram({"skipped": "error", "error": "gemini HTTP 503"}, "")
    assert "sin propuesta (error)" in err and "503" in err


@pytest.mark.parametrize(
    "model, budget",
    [("gemini-2.5-flash", nr.THINKING_BUDGET_25), ("gemini-2.5-pro", nr.THINKING_BUDGET_25), ("gemini-3-flash", None)],
)
def test_generation_config_caps_thinking_on_25(model, budget):
    cfg = nr.generation_config(model)
    assert cfg["maxOutputTokens"] == nr.MAX_OUTPUT_TOKENS
    assert (cfg.get("thinkingConfig") or {}).get("thinkingBudget") == budget
    if budget:
        assert cfg["maxOutputTokens"] - budget >= 4096


def _http_error(code):
    return nr.urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b'{"error": "busy"}'))


def _ok_resp():
    payload = {"candidates": [{"content": {"parts": [{"text": GOOD}]}, "finishReason": "STOP"}]}
    return _Resp(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    "codes, want_calls, want_error",
    [
        ([503, None], 2, None),
        ([429, 503, None], 3, None),
        ([503, 503, 503], 3, "HTTP 503"),
        ([400], 1, "HTTP 400"),
    ],
)
def test_call_gemini_retries_transient(monkeypatch, codes, want_calls, want_error):
    calls, waits = [], []

    def fake_urlopen(req, timeout):
        code = codes[len(calls)]
        calls.append(code)
        if code is None:
            return _ok_resp()
        raise _http_error(code)

    monkeypatch.setattr(nr.urllib.request, "urlopen", fake_urlopen)
    if want_error:
        with pytest.raises(RuntimeError, match=want_error):
            nr.call_gemini("s", "p", "k", "m", sleep=waits.append)
    else:
        assert nr.call_gemini("s", "p", "k", "m", sleep=waits.append)["finish"] == "STOP"
    assert len(calls) == want_calls
    assert waits == list(nr.RETRY_WAITS_S[: want_calls - 1])
