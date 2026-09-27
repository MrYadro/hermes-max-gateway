# MAX Mini-App «Assistants»: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Мини-апп MAX (React + MAX UI) для переключения профилей чатов и обзора профилей, с API на webhook-порту плагина и initData-авторизацией (только администратор).

**Architecture:** Роуты `/max/app/*` вешаются на aiohttp-app `WebhookTransport` (extra_routes), отдают статику из `maxbot/webapp/dist/` и JSON-API поверх `profile_switch`. Авторизация: HMAC-валидация `X-WebApp-InitData` (официальная схема MAX) + allowlist `MAX_ASSISTANT_ADMINS`/`MAX_ALLOWED_USERS`; dev-режим `?dev_user_id=` с 127.0.0.1.

**Tech Stack:** Python (aiohttp — уже в зависимостях), React 18 + TypeScript + vite + `@maxhub/max-ui` (новый npm-пакет в `maxbot/webapp/`).

**Spec:** `docs/superpowers/specs/2026-09-27-max-miniapp-assistants-design.md`

## Global Constraints

- Тесты: `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q` (из корня репо); линт: `.../.venv/bin/python -m ruff check .`
- Новое поведение — только RED→GREEN; фикстуры синтетические (chat_id 500/88, user_id 13/42)
- Коммиты на русском, conventional; ядро hermes-agent не трогаем
- PII: в тестах/репо никаких реальных id/токенов; токен бота только из scoped env
- Секреты (`MAX_ACCESS_TOKEN`, `MAX_WEBHOOK_SECRET`, `MAX_ASSISTANT_ADMINS`) читаются через `gateway.platforms._shared.get_scoped_secret`
- dist фронта коммитим; `node_modules` — в `.gitignore`

## Review Focus

1. **Битая/поддельная подпись initData** (и повторённый `hash`) → 401, никаких данных; тест в Task 2.
2. **Просроченный `auth_date`** (> 3600с) → 401 даже при валидной подписи; тест в Task 2.
3. **Не-админ с валидной подписью** → state/set дают 403; тест в Task 3.
4. **Path traversal в статике** (`/max/app/../../.env`) → 404, файлы только из dist; тест в Task 3.
5. **Dev-режим с «внешнего» адреса** → отклонён даже при MAX_ASSISTANT_DEV=1; тест в Task 3 (remote-мок).

---

### Task 1: Реестр известных чатов

**Files:**
- Modify: `maxbot/profile_switch.py`
- Modify: `maxbot/adapter.py` (`_on_message`, `_on_comment`)
- Test: `tests/test_profile_switch.py`

**Interfaces:**
- Produces:
  - `remember_chat(home: Path, chat_key: str, chat_type: str) -> None` — атомарно пишет `<home>/maxbot-chat-memory/_known_chats.json` (`{"<chat_id>": {"type": "dm"|"group"|"channel"}}`), чистит mtime-кэш
  - `known_chats(home: Path) -> Dict[str, Dict[str, str]]` — с mtime-кэшем (паттерн `load_map`)

- [ ] **Step 1: RED — тесты**

В `tests/test_profile_switch.py` добавить:

```python
def test_known_chats_roundtrip(tmp_path):
    profile_switch.remember_chat(tmp_path, "500", "channel")
    profile_switch.remember_chat(tmp_path, "88", "group")
    assert profile_switch.known_chats(tmp_path) == {
        "500": {"type": "channel"}, "88": {"type": "group"}}


def test_known_chats_type_overwrite(tmp_path):
    profile_switch.remember_chat(tmp_path, "500", "group")
    profile_switch.remember_chat(tmp_path, "500", "channel")
    assert profile_switch.known_chats(tmp_path) == {"500": {"type": "channel"}}


async def test_message_and_comment_remember_chat(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter
    from maxbot.models import parse_update

    class _Cfg:
        extra = {}

    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter._username_re = __import__("re").compile(r"(?<![\w@])@hermes_bot\b")
    col = _Col()
    adapter._message_handler = col
    await adapter._handle_update(_upd_text("привет", chat_id=77, chat_type="dialog"))
    ev = await _collect_event(adapter, parse_update({
        "update_type": "comment_created", "marker": 9,
        "message": {"body": {"mid": "cm.1", "text": "ок"},
                    "recipient": {"chat_id": 500, "post_id": "mid.777"},
                    "sender": {"user_id": 13, "name": "Петя"}, "timestamp": 1},
    }))
    assert ev is not None
    known = profile_switch.known_chats(tmp_path)
    assert known.get("77") == {"type": "dm"} and known.get("500") == {"type": "channel"}
```

- [ ] **Step 2: Run RED** — `pytest tests/test_profile_switch.py -q` → FAIL (нет `remember_chat`)

- [ ] **Step 3: Реализация**

В `maxbot/profile_switch.py` (после `set_profile`):

```python
_known_cache: Dict[Path, tuple] = {}  # home -> (mtime_ns, chats)


def _known_store(home: Path) -> Path:
    return home / "maxbot-chat-memory" / "_known_chats.json"


def known_chats(home: Path) -> Dict[str, Dict[str, str]]:
    f = _known_store(home)
    try:
        mtime = f.stat().st_mtime_ns
    except OSError:
        _known_cache.pop(home, None)
        return {}
    cached = _known_cache.get(home)
    if cached and cached[0] == mtime:
        return dict(cached[1])
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        data = {str(k): {"type": str(v.get("type") or "unknown")}
                for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        data = {}
    _known_cache[home] = (mtime, data)
    return dict(data)


def remember_chat(home: Path, chat_key: str, chat_type: str) -> None:
    chat_type = chat_type if chat_type in {"dm", "group", "channel"} else "unknown"
    f = _known_store(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    data = known_chats(home)
    data[str(chat_key)] = {"type": chat_type}
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    _known_cache.pop(home, None)
```

В `maxbot/adapter.py`:
- в `_on_message` после `chat_type = "dm" if msg.chat_type == "dialog" else "group"`:

```python
        with contextlib.suppress(Exception):
            from .profile_switch import remember_chat
            remember_chat(_plugin_home(), str(msg.chat_id), chat_type)
```

- в `_on_comment` после вычисления `channel_id` (до проверки текста):

```python
        with contextlib.suppress(Exception):
            from .profile_switch import remember_chat
            remember_chat(_plugin_home(), str(channel_id), "channel")
```

- [ ] **Step 4: GREEN + сюита** — вся сюита зелёная, ruff clean

- [ ] **Step 5: Commit** — `feat: реестр известных чатов (_known_chats.json) для UI переключалки`

---

### Task 2: Валидация initData

**Files:**
- Create: `maxbot/webapp_api.py`
- Test: `tests/test_webapp_api.py`

**Interfaces:**
- Produces: `validate_init_data(init_data: str, bot_token: str, *, max_age: int = 3600, now: Optional[float] = None) -> Optional[int]` — `user_id` при валидной подписи и свежем `auth_date`, иначе `None`

- [ ] **Step 1: RED — тесты**

Создать `tests/test_webapp_api.py`:

```python
"""Мини-апп API: валидация initData, авторизация, state/set."""
import hashlib
import hmac
import json
import urllib.parse

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.webapp_api import validate_init_data

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


def test_duplicated_or_missing_hash_rejected():
    dup = _make_init_data(
        {"auth_date": 1699999940, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN, dup_hash=True)
    assert validate_init_data(dup, TOKEN, now=1_700_000_000.0) is None
    assert validate_init_data("query_id=q-1", TOKEN, now=1_700_000_000.0) is None
```

- [ ] **Step 2: Run RED** — FAIL: нет модуля `maxbot.webapp_api`

- [ ] **Step 3: Реализация**

Создать `maxbot/webapp_api.py`:

```python
"""HTTP-API мини-аппа «Assistants»: initData-авторизация, state/set, статика."""
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable
from urllib.parse import unquote

from gateway.platforms._shared import get_scoped_secret

logger = logging.getLogger(__name__)

_INIT_HEADER = "X-WebApp-InitData"


def validate_init_data(init_data: str, bot_token: str, *, max_age: int = 3600,
                       now: Optional[float] = None) -> Optional[int]:
    """Официальная схема dev.max.ru/docs/webapps/validation.
    Возвращает user_id при валидной подписи и свежем auth_date, иначе None."""
    try:
        pairs: List[Tuple[str, str]] = []
        for chunk in (init_data or "").split("&"):
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            pairs.append((k, unquote(v)))
        hashes = [v for k, v in pairs if k == "hash"]
        if len(hashes) != 1:
            return None
        original = hashes[0]
        params = dict(pairs)
        launch = "\n".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "hash")
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, launch.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, original):
            return None
        auth_date = int(params.get("auth_date") or 0)
        ts = now if now is not None else time.time()
        if auth_date <= 0 or ts - auth_date > max_age:
            return None
        user = json.loads(params.get("user") or "{}")
        return int(user.get("id") or 0) or None
    except Exception:
        return None
```

- [ ] **Step 4: GREEN** — `pytest tests/test_webapp_api.py -q` PASS

- [ ] **Step 5: Commit** — `feat: валидация initData мини-аппа (официальная HMAC-схема MAX)`

---

### Task 3: Хендлеры state/set + статика

**Files:**
- Modify: `maxbot/webapp_api.py`
- Test: `tests/test_webapp_api.py`

**Interfaces:**
- Consumes: `profile_switch.load_map/set_profile/available_profiles/profile_exists/profile_description/known_chats`, `validate_init_data` (Task 2), `maxbot.adapter._plugin_home`
- Produces:
  - `authorized_user_id(headers: Dict[str, str], remote: str, query: Dict[str, str]) -> Optional[int]` — dev-режим (MAX_ASSISTANT_DEV + 127.0.0.1 + `dev_user_id`) или initData
  - `admin_ids() -> set[int]` — MAX_ASSISTANT_ADMINS, иначе MAX_ALLOWED_USERS
  - `build_routes(adapter) -> List[Tuple[str, str, Callable]]` — aiohttp-роуты:
    `("GET", "/max/app/state", ...)`, `("POST", "/max/app/set", ...)`,
    `("GET", "/max/app/{tail:.*}", ...)` — статика из `maxbot/webapp/dist/`

- [ ] **Step 1: RED — тесты**

Дополнить `tests/test_webapp_api.py`:

```python
def _mk_handler_env(monkeypatch, tmp_path, *, admins="13", dev=False):
    monkeypatch.setattr("maxbot.webapp_api._plugin_home", lambda: tmp_path)
    monkeypatch.setenv("MAX_ASSISTANT_ADMINS", admins)
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
    data = _valid()
    assert wa.authorized_user_id({_INIT_HEADER: data}, "8.8.8.8", {}) == 13
    assert wa.authorized_user_id({_INIT_HEADER: "hash=ff"}, "8.8.8.8", {}) is None


def test_admin_ids_from_env_or_allowed_users(tmp_path, monkeypatch):
    wa = _mk_handler_env(monkeypatch, tmp_path, admins="13,42")
    assert wa.admin_ids() == {13, 42}
    monkeypatch.delenv("MAX_ASSISTANT_ADMINS", raising=False)
    monkeypatch.setenv("MAX_ALLOWED_USERS", "7,13")
    assert wa.admin_ids() == {7, 13}
```

Интеграционные (aiohttp TestServer, dev-режим):

```python
import asyncio


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


async def test_non_admin_gets_403_and_dev_off(tmp_path, monkeypatch):
    wa = _mk_handler_env(monkeypatch, tmp_path, admins="13")
    import aiohttp

    server = await _serve(wa)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(server.make_url(
                    "/max/app/state?dev_user_id=42")) as r:
                assert r.status == 403
            # dev выключен: даже 127.0.0.1 без initData → 401
            async with http.get(server.make_url("/max/app/state")) as r:
                assert r.status == 401
    finally:
        await server.close()


async def test_static_index_and_traversal(tmp_path, monkeypatch, caplog):
    wa = _mk_handler_env(monkeypatch, tmp_path)
    dist = Path(wa.__file__).parent / "webapp" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets").mkdir(exist_ok=True)
    (dist / "assets" / "app.js").write_text("//bundle", encoding="utf-8")
    secret_file = dist.parent / "secret.txt"
    secret_file.write_text("s3cret", encoding="utf-8")
    import aiohttp

    server = await _serve(wa)
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(server.make_url("/max/app/")) as r:
                assert r.status == 200 and "app" in await r.text()
            async with http.get(server.make_url("/max/app/assets/app.js")) as r:
                assert r.status == 200
            async with http.get(server.make_url(
                    "/max/app/../secret.txt")) as r:
                assert r.status in (400, 404)
    finally:
        await server.close()
    secret_file.unlink()
```

(в начале файла уже есть `from pathlib import Path` — добавить при необходимости; `_INIT_HEADER` импортировать из модуля: `from maxbot.webapp_api import _INIT_HEADER`)

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Реализация**

Дополнить `maxbot/webapp_api.py`:

```python
def _plugin_home():
    from .adapter import _plugin_home as _home
    return _home()


def _admin_ids_from_env() -> set:
    raw = get_scoped_secret("MAX_ASSISTANT_ADMINS", "")
    if not str(raw).strip():
        raw = get_scoped_secret("MAX_ALLOWED_USERS", "")
    ids = set()
    for part in str(raw or "").replace(" ", "").split(","):
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def admin_ids() -> set:
    return _admin_ids_from_env()


def authorized_user_id(headers: Dict[str, str], remote: str, query: Dict[str, str]) -> Optional[int]:
    dev = str(get_scoped_secret("MAX_ASSISTANT_DEV", "") or "").strip().lower() in {"1", "true", "yes"}
    if dev and remote in {"127.0.0.1", "::1"}:
        raw = str(query.get("dev_user_id") or "").strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
    data = (headers or {}).get(_INIT_HEADER) or ""
    if not data:
        return None
    token = get_scoped_secret("MAX_ACCESS_TOKEN", "")
    return validate_init_data(data, token)


def _state_payload(user_id: int) -> Dict[str, Any]:
    from . import profile_switch
    home = _plugin_home()
    chats_map = profile_switch.load_map(home)
    known = profile_switch.known_chats(home)
    merged = dict(known)
    for key in chats_map:
        merged.setdefault(key, {"type": "unknown"})
    chats = [{"chat_id": k, "chat_type": v.get("type", "unknown"),
              "profile": chats_map.get(k, "default")} for k, v in sorted(merged.items())]
    profiles = [{"name": n, "description": profile_switch.profile_description(n)}
                for n in profile_switch.available_profiles()]
    return {"chats": chats, "profiles": profiles,
            "me": {"user_id": user_id, "is_admin": user_id in _admin_ids_from_env()}}


async def _handle_state(request):
    from aiohttp import web

    uid = authorized_user_id(dict(request.headers), request.remote, dict(request.query))
    if uid is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    if uid not in _admin_ids_from_env():
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response(_state_payload(uid))


async def _handle_set(request):
    from aiohttp import web

    uid = authorized_user_id(dict(request.headers), request.remote, dict(request.query))
    if uid is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    if uid not in _admin_ids_from_env():
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    chat_id = str((body or {}).get("chat_id") or "").strip()
    profile = str((body or {}).get("profile") or "").strip()
    from . import profile_switch
    if not chat_id or not profile_switch.profile_exists(profile):
        return web.json_response({"error": "unknown profile"}, status=400)
    profile_switch.set_profile(_plugin_home(), chat_id, profile)
    return web.json_response({"ok": True, "state": _state_payload(uid)})


async def _handle_static(request):
    from aiohttp import web

    dist = Path(__file__).parent / "webapp" / "dist"
    tail = request.match_info.get("tail", "")
    if not tail:
        f = dist / "index.html"
    else:
        f = (dist / tail).resolve()
        if not str(f).startswith(str(dist.resolve())):
            return web.json_response({"error": "not found"}, status=404)
    if not f.is_file():
        return web.json_response({"error": "not found"}, status=404)
    if f.name == "index.html":
        return web.FileResponse(f, headers={"Cache-Control": "no-cache"})
    return web.FileResponse(f)


def build_routes(adapter) -> List[Tuple[str, Any, Any]]:
    return [
        ("GET", "/max/app/state", _handle_state),
        ("POST", "/max/app/set", _handle_set),
        ("GET", "/max/app/{tail:.*}", _handle_static),
    ]
```

В начале файла добавить импорт `from . import profile_switch  # noqa: F401` не нужен — локальные импорты уже в функциях.

- [ ] **Step 4: GREEN + сюита**

- [ ] **Step 5: Commit** — `feat: API мини-аппа — state/set + статика, админы и dev-режим`

---

### Task 4: extra_routes в WebhookTransport + подключение

**Files:**
- Modify: `maxbot/transports.py` (`WebhookTransport.__init__`, `start`)
- Modify: `maxbot/adapter.py` (`_make_transport`)
- Test: `tests/test_webapp_api.py`

**Interfaces:**
- Consumes: `webapp_api.build_routes(adapter)` (Task 3)
- Produces: `WebhookTransport(..., extra_routes: Optional[List[Tuple[str, str, Callable]]] = None)` — роуты добавляются в app до старта

- [ ] **Step 1: RED — тест**

```python
async def test_webhook_transport_serves_app_routes(tmp_path, monkeypatch):
    from maxbot.transports import WebhookTransport

    wa = _mk_handler_env(monkeypatch, tmp_path, dev=True)
    import aiohttp

    class FakeClient:
        async def subscribe(self, url, types, secret=None):
            self.subscribed = (url, types, secret)

        async def unsubscribe(self):
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
```

- [ ] **Step 2: Run RED** — FAIL: `WebhookTransport.__init__() got an unexpected keyword argument 'extra_routes'`

- [ ] **Step 3: Реализация**

`maxbot/transports.py`, в `WebhookTransport.__init__` добавить параметр и поле:

```python
    def __init__(self, client, *, url: str, port: int, secret: Optional[str] = None,
                 path: str = "/max/webhook",
                 update_types: Optional[List[str]] = None,
                 extra_routes: Optional[List[tuple]] = None):
        ...
        self._extra_routes = extra_routes or []
```

в `start()` после `self._app.router.add_post(self._path, self._handler)`:

```python
        for method, rpath, handler in self._extra_routes:
            self._app.router.add_route(method, rpath, handler)
```

`maxbot/adapter.py`, в `_make_transport` (ветка webhook): при создании `WebhookTransport(...)` добавить:

```python
        try:
            from .webapp_api import build_routes
            extra = {"extra_routes": build_routes(self)}
        except Exception:
            extra = {}
```

и передать `**extra` в конструктор `WebhookTransport`.

- [ ] **Step 4: GREEN + сюита**

- [ ] **Step 5: Commit** — `feat: extra_routes на webhook-порту — мини-апп и API на одном ingress`

---

### Task 5: Кнопка open_app в /assistant

**Files:**
- Modify: `maxbot/adapter.py` (`_assistant_command`)
- Test: `tests/test_profile_switch.py`

**Interfaces:**
- Consumes: `get_scoped_secret("MAX_WEBHOOK_URL")`
- Produces: при заданном `MAX_WEBHOOK_URL` — после пикера (и в прямой форме) сообщение с inline-клавиатурой `[{"type": "open_app", "text": "🖥 Управлять профилями", "url": "<base>/max/app/"}]`

- [ ] **Step 1: RED — тест**

```python
async def test_assistant_sends_open_app_button(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _FC(_SendFC):
        def __init__(self):
            super().__init__()
            self.keyboards = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            if kw.get("attachments"):
                self.keyboards.append(kw["attachments"])
            return "m.1"

    monkeypatch.setattr("maxbot.adapter.get_scoped_secret",
                        lambda name, default="": "https://bot.example" if name == "MAX_WEBHOOK_URL" else default)
    inter = _InteractiveRec()
    fc = _FC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _RunnerAuth()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr(profile_switch, "_soul_text", lambda name: "")
    await adapter._handle_update(_upd_text("/assistant"))
    assert inter.calls  # пикер отправлен
    assert fc.keyboards and fc.keyboards[0][0]["payload"]["buttons"][0][0]["type"] == "open_app"
    assert fc.keyboards[0][0]["payload"]["buttons"][0][0]["url"].endswith("/max/app/")
```

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Реализация**

В `_assistant_command` в конце (после `send_choice_picker`) и в ветке прямого переключения после подтверждения:

```python
        base = str(get_scoped_secret("MAX_WEBHOOK_URL", "") or "").rstrip("/")
        if base:
            with contextlib.suppress(Exception):
                await self._client.send_message(
                    int(chat_id), "🖥 Профили и настройки:",
                    attachments=[{"type": "inline_keyboard", "payload": {"buttons": [[
                        {"type": "open_app", "text": "🖥 Управлять профилями",
                         "url": f"{base}/max/app/"}]]}}])
```

(в прямую ветку — тот же блок; вынести в локальную функцию `_send_app_link()` внутри `_assistant_command` и вызвать в обоих местах)

- [ ] **Step 4: GREEN + сюита**

- [ ] **Step 5: Commit** — `feat: /assistant шлёт кнопку open_app для запуска мини-аппа`

---

### Task 6: Фронтенд мини-аппа (vite + MAX UI)

**Files:**
- Create: `maxbot/webapp/package.json`, `maxbot/webapp/vite.config.ts`, `maxbot/webapp/tsconfig.json`, `maxbot/webapp/index.html`, `maxbot/webapp/src/main.tsx`, `maxbot/webapp/src/App.tsx`, `maxbot/webapp/src/api.ts`
- Create (сборка): `maxbot/webapp/dist/*`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: API Task 3 (`GET /max/app/state`, `POST /max/app/set`, заголовок `X-WebApp-InitData`)
- Produces: собранный `dist/` (index.html + assets), который отдаёт `_handle_static`

- [ ] **Step 1: Каркас проекта**

`maxbot/webapp/package.json`:

```json
{
  "name": "maxbot-assistants-webapp",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build"
  },
  "dependencies": {
    "@maxhub/max-ui": "^1.0.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.5.0",
    "vite": "^5.4.0"
  }
}
```

`maxbot/webapp/vite.config.ts`:

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const target = process.env.MAX_WEBHOOK_PORT || "8080";

export default defineConfig({
  base: "/max/app/",
  plugins: [react()],
  server: {
    proxy: {
      "/max/app/state": `http://localhost:${target}`,
      "/max/app/set": `http://localhost:${target}`,
    },
  },
});
```

`maxbot/webapp/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["vite/client"]
  },
  "include": ["src"]
}
```

`maxbot/webapp/index.html`:

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Assistants</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`maxbot/webapp/src/main.tsx`:

```tsx
import { createRoot } from "react-dom/client";
import { MaxUI } from "@maxhub/max-ui";
import "@maxhub/max-ui/dist/styles.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <MaxUI>
    <App />
  </MaxUI>,
);
```

`maxbot/webapp/src/api.ts`:

```typescript
export interface ChatEntry {
  chat_id: string;
  chat_type: "dm" | "group" | "channel" | "unknown";
  profile: string;
}
export interface ProfileEntry {
  name: string;
  description: string;
}
export interface StateResponse {
  chats: ChatEntry[];
  profiles: ProfileEntry[];
  me: { user_id: number; is_admin: boolean };
}

declare global {
  interface Window {
    WebApp?: { initData?: string };
  }
}

function devUserId(): string | null {
  return new URLSearchParams(window.location.search).get("dev_user_id");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = window.WebApp?.initData;
  if (initData) headers["X-WebApp-InitData"] = initData;
  const dev = devUserId();
  const url = dev ? `${path}${path.includes("?") ? "&" : "?"}dev_user_id=${dev}` : path;
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) throw new Error("Нужна авторизация: откройте приложение в MAX");
  if (res.status === 403) throw new Error("Нет доступа к управлению профилями");
  if (!res.ok) throw new Error(`Ошибка ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  state: () => request<StateResponse>("/max/app/state"),
  set: (chatId: string, profile: string) =>
    request<{ ok: boolean; state: StateResponse }>("/max/app/set", {
      method: "POST",
      body: JSON.stringify({ chat_id: chatId, profile }),
    }),
};
```

`maxbot/webapp/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Button, CellList, CellSimple, Counter, Panel, Spinner, Typography } from "@maxhub/max-ui";
import { api, ProfileEntry, StateResponse } from "./api";

const TYPE_ICONS: Record<string, string> = { dm: "👤", group: "👥", channel: "📢", unknown: "💬" };

export default function App() {
  const [state, setState] = useState<StateResponse | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"chats" | "profiles">("chats");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api.state().then(setState).catch((e) => setError(String(e.message ?? e)));

  useEffect(() => { load(); }, []);

  const pick = async (chatId: string, profile: string) => {
    setBusy(true);
    try {
      const r = await api.set(chatId, profile);
      setState(r.state);
      setOpen(null);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  if (error) return <Panel><Typography.Body>{error}</Typography.Body></Panel>;
  if (!state) return <Panel><Spinner /></Panel>;

  return (
    <Panel mode="secondary">
      <Typography.Title>Assistants</Typography.Title>
      <CellList>
        <CellSimple onClick={() => setTab("chats")}>Чаты {tab === "chats" ? "•" : ""}</CellSimple>
        <CellSimple onClick={() => setTab("profiles")}>Профили {tab === "profiles" ? "•" : ""}</CellSimple>
      </CellList>
      {tab === "chats" ? (
        <CellList>
          {state.chats.map((c) => (
            <div key={c.chat_id}>
              <CellSimple onClick={() => setOpen(open === c.chat_id ? null : c.chat_id)}>
                {TYPE_ICONS[c.chat_type] ?? "💬"} {c.chat_id}
                <Counter>{c.profile}</Counter>
              </CellSimple>
              {open === c.chat_id && (
                <CellList>
                  {state.profiles.map((p: ProfileEntry) => (
                    <Button key={p.name} disabled={busy} onClick={() => pick(c.chat_id, p.name)}>
                      {p.name === c.profile ? `✓ ${p.name}` : p.name}
                    </Button>
                  ))}
                </CellList>
              )}
            </div>
          ))}
        </CellList>
      ) : (
        <CellList>
          {state.profiles.map((p) => (
            <CellSimple key={p.name}>
              {p.name}
              <Typography.Body>{p.description || "—"}</Typography.Body>
            </CellSimple>
          ))}
        </CellList>
      )}
    </Panel>
  );
}
```

`.gitignore` — добавить:

```
maxbot/webapp/node_modules/
```

- [ ] **Step 2: Установка и smoke-сборка**

```bash
cd maxbot/webapp && npm install && npm run build
```

Expected: `dist/` создан (index.html + assets/), tsc без ошибок. Если типы `@maxhub/max-ui` не совпадают с использованными пропсами (CellSimple/Counter) — поправить под фактический API библиотеки, сохранив структуру экранов.

- [ ] **Step 3: Проверка связки локально**

- Бэкенд: `MAX_ASSISTANT_DEV=1 MAX_ASSISTANT_ADMINS=13 MAX_ACCESS_TOKEN=<synthetic> .venv/bin/python -c "from aiohttp import web; ..."` — поднять `build_routes` на порту 8080 или запустить smoke-скрипт из тестов; проще: `pytest tests/test_webapp_api.py -q` уже покрывает; ручная проверка: `cd maxbot/webapp && npm run dev`, открыть `http://localhost:5173/max/app/?dev_user_id=13` (бэкенд-дев должен быть поднят: `MAX_ASSISTANT_DEV=1 ... .venv/bin/python -m pytest`-сервер не годится — поднять разово скриптом `scripts/dev_api.py`: создать при необходимости, 15 строк, TestServer-паттерн из теста).

- [ ] **Step 4: Коммит**

```bash
git add maxbot/webapp .gitignore
git commit -m "feat: мини-апп Assistants (React+MAX UI): чаты→профиль и обзор профилей"
```

---

### Task 7: Документация и финал

**Files:**
- Modify: `README.md`, `AGENTS.md`

- [ ] **Step 1: README** — раздел «Мини-приложение Assistants»: включение (webhook-режим + `MAX_WEBHOOK_URL`, `MAX_ASSISTANT_ADMINS`), локальная разработка (`npm run dev` + `MAX_ASSISTANT_DEV=1`), сборка (`npm run build`, dist коммитится), ссылка-кнопка из `/assistant`, предупреждение про `secret=self._secret` (чек перед включением webhook).
- [ ] **Step 2: AGENTS.md** — строка в инварианты: «мини-апп `/max/app/*` на webhook-порту; авторизация initData-HMAC + MAX_ASSISTANT_ADMINS; dist коммитим».
- [ ] **Step 3: Финал** — полная сюита + ruff; `cd maxbot/webapp && npm run build` зелёная.
- [ ] **Step 4: Commit** — `docs: мини-апп Assistants — настройка, разработка, деплой`
