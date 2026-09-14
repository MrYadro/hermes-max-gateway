import asyncio
import json

import httpx
import pytest
import respx

from maxbot.max_api import MaxApiError, MaxClient, extract_message_id

BASE = "http://test.max"


def client(**kw) -> MaxClient:
    return MaxClient("TOKEN123", BASE, rps=1000.0, backoff_base=0.001, **kw)


@respx.mock
async def test_authorization_header_sent():
    route = respx.get(f"{BASE}/me").mock(return_value=httpx.Response(200, json={"user_id": 1}))
    async with client() as c:
        await c._request("GET", "/me")
    assert route.calls.last.request.headers["Authorization"] == "TOKEN123"


@respx.mock
async def test_throttle_enforces_min_interval(monkeypatch):
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, json={}))
    stamps = []

    async def fake_sleep(delay):
        stamps.append(delay)

    monkeypatch.setattr("maxbot.max_api.asyncio.sleep", fake_sleep)
    async with MaxClient("T", BASE, rps=2.0, backoff_base=0.001) as c:  # интервал 0.5с
        for _ in range(3):
            await c._request("GET", "/x")
    throttles = [d for d in stamps if d > 0.4]
    assert len(throttles) >= 2  # 2-й и 3-й запрос ждали ~0.5с


@respx.mock
async def test_retry_on_500_then_success():
    route = respx.get(f"{BASE}/me").mock(side_effect=[
        httpx.Response(500, text="boom"),
        httpx.Response(200, json={"user_id": 9}),
    ])
    async with client() as c:
        resp = await c._request("GET", "/me")
    assert resp["user_id"] == 9 and route.call_count == 2


@respx.mock
async def test_retry_after_on_429(monkeypatch):
    monkeypatch.setattr("maxbot.max_api.asyncio.sleep", asyncio.sleep)
    route = respx.get(f"{BASE}/me").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0.01"}),
        httpx.Response(200, json={"user_id": 3}),
    ])
    async with client() as c:
        resp = await c._request("GET", "/me")
    assert resp["user_id"] == 3 and route.call_count == 2


@respx.mock
async def test_4xx_raises_immediately():
    route = respx.get(f"{BASE}/me").mock(return_value=httpx.Response(401, json={"code": "auth", "message": "bad token"}))
    async with client() as c:
        with pytest.raises(MaxApiError) as ei:
            await c._request("GET", "/me")
    assert ei.value.status == 401 and ei.value.code == "auth"
    assert route.call_count == 1


@respx.mock
async def test_network_error_exhausts_retries():
    respx.get(f"{BASE}/me").mock(side_effect=httpx.ConnectError("no route"))
    async with client(retries=2) as c:
        with pytest.raises(MaxApiError) as ei:
            await c._request("GET", "/me")
    assert ei.value.status == 0 and ei.value.code == "network"


@respx.mock
async def test_send_message_returns_mid_and_format_markdown():
    route = respx.post(f"{BASE}/messages").mock(return_value=httpx.Response(
        200, json={"message": {"body": {"mid": "mid.9", "text": "x"}}}))
    async with client() as c:
        mid = await c.send_message(100, "привет **мир**")
    body = json.loads(route.calls.last.request.content)
    assert mid == "mid.9"
    assert body == {"text": "привет **мир**", "format": "markdown", "notify": True}
    assert route.calls.last.request.url.params["chat_id"] == "100"
    assert route.calls.last.request.url.params["disable_link_preview"] == "true"


@respx.mock
async def test_send_message_retries_attachment_not_ready():
    route = respx.post(f"{BASE}/messages").mock(side_effect=[
        httpx.Response(400, json={"code": "attachment.not.ready", "message": "not processed"}),
        httpx.Response(200, json={"message": {"body": {"mid": "m2"}}}),
    ])
    async with client() as c:
        mid = await c.send_message(1, "текст", attachments=[{"type": "image", "payload": {"token": "t"}}])
    assert mid == "m2" and route.call_count == 2


@respx.mock
async def test_get_updates_parses_and_returns_marker():
    upd = {"update_type": "message_created", "marker": 7,
           "message": {"body": {"mid": "m", "text": "hi"},
                       "recipient": {"chat_id": 1, "chat_type": "dialog"},
                       "sender": {"user_id": 2, "name": "N"}}}
    route = respx.get(f"{BASE}/updates").mock(return_value=httpx.Response(
        200, json={"updates": [upd], "marker": 8}))
    async with client() as c:
        marker, updates = await c.get_updates(marker=5)
    assert marker == 8 and updates[0].message.body.text == "hi"
    assert route.calls.last.request.url.params["marker"] == "5"
    assert "message_callback" in route.calls.last.request.url.params["types"]


@respx.mock
async def test_edit_and_delete_use_query_message_id():
    put = respx.put(f"{BASE}/messages").mock(return_value=httpx.Response(200, json={"success": True}))
    dele = respx.delete(f"{BASE}/messages").mock(return_value=httpx.Response(200, json={"success": True}))
    async with client() as c:
        assert await c.edit_message("mid.1", "новый текст")
        assert await c.delete_message("mid.1")
    assert put.calls.last.request.url.params["message_id"] == "mid.1"
    assert dele.calls.last.request.url.params["message_id"] == "mid.1"


@respx.mock
async def test_subscribe_sends_secret_and_types():
    route = respx.post(f"{BASE}/subscriptions").mock(return_value=httpx.Response(200, json={}))
    async with client() as c:
        await c.subscribe("https://x/hook", ["message_created"], secret="S")
    body = json.loads(route.calls.last.request.content)
    assert body == {"url": "https://x/hook", "update_types": ["message_created"], "secret": "S"}


@respx.mock
async def test_upload_flow():
    respx.post(f"{BASE}/uploads").mock(return_value=httpx.Response(200, json={"url": "https://iu.oneme.ru/u?sig=1"}))
    up = respx.post("https://iu.oneme.ru/u").mock(return_value=httpx.Response(200, json={"token": "TOK"}))
    async with client() as c:
        url = await c.get_upload_url("image")
        token = await c.upload_to_url(url, "tests/fixtures/tiny.png")
    assert token == "TOK" and "data" in str(up.calls.last.request.content)


@respx.mock
async def test_upload_photos_map_token():
    """image-хост отвечает {"photos": {<id>: {"token": ...}}} — достаём токен."""
    respx.post("https://iu.oneme.ru/u").mock(return_value=httpx.Response(
        200, json={"photos": {"abc==": {"token": "PHOTO-TOK"}}}))
    async with client() as c:
        token = await c.upload_to_url("https://iu.oneme.ru/u", "tests/fixtures/tiny.png")
    assert token == "PHOTO-TOK"


@respx.mock
async def test_upload_multipart_part_has_content_type():
    """API-хосты требуют curl-подобный multipart: per-part Content-Type обязателен."""
    route = respx.post("https://fu.oneme.ru/u").mock(
        return_value=httpx.Response(200, json={"token": "T"}))
    async with client() as c:
        await c.upload_to_url("https://fu.oneme.ru/u", "tests/fixtures/tiny.png")
    body = route.calls.last.request.content.decode("utf-8", "replace")
    assert 'Content-Disposition: form-data; name="data"; filename=' in body
    assert "Content-Type: application/octet-stream" in body


@respx.mock
async def test_upload_video_token_from_slot():
    """video/audio: токен приходит в /uploads, файловой POST возвращает retval."""
    slot = respx.post(f"{BASE}/uploads").mock(return_value=httpx.Response(
        200, json={"url": "https://omub.okcdn.ru/u", "token": "VID-TOK"}))
    respx.post("https://omub.okcdn.ru/u").mock(
        return_value=httpx.Response(200, text="<retval>1</retval>"))
    async with client() as c:
        url, hint = await c.get_upload_slot("video")
        token = await c.upload_to_url(url, "tests/fixtures/tiny.png", token_hint=hint)
    assert slot.calls.last.request.url.params["type"] == "video"
    assert token == "VID-TOK"


def test_extract_message_id_variants():
    assert extract_message_id({"message": {"body": {"mid": "m1"}}}) == "m1"
    assert extract_message_id({"message_id": "m2"}) == "m2"
    assert extract_message_id({}) is None
