"""Tests for simdrive.wda.client — WDA HTTP client.

All tests use httpx.MockTransport so no real network is ever contacted.
Coverage target: 100% of client.py.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_transport(responses: list[tuple[int, Any]]) -> httpx.MockTransport:
    """Return a MockTransport that replays the given (status_code, body) list."""
    queue = list(responses)

    def _handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        status, body = queue.pop(0)
        if isinstance(body, (dict, list)):
            content = json.dumps(body).encode()
            headers = {"content-type": "application/json"}
        else:
            content = str(body).encode()
            headers = {"content-type": "text/plain"}
        return httpx.Response(status, content=content, headers=headers)

    return httpx.MockTransport(_handler)


def _ok(**kwargs) -> dict:
    return {"value": kwargs}


def _make_client(responses: list[tuple[int, Any]], host: str = "localhost", port: int = 8100):
    from simdrive.wda.client import WdaClient
    client = WdaClient(host=host, port=port)
    client._replace_transport(_make_transport(responses))
    return client


# ── status ────────────────────────────────────────────────────────────────────


def test_status_returns_dict():
    body = {"value": {"ready": True, "os": {"name": "iOS", "version": "26.0"}}}
    client = _make_client([(200, body)])
    result = client.status()
    assert result["value"]["ready"] is True


def test_status_raises_on_http_error():
    from simdrive.errors import SimdriveError
    client = _make_client([(500, "Internal Server Error")])
    with pytest.raises(SimdriveError) as exc:
        client.status()
    assert exc.value.code == "wda_http_error"
    assert "Recovery:" in exc.value.message


def test_status_raises_wda_unreachable():
    """TransportError → wda_unreachable."""
    from simdrive.errors import SimdriveError

    def _failing(request):
        raise httpx.ConnectError("refused")

    from simdrive.wda.client import WdaClient
    client = WdaClient(host="localhost", port=8100)
    client._replace_transport(httpx.MockTransport(_failing))
    with pytest.raises(SimdriveError) as exc:
        client.status()
    assert exc.value.code == "wda_unreachable"
    assert "Recovery:" in exc.value.message


# ── open_session ─────────────────────────────────────────────────────────────


def test_open_session_returns_session_id():
    body = {"value": {"sessionId": "abc123", "capabilities": {}}}
    client = _make_client([(200, body)])
    sid = client.open_session("com.example.app")
    assert sid == "abc123"
    assert client._session_id == "abc123"


def test_open_session_fallback_key():
    """WDA sometimes returns sessionId at top level."""
    body = {"sessionId": "fallback-id", "value": {}}
    client = _make_client([(200, body)])
    sid = client.open_session("com.example.app")
    assert sid == "fallback-id"


def test_open_session_raises_if_no_session_id():
    from simdrive.errors import SimdriveError
    body = {"value": {"error": "no session"}}
    client = _make_client([(200, body)])
    with pytest.raises(SimdriveError) as exc:
        client.open_session("com.example.app")
    assert exc.value.code == "wda_session_open_failed"
    assert "Recovery:" in exc.value.message


# ── tap ──────────────────────────────────────────────────────────────────────


def test_tap_succeeds():
    client = _make_client([(200, {"value": {"sessionId": "s1"}}), (200, _ok())])
    client.open_session("com.app")
    client.tap(100.0, 200.0)


def test_tap_raises_without_open_session():
    from simdrive.errors import SimdriveError
    client = _make_client([])
    with pytest.raises(SimdriveError) as exc:
        client.tap(0, 0)
    assert exc.value.code == "wda_session_not_open"
    assert "Recovery:" in exc.value.message


def test_tap_propagates_http_error():
    from simdrive.errors import SimdriveError
    client = _make_client([
        (200, {"value": {"sessionId": "s1"}}),
        (422, "unprocessable"),
    ])
    client.open_session("com.app")
    with pytest.raises(SimdriveError) as exc:
        client.tap(50.0, 50.0)
    assert exc.value.code == "wda_http_error"


# ── swipe ─────────────────────────────────────────────────────────────────────


def test_swipe_succeeds():
    client = _make_client([
        (200, {"value": {"sessionId": "s2"}}),
        (200, _ok()),
    ])
    client.open_session("com.app")
    client.swipe(10.0, 20.0, 100.0, 200.0, duration_ms=500)


def test_swipe_default_duration():
    client = _make_client([
        (200, {"value": {"sessionId": "s3"}}),
        (200, _ok()),
    ])
    client.open_session("com.app")
    # duration_ms defaults to 300
    client.swipe(0, 0, 100, 100)


# ── type_text ─────────────────────────────────────────────────────────────────


def test_type_text_sends_chars_as_list():
    """type_text must POST value=[list of chars]."""
    captured: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/wda/keys" in str(request.url):
            body = json.loads(request.content)
            captured.append(body)
        return httpx.Response(200, json=_ok())

    from simdrive.wda.client import WdaClient
    client = WdaClient("localhost", 8100)
    client._replace_transport(httpx.MockTransport(_handler))
    client._session_id = "s4"
    client.type_text("hi")
    assert captured[0]["value"] == ["h", "i"]


def test_type_text_empty_string_still_calls_api():
    client = _make_client([
        (200, {"value": {"sessionId": "s5"}}),
        (200, _ok()),
    ])
    client.open_session("com.app")
    client.type_text("")  # empty — sends []


# ── press_key ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key,expected", [
    ("home", "home"),
    ("volumeUp", "volumeUp"),
    ("volumeDown", "volumeDown"),
    ("power", "power"),
    ("lock", "power"),  # alias
])
def test_press_key_maps_correctly(key, expected):
    captured: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if "/wda/pressButton" in str(request.url):
            captured.append(json.loads(request.content))
        return httpx.Response(200, json=_ok())

    from simdrive.wda.client import WdaClient
    client = WdaClient("localhost", 8100)
    client._replace_transport(httpx.MockTransport(_handler))
    client._session_id = "s6"
    client.press_key(key)
    assert captured[0]["name"] == expected


def test_press_key_unknown_raises():
    from simdrive.errors import SimdriveError
    from simdrive.wda.client import WdaClient
    client = WdaClient("localhost", 8100)
    client._session_id = "s7"
    with pytest.raises(SimdriveError) as exc:
        client.press_key("banana")
    assert exc.value.code == "wda_unknown_button"
    assert "Recovery:" in exc.value.message


# ── clear_field ───────────────────────────────────────────────────────────────


def test_clear_field_uses_active_element():
    """clear_field must call GET /element/active then POST /element/<id>/clear."""
    calls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        calls.append(f"{request.method} {path}")
        if "element/active" in path:
            return httpx.Response(200, json={"value": {"ELEMENT": "elem42"}})
        return httpx.Response(200, json=_ok())

    from simdrive.wda.client import WdaClient
    client = WdaClient("localhost", 8100)
    client._replace_transport(httpx.MockTransport(_handler))
    client._session_id = "s8"
    client.clear_field()
    assert any("element/active" in c for c in calls)
    assert any("elem42" in c and "clear" in c for c in calls)


def test_clear_field_fallback_when_no_active_element():
    """If no element-id in response, clear_field must fall back to wda/keys."""
    calls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        calls.append(f"{request.method} {path}")
        if "element/active" in path:
            return httpx.Response(200, json={"value": {}})
        return httpx.Response(200, json=_ok())

    from simdrive.wda.client import WdaClient
    client = WdaClient("localhost", 8100)
    client._replace_transport(httpx.MockTransport(_handler))
    client._session_id = "s9"
    client.clear_field()
    assert any("/wda/keys" in c for c in calls)


def test_clear_field_uses_alternate_element_key():
    """WDA 3+ uses the W3C element key 'element-6066-...'."""
    calls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        calls.append(f"{request.method} {path}")
        if "element/active" in path:
            return httpx.Response(200, json={
                "value": {"element-6066-11e4-a52e-4f735466cecf": "w3c-elem"}
            })
        return httpx.Response(200, json=_ok())

    from simdrive.wda.client import WdaClient
    client = WdaClient("localhost", 8100)
    client._replace_transport(httpx.MockTransport(_handler))
    client._session_id = "s10"
    client.clear_field()
    assert any("w3c-elem" in c and "clear" in c for c in calls)


# ── screenshot ────────────────────────────────────────────────────────────────


def test_screenshot_decodes_base64():
    png_bytes = b"\x89PNG fake"
    b64_str = base64.b64encode(png_bytes).decode()
    client = _make_client([
        (200, {"value": {"sessionId": "s11"}}),
        (200, {"value": b64_str}),
    ])
    client.open_session("com.app")
    result = client.screenshot()
    assert result == png_bytes


def test_screenshot_without_session_raises():
    from simdrive.errors import SimdriveError
    client = _make_client([])
    with pytest.raises(SimdriveError) as exc:
        client.screenshot()
    assert exc.value.code == "wda_session_not_open"


# ── delete_session ────────────────────────────────────────────────────────────


def test_delete_session_clears_session_id():
    client = _make_client([
        (200, {"value": {"sessionId": "s12"}}),
        (200, _ok()),
    ])
    client.open_session("com.app")
    assert client._session_id == "s12"
    client.delete_session()
    assert client._session_id is None


def test_delete_session_noop_if_no_session():
    client = _make_client([])
    # Should not raise
    client.delete_session()


def test_delete_session_swallows_errors():
    """delete_session is best-effort; HTTP errors should not propagate."""
    client = _make_client([(500, "server gone")])
    client._session_id = "stale"
    client.delete_session()  # must not raise


# ── check_alive ───────────────────────────────────────────────────────────────


def test_check_alive_ok():
    body = {"value": {"ready": True}}
    client = _make_client([(200, body)])
    client.check_alive("TEST-UDID")  # must not raise


def test_check_alive_raises_session_lost():
    from simdrive.errors import SimdriveError

    def _failing(request):
        raise httpx.ConnectError("refused")

    from simdrive.wda.client import WdaClient
    client = WdaClient("localhost", 8100)
    client._replace_transport(httpx.MockTransport(_failing))
    with pytest.raises(SimdriveError) as exc:
        client.check_alive("DEAD-UDID")
    assert exc.value.code == "wda_session_lost"
    assert "Recovery:" in exc.value.message


# ── close ─────────────────────────────────────────────────────────────────────


def test_close_does_not_raise():
    client = _make_client([])
    client.close()
