"""Мини-апп API: валидация initData, авторизация, state/set."""
import hashlib
import hmac
import json
import time
import urllib.parse

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.webapp_api import _INIT_HEADER, validate_init_data

TOKEN = "synthetic-bot-token"


def _make_init_data(pairs: dict, token: str, *, hours_ago: float = 0.0, hash_override=None,
                    dup_hash=False):
    """initData по официальной схеме: values URL-encoded, подпись HMAC."""
    params = {k: urllib.parse.quote(str(v), safe="") for k, v in pairs.items()}
    sorted_pairs = sorted(pairs.items())
    launch = "\n".join(f"{k}={v}" for k, v in sorted_pairs)
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    good_hash = hmac.new(secret, launch.encode(), hashlib.sha256).hexdigest()
    h = hash_override if hash_override is not None else good_hash
    parts = [f"{k}={params[k]}" for k, _ in sorted_pairs]
    parts.append(f"hash={h}")
    if dup_hash:
        parts.append(f"hash={h}")
    return "&".join(parts)


USER = {"id": 13, "first_name": "Петя", "username": None, "language_code": "ru"}


def _valid(now=1_700_000_000.0):
    return _make_init_data(
        {"auth_date": int(now - 60), "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN, hours_ago=0.0)


def _fresh():
    """Подпись с auth_date у реального «сейчас» — для authorized_user_id без now=."""
    return _make_init_data(
        {"auth_date": int(time.time()) - 60, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN)


def test_valid_signature_returns_user_id():
    assert validate_init_data(_valid(), TOKEN, now=1_700_000_000.0) == 13


def test_bad_signature_rejected():
    data = _make_init_data(
        {"auth_date": 1699999940, "query_id": "q-1", "user": json.dumps(USER)},
        "wrong-token")
    assert validate_init_data(data, TOKEN, now=1_700_000_000.0) is None


def test_tampered_hash_rejected():
    data = _make_init_data(
        {"auth_date": 1699999940, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN, hash_override="deadbeef" * 8)
    assert validate_init_data(data, TOKEN, now=1_700_000_000.0) is None


def test_stale_auth_date_rejected():
    data = _make_init_data(
        {"auth_date": 1_700_000_000 - 7200, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN)
    assert validate_init_data(data, TOKEN, now=1_700_000_000.0) is None


def test_future_auth_date_rejected():
    now = 1_700_000_000.0
    data = _make_init_data(
        {"auth_date": int(now) + 300, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN)
    assert validate_init_data(data, TOKEN, now=now) is None


def test_duplicated_or_missing_hash_rejected():
    dup = _make_init_data(
        {"auth_date": 1699999940, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN, dup_hash=True)
    assert validate_init_data(dup, TOKEN, now=1_700_000_000.0) is None
    assert validate_init_data("query_id=q-1", TOKEN, now=1_700_000_000.0) is None


def _mk_handler_env(monkeypatch, tmp_path, *, admins="13", dev=False):
    monkeypatch.setattr("maxbot.webapp_api._plugin_home", lambda: tmp_path)
    monkeypatch.setenv("MAX_ASSISTANT_ADMINS", admins)
    monkeypatch.setenv("MAX_ACCESS_TOKEN", TOKEN)
    if dev:
        monkeypatch.setenv("MAX_ASSISTANT_DEV", "1")
    else:
        monkeypatch.delenv("MAX_ASSISTANT_DEV", raising=False)
    import maxbot.webapp_api as wa
    monkeypatch.setattr(wa.profile_switch, "_existing_profiles", lambda: {"default", "work"})
    return wa


def test_authorized_dev_only_from_localhost(tmp_path, monkeypatch):
    wa = _mk_handler_env(monkeypatch, tmp_path, dev=True)
    ok = wa.authorized_user_id({}, "127.0.0.1", {"dev_user_id": "13"})
    assert ok == 13
    assert wa.authorized_user_id({}, "192.168.1.5", {"dev_user_id": "13"}) is None
    assert wa.authorized_user_id({}, "127.0.0.1", {}) is None


def test_authorized_init_data_header(tmp_path, monkeypatch):
    wa = _mk_handler_env(monkeypatch, tmp_path)
    data = _fresh()
    assert wa.authorized_user_id({_INIT_HEADER: data}, "8.8.8.8", {}) == 13
    assert wa.authorized_user_id({_INIT_HEADER: "hash=ff"}, "8.8.8.8", {}) is None


def test_authorized_init_data_header_lowercase_key(tmp_path, monkeypatch):
    # браузер/WebApp может слать заголовок lowercase (HTTP/2) — lookup
    # обязан быть регистронезависимым
    wa = _mk_handler_env(monkeypatch, tmp_path)
    assert wa.authorized_user_id({"x-webapp-initdata": _fresh()}, "8.8.8.8", {}) == 13


def test_dev_bypass_rejected_behind_proxy_headers(tmp_path, monkeypatch):
    # reverse-proxy ставит X-Forwarded-For/X-Real-IP: dev-байпас с
    # remote=127.0.0.1 (как его видит aiohttp за прокси) должен быть закрыт
    wa = _mk_handler_env(monkeypatch, tmp_path, dev=True)
    assert wa.authorized_user_id(
        {"X-Forwarded-For": "203.0.113.5"}, "127.0.0.1", {"dev_user_id": "13"}) is None
    assert wa.authorized_user_id(
        {"x-real-ip": "203.0.113.5"}, "127.0.0.1", {"dev_user_id": "13"}) is None
    # без прокси-заголовков dev-байпас на localhost работает
    assert wa.authorized_user_id({}, "127.0.0.1", {"dev_user_id": "13"}) == 13


def test_admin_ids_from_env_or_allowed_users(tmp_path, monkeypatch):
    wa = _mk_handler_env(monkeypatch, tmp_path, admins="13,42")
    assert wa.admin_ids() == {13, 42}
    monkeypatch.delenv("MAX_ASSISTANT_ADMINS", raising=False)
    monkeypatch.setenv("MAX_ALLOWED_USERS", "7,13")
    assert wa.admin_ids() == {7, 13}


async def _serve(wa):
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    class _Adapter:
        pass

    app = web.Application()
    for method, path, handler in wa.build_routes(_Adapter()):
        app.router.add_route(method, path, handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_state_and_set_flow(tmp_path, monkeypatch):
    wa = _mk_handler_env(monkeypatch, tmp_path, dev=True)
    import aiohttp

    server = await _serve(wa)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(server.make_url(
                    "/max/app/state?dev_user_id=13")) as r:
                assert r.status == 200
                state = await r.json()
            assert state["me"] == {"user_id": 13, "is_admin": True}
            names = {p["name"] for p in state["profiles"]}
            assert names == {"default", "work"}
            async with http.post(server.make_url(
                    "/max/app/set?dev_user_id=13"),
                    json={"chat_id": "500", "profile": "work"}) as r:
                assert r.status == 200
                body = await r.json()
            assert body["ok"] is True
            chats = {c["chat_id"]: c for c in body["state"]["chats"]}
            assert chats["500"]["profile"] == "work"
            async with http.post(server.make_url(
                    "/max/app/set?dev_user_id=13"),
                    json={"chat_id": "500", "profile": "ghost"}) as r:
                assert r.status == 400
    finally:
        await server.close()


async def test_state_accepts_lowercase_init_data_header(tmp_path, monkeypatch):
    """Хендлер передаёт request.headers как есть (CIMultiDict): lowercase-ключ
    от реального клиента не должен теряться при dict(...)-конверсии."""
    wa = _mk_handler_env(monkeypatch, tmp_path)
    import aiohttp

    server = await _serve(wa)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(server.make_url("/max/app/state"),
                                headers={"x-webapp-initdata": _fresh()}) as r:
                assert r.status == 200
                assert (await r.json())["me"]["user_id"] == 13
    finally:
        await server.close()


async def test_non_admin_gets_403_and_dev_off(tmp_path, monkeypatch):
    wa = _mk_handler_env(monkeypatch, tmp_path, admins="13", dev=True)
    import aiohttp

    server = await _serve(wa)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(server.make_url(
                    "/max/app/state?dev_user_id=42")) as r:
                assert r.status == 403
            monkeypatch.delenv("MAX_ASSISTANT_DEV")
            # dev выключен: даже 127.0.0.1 без initData → 401
            async with http.get(server.make_url("/max/app/state")) as r:
                assert r.status == 401
            async with http.get(server.make_url(
                    "/max/app/state?dev_user_id=42")) as r:
                assert r.status == 401
    finally:
        await server.close()


async def test_static_index_and_traversal(tmp_path, monkeypatch):
    wa = _mk_handler_env(monkeypatch, tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    monkeypatch.setattr(wa, "_dist_dir", lambda: dist)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("//bundle", encoding="utf-8")
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("s3cret", encoding="utf-8")
    leak_file = tmp_path / "dist_leak.txt"
    leak_file.write_text("leak", encoding="utf-8")
    import aiohttp

    server = await _serve(wa)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(server.make_url("/max/app/")) as r:
                assert r.status == 200 and "app" in await r.text()
            async with http.get(server.make_url("/max/app/assets/app.js")) as r:
                assert r.status == 200
            # закодированный %2F доходит до хендлера (yarl не нормализует),
            # закрыт защитой → JSON 404 именно от хендлера
            async with http.get(server.make_url(
                    "/max/app/..%2Fsecret.txt")) as r:
                assert r.status == 404
                assert await r.json() == {"error": "not found"}
            # сиблинг с общим префиксом имени (prefix-обход startswith)
            async with http.get(server.make_url(
                    "/max/app/..%2Fdist_leak.txt")) as r:
                assert r.status == 404
                assert await r.json() == {"error": "not found"}
    finally:
        await server.close()


async def test_webhook_transport_serves_app_routes(tmp_path, monkeypatch):
    from maxbot.transports import WebhookTransport

    wa = _mk_handler_env(monkeypatch, tmp_path, dev=True)
    import aiohttp

    class FakeClient:
        async def subscribe(self, url, types, secret=None):
            self.subscribed = (url, types, secret)

        async def unsubscribe(self, url=None):
            pass

    fc = FakeClient()
    tr = WebhookTransport(fc, url="https://synthetic.example/hook", port=0,
                          extra_routes=wa.build_routes(None))
    await tr.start(lambda u: None)
    try:
        addr = tr._runner.addresses[0]
        base = f"http://{addr[0]}:{addr[1]}"
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{base}/max/app/state?dev_user_id=13") as r:
                assert r.status == 200
                assert (await r.json())["me"]["user_id"] == 13
    finally:
        await tr.stop()
