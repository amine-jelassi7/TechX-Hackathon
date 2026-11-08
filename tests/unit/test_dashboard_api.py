from pathlib import Path

import httpx
import pytest

from dashboard import api


def test_token_header_and_json(monkeypatch):
    monkeypatch.setenv("OWNER_TOKEN", "secret-test")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["token"] = req.headers.get("X-Owner-Token")
        seen["query"] = req.url.query.decode()
        return httpx.Response(200, json=[{"id": "a"}])

    out = api.request(
        "GET", "/owner/audit", params={"limit": 5}, transport=httpx.MockTransport(handler)
    )
    assert out == [{"id": "a"}]
    assert seen == {"token": "secret-test", "query": "limit=5"}


def test_http_error_raises_api_error():
    t = httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "bad"}))
    with pytest.raises(api.ApiError):
        api.request("GET", "/owner/kpis", transport=t)


def test_connection_error_mentions_url(monkeypatch):
    monkeypatch.setenv("API_URL", "http://127.0.0.1:1")

    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(api.ApiError, match="Backend not reachable at http://127.0.0.1:1"):
        api.request("GET", "/owner/kpis", transport=httpx.MockTransport(boom))


def test_pages_render_with_backend_down(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("API_URL", "http://127.0.0.1:1")
    at = AppTest.from_file(
        str(Path(__file__).resolve().parents[2] / "dashboard" / "app.py"), default_timeout=30
    ).run()
    assert not at.exception
    for page in [
        "Approvals",
        "Q&A manager",
        "Insights",
        "Security & Evals",
        "Simulator",
        "Live feed",
    ]:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
    assert at.error


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_tokens_meet_wcag_aa_in_both_themes():
    from dashboard.palette import PALETTES

    for mode, p in PALETTES.items():
        for surface in ("void", "panel", "panel_hi"):
            for ink in ("text", "dim", "signal_ink", "alarm_ink"):
                assert _contrast(p[ink], p[surface]) >= 4.5, (mode, ink, surface)
        assert _contrast(p["on_signal"], p["signal"]) >= 4.5, mode


def test_theme_toggle_follows_query_param_and_renders(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("API_URL", "http://127.0.0.1:1")
    path = str(Path(__file__).resolve().parents[2] / "dashboard" / "app.py")
    at = AppTest.from_file(path, default_timeout=30)
    at.query_params["theme"] = "light"
    at.run()
    assert not at.exception
    assert at.sidebar.toggle[0].value is True
    at.sidebar.toggle[0].set_value(False).run()
    assert not at.exception
    assert at.query_params["theme"] == ["dark"] or at.query_params["theme"] == "dark"
