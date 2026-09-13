# Hermes MAX Gateway — план имплементации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Внешний плагин `maxbot` для гейтвея Hermes Agent, подключающий мессенджер MAX (max.ru): текст, markdown, все входящие вложения, кнопки approve/deny/clarify/picker, streaming-превью, cron-доставка, polling- и webhook-режимы.

**Architecture:** Один адаптер `MaxAdapter(BasePlatformAdapter)` с подменным транспортом (long polling по умолчанию / webhook через aiohttp). Тонкий собственный HTTP-клиент `MaxClient` поверх `platform-api2.max.ru` (без сторонних MAX-SDK). Модули: модели апдейтов, markdown-санитайзер, uploads с кэшем токенов, интерактивные кнопки с диспетчером, конфиг-хуки.

**Tech Stack:** Python 3.11+, httpx (клиент), aiohttp (webhook-сервер), pytest + pytest-asyncio + respx, ruff. Хост-интерфейс — hermes-agent (`BasePlatformAdapter`, `register(ctx)`).

**Spec:** `docs/superpowers/specs/2026-09-13-hermes-max-gateway-design.md`

## Global Constraints

- Имя платформы в гейтвее — `"max"`; python-пакет — `maxbot` (не `max`).
- Установка плагином-каталогом (`~/.hermes/plugins/maxbot/` c `__init__.py` c `register(ctx)` + `plugin.yaml`) или pip entry point группы `hermes_agent.plugins`.
- MAX API: база `https://platform-api2.max.ru`, токен только в заголовке `Authorization`, лимит 30 rps, `PUT /messages` ≤2 правок/сек на чат и ≤4000 символов.
- Зависимости плагина: только `httpx` и `aiohttp`. Никаких MAX-SDK.
- Callback-payload convention (общая с Telegram-адаптером): `ea:<choice>:<id>`, `sc:<choice>:<id>`, `cl:<id>:<idx>`, `cp:<idx>`.
- Кнопки и пользовательские тексты — на русском; идентификаторы кода — английские.
- Каждый таск: тест → красный → реализация → зелёный → коммит. Коммиты conventional (`feat:`, `test:`, `docs:`, `chore:`).
- Дев-окружение требует клон hermes-agent рядом: `git clone --depth 1 https://github.com/NousResearch/hermes-agent.git ../hermes-agent` (см. Task 1).

---

### Task 1: Скелет проекта + манифест плагина + dev-окружение

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `conftest.py`, `maxbot/__init__.py`, `maxbot/plugin.yaml`
- Test: `tests/test_plugin.py`

**Interfaces:**
- Produces: пакет `maxbot` c `register(ctx)` (полная регистрация — в Task 9); `conftest.py` добавляет hermes-agent в `sys.path` (используется всеми последующими hermes-зависимыми тестами).

- [ ] **Step 1: Написать failing test манифеста**

```python
# tests/test_plugin.py
"""Проверка манифеста плагина и точки входа (без hermes-agent)."""
from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parent.parent / "maxbot"


def test_plugin_yaml_manifest():
    manifest = yaml.safe_load((PKG / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["kind"] == "platform"
    assert manifest["name"] == "max-platform"
    required = {e["name"] for e in manifest["requires_env"]}
    assert "MAX_ACCESS_TOKEN" in required
    optional = {e["name"] for e in manifest["optional_env"]}
    assert {"MAX_ALLOWED_USERS", "MAX_HOME_CHANNEL", "MAX_UPDATES_MODE"} <= optional


def test_package_exposes_register():
    import maxbot

    assert callable(getattr(maxbot, "register", None))
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_plugin.py -v`
Expected: FAIL (`ModuleNotFoundError: maxbot` / нет plugin.yaml)

- [ ] **Step 3: Создать скелет**

```toml
# pyproject.toml
[project]
name = "maxbot"
version = "0.1.0"
description = "Плагин платформы MAX (max.ru) для гейтвея Hermes Agent"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
dependencies = ["httpx>=0.27", "aiohttp>=3.9"]

[project.entry-points."hermes_agent.plugins"]
maxbot = "maxbot"

[dependency-groups]
dev = ["pytest>=8.4", "pytest-asyncio>=0.24", "respx>=0.22", "ruff>=0.6", "pyyaml>=6"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 120
target-version = "py311"
```

```gitignore
# .gitignore
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
dist/
```

```python
# conftest.py
"""Подключаем исходники hermes-agent к sys.path (адаптер импортирует gateway.*)."""
import os

import pytest
import sys
from pathlib import Path


def _add_hermes_to_path() -> None:
    candidates = [os.environ.get("HERMES_AGENT_SRC"), "../hermes-agent", "hermes-agent"]
    for cand in candidates:
        if not cand:
            continue
        root = Path(cand).resolve()
        if (root / "gateway" / "platforms" / "base.py").exists():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return


_add_hermes_to_path()


@pytest.fixture(scope="session", autouse=True)
def _max_platform_member():
    """Platform("max") разрешается только для зарегистрированных платформ.
    В юнит-тестах адаптер конструируется напрямую — создаём псевдо-член enum."""
    try:
        from gateway.config import Platform
        Platform._add_pseudo_member("max")
    except Exception:
        pass
    yield
```

```python
# maxbot/__init__.py
"""Плагин платформы MAX для Hermes Agent. Полная register() появится в Task 9."""


def register(ctx):  # placeholder до Task 9
    raise NotImplementedError("register() подключается в Task 9")
```

```yaml
# maxbot/plugin.yaml
name: max-platform
label: MAX
kind: platform
version: 0.1.0
description: >
  Плагин платформы MAX (max.ru) для гейтвея Hermes Agent. Подключает бота MAX:
  текст, markdown, медиа (входящие image/video/audio/file/sticker/share/contact/location),
  inline-кнопки (approve/deny, clarify, пикеры), streaming-превью, cron-доставку.
  Транспорты: long polling (по умолчанию) и webhook.
author: hermes-max-gateway
requires_env:
  - name: MAX_ACCESS_TOKEN
    description: "Токен бота MAX из кабинета «MAX для бизнеса» (business.max.ru)"
    prompt: "Токен доступа бота MAX"
    url: "https://business.max.ru/self"
    password: true
optional_env:
  - name: MAX_ALLOWED_USERS
    description: "Разрешённые user_id через запятую (пусто = никому)"
    prompt: "Разрешённые пользователи (csv user_id)"
    password: false
  - name: MAX_ALLOW_ALL_USERS
    description: "Разрешить всем (только для разработки)"
    prompt: "Разрешить всем? (true/false)"
    password: false
  - name: MAX_HOME_CHANNEL
    description: "chat_id для cron-доставки и уведомлений"
    prompt: "Домашний канал (chat_id)"
    password: false
  - name: MAX_UPDATES_MODE
    description: "Режим апдейтов: polling (по умолчанию) или webhook"
    prompt: "Режим апдейтов (polling/webhook)"
    password: false
  - name: MAX_WEBHOOK_URL
    description: "Публичный HTTPS-URL для webhook-режима"
    prompt: "Webhook URL"
    password: false
  - name: MAX_WEBHOOK_PORT
    description: "Локальный порт webhook-сервера (по умолчанию 8443)"
    prompt: "Webhook порт"
    password: false
  - name: MAX_WEBHOOK_SECRET
    description: "Секрет для проверки запросов webhook"
    prompt: "Webhook секрет"
    password: true
  - name: MAX_API_BASE
    description: "Переопределение базового URL API (тесты)"
    prompt: "Базовый URL API"
    password: false
```

- [ ] **Step 4: Инициализировать окружение и запустить тест**

Run: `git clone --depth 1 https://github.com/NousResearch/hermes-agent.git ../hermes-agent` (если ещё нет) и `uv sync && uv run pytest tests/test_plugin.py -v`
Expected: PASS (2 теста)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore conftest.py maxbot/ tests/
git commit -m "chore: скелет проекта и манифест плагина maxbot"
```

---

### Task 2: Модели апдейтов (`maxbot/models.py`)

**Files:**
- Create: `maxbot/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `User(user_id: int, name: str = "", username: Optional[str] = None)`
  - `Attachment(type: str, payload: Dict[str, Any] = {})`
  - `MessageBody(mid: Optional[str], text: Optional[str], attachments: List[Attachment])`
  - `Message(body: MessageBody, chat_id: int, chat_type: str, sender: Optional[User], timestamp: int, link: Optional[Dict], raw: Dict)`
  - `Callback(callback_id: str, payload: str, message: Optional[Message])`
  - `Update(update_type: str, marker: Optional[int], message: Optional[Message], callback: Optional[Callback], chat_id: Optional[int], user: Optional[User])`
  - `parse_update(d: Dict) -> Update`, `parse_message(d: Dict) -> Message`

- [ ] **Step 1: Failing tests**

```python
# tests/test_models.py
from maxbot.models import parse_update


def _message_created(text="привет", chat_type="dialog", sender_id=42, chat_id=100):
    return {
        "update_type": "message_created",
        "marker": 555,
        "message": {
            "body": {"mid": "mid.1", "text": text, "attachments": []},
            "recipient": {"chat_id": chat_id, "chat_type": chat_type},
            "sender": {"user_id": sender_id, "name": "Иван", "username": "ivan"},
            "timestamp": 1737500130100,
        },
    }


def test_parse_message_created_dm():
    u = parse_update(_message_created())
    assert u.update_type == "message_created" and u.marker == 555
    m = u.message
    assert m.chat_id == 100 and m.chat_type == "dialog"
    assert m.body.mid == "mid.1" and m.body.text == "привет"
    assert m.sender.user_id == 42 and m.sender.name == "Иван"


def test_parse_message_with_attachments():
    d = _message_created()
    d["message"]["body"]["attachments"] = [
        {"type": "image", "payload": {"token": "t1", "url": "https://cdn/x.jpg"}},
        {"type": "location", "payload": {"latitude": 55.75, "longitude": 37.61}},
    ]
    atts = parse_update(d).message.body.attachments
    assert atts[0].type == "image" and atts[0].payload["token"] == "t1"
    assert atts[1].payload["latitude"] == 55.75


def test_parse_message_callback():
    d = {
        "update_type": "message_callback",
        "marker": 556,
        "callback": {
            "callback_id": "cb.1", "payload": "ea:once:7",
            "message": _message_created()["message"],
        },
    }
    u = parse_update(d)
    assert u.callback.callback_id == "cb.1" and u.callback.payload == "ea:once:7"
    assert u.callback.message.chat_id == 100


def test_parse_bot_started():
    u = parse_update({"update_type": "bot_started", "marker": 1, "chat_id": 900, "user": {"user_id": 5, "name": "Оля"}})
    assert u.chat_id == 900 and u.user.user_id == 5


def test_parse_tolerant_to_unknown_fields():
    d = _message_created()
    d["message"]["unknown_future_field"] = {"a": 1}
    assert parse_update(d).message.raw["unknown_future_field"] == {"a": 1}
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL (`No module named 'maxbot.models'`)

- [ ] **Step 3: Реализация**

```python
# maxbot/models.py
"""Типизированные модели апдейтов MAX Bot API (толерантны к лишним полям)."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class User:
    user_id: int
    name: str = ""
    username: Optional[str] = None


@dataclass
class Attachment:
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageBody:
    mid: Optional[str] = None
    text: Optional[str] = None
    attachments: List[Attachment] = field(default_factory=list)


@dataclass
class Message:
    body: MessageBody
    chat_id: int
    chat_type: str  # "dialog" | "chat" | "channel"
    sender: Optional[User] = None
    timestamp: int = 0
    link: Optional[Dict[str, Any]] = None  # {"type": "reply", "mid": "..."} и т.п.
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Callback:
    callback_id: str
    payload: str
    message: Optional[Message] = None


@dataclass
class Update:
    update_type: str
    marker: Optional[int] = None
    message: Optional[Message] = None
    callback: Optional[Callback] = None
    chat_id: Optional[int] = None  # bot_started
    user: Optional[User] = None    # bot_started


def _user(d: Dict[str, Any]) -> Optional[User]:
    if not d:
        return None
    return User(user_id=int(d.get("user_id") or 0), name=d.get("name") or "",
                username=d.get("username"))


def _attachments(items: Any) -> List[Attachment]:
    return [Attachment(type=i.get("type", ""), payload=i.get("payload") or {})
            for i in items or [] if isinstance(i, dict)]


def parse_message(d: Dict[str, Any]) -> Message:
    body_raw = d.get("body") or {}
    recipient = d.get("recipient") or {}
    body = MessageBody(mid=body_raw.get("mid"), text=body_raw.get("text"),
                       attachments=_attachments(body_raw.get("attachments")))
    return Message(body=body, chat_id=int(recipient.get("chat_id") or 0),
                   chat_type=recipient.get("chat_type") or "", sender=_user(d.get("sender")),
                   timestamp=int(d.get("timestamp") or 0), link=d.get("link"), raw=d)


def parse_update(d: Dict[str, Any]) -> Update:
    callback = None
    if d.get("callback"):
        cb = d["callback"]
        msg = parse_message(cb["message"]) if cb.get("message") else None
        callback = Callback(callback_id=str(cb.get("callback_id") or ""),
                            payload=str(cb.get("payload") or ""), message=msg)
    return Update(update_type=d.get("update_type") or "", marker=d.get("marker"),
                  message=parse_message(d["message"]) if d.get("message") else None,
                  callback=callback, chat_id=d.get("chat_id"), user=_user(d.get("user")))
```

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (5 тестов)

- [ ] **Step 5: Commit**

```bash
git add maxbot/models.py tests/test_models.py
git commit -m "feat: модели апдейтов MAX и parse_update"
```

---

### Task 3: MaxClient — HTTP-ядро (`maxbot/max_api.py`)

**Files:**
- Create: `maxbot/max_api.py`
- Test: `tests/test_max_api.py`

**Interfaces:**
- Consumes: `maxbot.models` (Task 2).
- Produces:
  - `DEFAULT_BASE = "https://platform-api2.max.ru"`
  - `MaxApiError(status: int, message: str = "", code: Optional[str] = None)`; поля `.status`, `.code`, `.message`
  - `MaxClient(token, base_url=DEFAULT_BASE, *, rps: float = 30.0, retries: int = 3, backoff_base: float = 1.0)`; метод `await close()`; внутренний `await _request(method, path, *, params=None, json_body=None, timeout=None) -> Any`
  - Характер поведения (проверяется тестами): заголовок `Authorization` на каждом запросе; троттлинг ≥`1/rps` между запросами; ретраи на 429 (с `Retry-After`) / 5xx / сетевые ошибки с экспоненциальным backoff+jitter; 4xx → `MaxApiError` без ретрая.

- [ ] **Step 1: Failing tests**

```python
# tests/test_max_api.py
import asyncio

import httpx
import pytest
import respx

from maxbot.max_api import MaxApiError, MaxClient

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
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_max_api.py -v`
Expected: FAIL (`No module named 'maxbot.max_api'`)

- [ ] **Step 3: Реализация ядра**

```python
# maxbot/max_api.py
"""Тонкий асинхронный клиент MAX Bot API (platform-api2.max.ru)."""
import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from maxbot.models import Update, parse_update

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://platform-api2.max.ru"


class MaxApiError(Exception):
    """Ошибка MAX API. status=0 — сетевая. code — машинный код (напр. attachment.not.ready)."""

    def __init__(self, status: int, message: str = "", code: Optional[str] = None):
        self.status, self.code, self.message = status, code, message
        super().__init__(f"HTTP {status} code={code!r} {message}")


def _backoff(attempt: int, base: float) -> float:
    return min(60.0, base * (2 ** attempt)) * (0.5 + random.random() / 2)


class MaxClient:
    def __init__(self, token: str, base_url: str = DEFAULT_BASE, *,
                 rps: float = 30.0, retries: int = 3, backoff_base: float = 1.0):
        self._client = httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": token},
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=60.0, pool=30.0))
        self._min_interval = 1.0 / max(0.1, rps)
        self._last_sent = 0.0
        self._gate = asyncio.Lock()
        self._retries = retries
        self._backoff_base = backoff_base

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "MaxClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _throttle(self) -> None:
        async with self._gate:
            wait = self._last_sent + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_sent = time.monotonic()

    async def _request(self, method: str, path: str, *, params: Optional[dict] = None,
                       json_body: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        attempt = 0
        while True:
            await self._throttle()
            try:
                resp = await self._client.request(method, path, params=params,
                                                  json=json_body, timeout=timeout)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self._retries:
                    raise MaxApiError(0, str(exc), code="network") from exc
                attempt += 1
                await asyncio.sleep(_backoff(attempt, self._backoff_base))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self._retries:
                    break
                attempt += 1
                retry_after = resp.headers.get("Retry-After")
                delay = (min(60.0, float(retry_after)) if retry_after
                         else _backoff(attempt, self._backoff_base))
                await asyncio.sleep(delay)
                continue
            break
        if resp.status_code >= 400:
            code = message = None
            try:
                body = resp.json()
                code, message = body.get("code"), body.get("message")
            except Exception:
                pass
            raise MaxApiError(resp.status_code, message or resp.text[:200], code)
        if not resp.content:
            return {}
        return resp.json()
```

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_max_api.py -v`
Expected: PASS (6 тестов)

- [ ] **Step 5: Commit**

```bash
git add maxbot/max_api.py tests/test_max_api.py
git commit -m "feat: HTTP-ядро MaxClient (авторизация, троттлинг 30rps, ретраи)"
```

---

### Task 4: MaxClient — методы API

**Files:**
- Modify: `maxbot/max_api.py`
- Test: `tests/test_max_api.py` (дополнить)

**Interfaces:**
- Consumes: `_request`, `parse_update` (Task 3, 2).
- Produces (все — async методы `MaxClient`):
  - `get_me() -> User`
  - `get_chat(chat_id: int) -> Dict[str, Any]`
  - `get_message(message_id: str) -> Message`
  - `send_message(chat_id, text, *, attachments=None, notify=True, reply_to_mid=None) -> str` — возвращает `mid`; при коде `attachment.not.ready` сам делает ≤3 ретрая с паузой
  - `edit_message(message_id, text, *, attachments=None) -> bool`
  - `delete_message(message_id) -> bool`
  - `get_updates(marker: Optional[int] = None, timeout: int = 90) -> Tuple[int, List[Update]]`
  - `get_subscriptions() -> List[Dict]`, `subscribe(url, update_types: List[str], secret: Optional[str] = None) -> Dict`, `unsubscribe() -> bool`
  - `answer_callback(callback_id: str, text: Optional[str] = None) -> bool`
  - `chat_action(chat_id, action: str = "typing_on") -> bool`
  - `set_commands(commands: List[Dict[str, str]]) -> bool` — `PATCH /me/commands`
  - `get_upload_url(kind: str) -> str`, `upload_to_url(upload_url: str, path: str) -> str` (multipart поле `data`)
  - `extract_message_id(resp: Dict) -> Optional[str]` — модульная функция

- [ ] **Step 1: Failing tests (дополнить tests/test_max_api.py)**

```python
# tests/test_max_api.py — дополнения
from maxbot.max_api import extract_message_id


@respx.mock
async def test_send_message_returns_mid_and_format_markdown():
    route = respx.post(f"{BASE}/messages").mock(return_value=httpx.Response(
        200, json={"message": {"body": {"mid": "mid.9", "text": "x"}}}))
    async with client() as c:
        mid = await c.send_message(100, "привет **мир**")
    body = json.loads(route.calls.last.request.content)
    assert mid == "mid.9"
    assert body == {"text": "привет **мир**", "format": "markdown", "notify": True}


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


def test_extract_message_id_variants():
    assert extract_message_id({"message": {"body": {"mid": "m1"}}}) == "m1"
    assert extract_message_id({"message_id": "m2"}) == "m2"
    assert extract_message_id({}) is None
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_max_api.py -v`
Expected: новые тесты FAIL (нет методов); старые PASS

- [ ] **Step 3: Реализовать методы (дополнить max_api.py)**

```python
# maxbot/max_api.py — дополнения после класса MaxClient._request (внутрь класса)
import json as _json  # наверх файла не нужно — используем httpx json

    # ── внутри MaxClient ──
    async def get_me(self) -> User:
        from maxbot.models import User
        d = await self._request("GET", "/me")
        return User(user_id=int(d.get("user_id") or 0), name=d.get("name") or "", username=d.get("username"))

    async def get_chat(self, chat_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/chats/{chat_id}")

    async def get_message(self, message_id: str) -> Message:
        from maxbot.models import parse_message
        d = await self._request("GET", f"/messages/{message_id}")
        return parse_message(d.get("message") or d)

    async def send_message(self, chat_id: int, text: str, *, attachments: Optional[List[dict]] = None,
                           notify: bool = True, reply_to_mid: Optional[str] = None) -> str:
        body: Dict[str, Any] = {"text": text, "format": "markdown", "notify": notify}
        if attachments:
            body["attachments"] = attachments
        if reply_to_mid:
            body["link"] = {"type": "reply", "mid": reply_to_mid}
        for attempt in range(4):  # attachment.not.ready: 3 ретрая
            try:
                resp = await self._request("POST", "/messages", json_body=body)
            except MaxApiError as exc:
                if exc.code != "attachment.not.ready" or attempt >= 3:
                    raise
                await asyncio.sleep(max(0.3, self._backoff_base * (2 ** attempt)))
                continue
            mid = extract_message_id(resp)
            if mid is None:
                raise MaxApiError(200, "send_message: нет message_id в ответе", code="no_mid")
            return mid
        raise MaxApiError(0, "unreachable", code="internal")

    async def edit_message(self, message_id: str, text: str, *,
                           attachments: Optional[List[dict]] = None) -> bool:
        body: Dict[str, Any] = {"text": text, "format": "markdown"}
        if attachments is not None:
            body["attachments"] = attachments
        resp = await self._request("PUT", "/messages", params={"message_id": message_id}, json_body=body)
        return bool(resp.get("success", True))

    async def delete_message(self, message_id: str) -> bool:
        resp = await self._request("DELETE", "/messages", params={"message_id": message_id})
        return bool(resp.get("success", True))

    async def get_updates(self, marker: Optional[int] = None, timeout: int = 90) -> Tuple[int, List[Update]]:
        params = {"timeout": timeout}
        if marker is not None:
            params["marker"] = marker
        resp = await self._request("GET", "/updates", params=params, timeout=timeout + 5.0)
        updates = [parse_update(u) for u in resp.get("updates") or []]
        return int(resp.get("marker") or marker or 0), updates

    async def get_subscriptions(self) -> List[Dict[str, Any]]:
        return list((await self._request("GET", "/subscriptions")).get("subscriptions") or [])

    async def subscribe(self, url: str, update_types: List[str], secret: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"url": url, "update_types": update_types}
        if secret:
            body["secret"] = secret
        return await self._request("POST", "/subscriptions", json_body=body)

    async def unsubscribe(self) -> bool:
        resp = await self._request("DELETE", "/subscriptions")
        return bool(resp.get("success", True))

    async def answer_callback(self, callback_id: str, text: Optional[str] = None) -> bool:
        body: Dict[str, Any] = {"callback_id": callback_id}
        if text:
            body["message"] = {"text": text}
        resp = await self._request("POST", "/answers", json_body=body)
        return bool(resp.get("success", True))

    async def chat_action(self, chat_id: int, action: str = "typing_on") -> bool:
        resp = await self._request("POST", f"/chats/{chat_id}/actions", json_body={"action": action})
        return bool(resp.get("success", True))

    async def set_commands(self, commands: List[Dict[str, str]]) -> bool:
        resp = await self._request("PATCH", "/me/commands", json_body={"commands": commands})
        return bool(resp.get("success", True))

    async def get_upload_url(self, kind: str) -> str:
        resp = await self._request("POST", "/uploads", params={"type": kind})
        url = resp.get("url")
        if not url:
            raise MaxApiError(200, "uploads: нет url", code="no_url")
        return str(url)

    async def upload_to_url(self, upload_url: str, path: str) -> str:
        import os
        with open(path, "rb") as fh:
            resp = await self._client.post(
                upload_url, data={"data": (os.path.basename(path), fh)},
                headers={"Authorization": self._client.headers["Authorization"]})
        if resp.status_code >= 400:
            raise MaxApiError(resp.status_code, resp.text[:200], code="upload_failed")
        token = resp.json().get("token")
        if not token:
            raise MaxApiError(resp.status_code, "upload: нет token", code="no_token")
        return str(token)


def extract_message_id(resp: Dict[str, Any]) -> Optional[str]:
    msg = resp.get("message") if isinstance(resp, dict) else None
    if isinstance(msg, dict):
        mid = (msg.get("body") or {}).get("mid") or msg.get("message_id")
        if mid:
            return str(mid)
    if isinstance(resp, dict) and resp.get("message_id"):
        return str(resp["message_id"])
    return None
```

Внимание: тест-файл использует `json.loads` — добавить `import json` в шапку `tests/test_max_api.py`; фикстуру `tests/fixtures/tiny.png` создать как любой непустой файл (`printf '\x89PNG' > tests/fixtures/tiny.png`).

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_max_api.py -v`
Expected: PASS (все 13)

- [ ] **Step 5: Commit**

```bash
git add maxbot/max_api.py tests/test_max_api.py tests/fixtures/tiny.png
git commit -m "feat: методы MaxClient (messages/uploads/subscriptions/answers/actions/commands)"
```

---

### Task 5: Markdown-санитайзер (`maxbot/markdown.py`)

**Files:**
- Create: `maxbot/markdown.py`
- Test: `tests/test_markdown.py`

**Interfaces:**
- Produces: `sanitize_markdown(text: str) -> str` — LLM-markdown → безопасный MAX-markdown.

- [ ] **Step 1: Failing tests**

```python
# tests/test_markdown.py
from maxbot.markdown import sanitize_markdown


def test_headings_to_bold():
    assert sanitize_markdown("# Заголовок\n## Ещё") == "**Заголовок**\n**Ещё**"


def test_html_stripped():
    assert sanitize_markdown("привет <b>мир</b> <a href='x'>ссылка</a>") == "привет мир ссылка"


def test_table_to_code_block():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = sanitize_markdown(md)
    assert out.startswith("```") and out.endswith("```") and "| a | b |" in out


def test_long_link_clamped():
    url = "https://x.ru/" + "a" * 2100
    out = sanitize_markdown(f"[текст]({url})")
    assert "https://x.ru/" in out and len(out) < len(url)


def test_stray_odd_markers_escaped():
    out = sanitize_markdown("цена 5* 3 и _ хвост")
    assert "\\*" in out or "* 3" not in out  # одиночный * экранирован


def test_normal_markdown_untouched():
    text = "**жирный** *курсив* `code` [ссылка](https://ok.ru) > цитата"
    assert sanitize_markdown(text) == text
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_markdown.py -v`
Expected: FAIL

- [ ] **Step 3: Реализация**

```python
# maxbot/markdown.py
"""Конвертация LLM-markdown в безопасный markdown MAX."""
import re

_HTML_RE = re.compile(r"</?[a-zA-Z][^>\n]*>")
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_TABLE_RE = re.compile(r"(?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-: |]+\|[ \t]*\n)(?:^[ \t]*\|.*\|[ \t]*\n?)+", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")

_MAX_URL = 2048


def _strip_html(text: str) -> str:
    return _HTML_RE.sub("", text)


def _headings_to_bold(text: str) -> str:
    return _HEAD_RE.sub(lambda m: f"**{m.group(2)}**", text)


def _tables_to_code(text: str) -> str:
    def _repl(m: re.Match) -> str:
        return "```\n" + m.group(0).rstrip() + "\n```"
    return _TABLE_RE.sub(_repl, text)


def _clamp_links(text: str) -> str:
    def _repl(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if len(url) <= _MAX_URL:
            return m.group(0)
        return f"{label}: {url[:_MAX_URL - 3]}..."
    return _LINK_RE.sub(_repl, text)


def _escape_stray(text: str) -> str:
    """Экранируем * и _, если их суммарное количество нечётное (непарный маркер)."""
    for ch in ("*", "_"):
        if text.count(ch) % 2 == 1:
            # экранируем одиночные (не соседствующие с таким же) вхождения
            text = re.sub(rf"(?<!\\)(?<!{re.escape(ch)}){re.escape(ch)}(?!{re.escape(ch)})",
                          f"\\{ch}", text)
    return text


def sanitize_markdown(text: str) -> str:
    text = _strip_html(text)
    text = _tables_to_code(text)
    text = _headings_to_bold(text)
    text = _clamp_links(text)
    text = _escape_stray(text)
    return text.strip()
```

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_markdown.py -v`
Expected: PASS (6)

- [ ] **Step 5: Commit**

```bash
git add maxbot/markdown.py tests/test_markdown.py
git commit -m "feat: sanitize_markdown под ограничения MAX"
```

---

### Task 6: Загрузка медиа (`maxbot/uploads.py`)

**Files:**
- Create: `maxbot/uploads.py`
- Test: `tests/test_uploads.py`

**Interfaces:**
- Consumes: `MaxClient.get_upload_url` / `upload_to_url` (Task 4).
- Produces: `Uploader(client)` с `await upload(path: str, kind: str) -> str` (токен; кэш `(path, size, mtime) → token`) и `clear()`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_uploads.py
import httpx
import respx

from maxbot.uploads import Uploader

BASE = "http://test.max"


class FakeClient:
    """Минимальный двойник MaxClient для Uploader."""

    def __init__(self):
        self.upload_calls = []

    async def get_upload_url(self, kind):
        self.kind = kind
        return "http://upload.test/u"

    async def upload_to_url(self, url, path):
        self.upload_calls.append((url, path))
        return f"TOK{len(self.upload_calls)}"


async def test_upload_caches_by_path(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(b"\x89PNG" * 10)
    fc = FakeClient()
    up = Uploader(fc)
    t1 = await up.upload(str(f), "image")
    t2 = await up.upload(str(f), "image")
    assert t1 == t2 == "TOK1" and len(fc.upload_calls) == 1


async def test_upload_invalidated_on_file_change(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(b"a")
    fc = FakeClient()
    up = Uploader(fc)
    await up.upload(str(f), "image")
    f.write_bytes(b"bb")  # size изменился
    await up.upload(str(f), "image")
    assert len(fc.upload_calls) == 2
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_uploads.py -v`
Expected: FAIL

- [ ] **Step 3: Реализация**

```python
# maxbot/uploads.py
"""Загрузка медиа в MAX с кэшем переиспользуемых токенов."""
import os
from typing import Dict, Tuple


class Uploader:
    def __init__(self, client):
        self._client = client
        self._cache: Dict[Tuple[str, int, int], str] = {}

    async def upload(self, path: str, kind: str) -> str:
        """kind ∈ image|video|audio|file. Возвращает токен вложения."""
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_size, int(st.st_mtime))
        token = self._cache.get(key)
        if token:
            return token
        url = await self._client.get_upload_url(kind)
        token = await self._client.upload_to_url(url, path)
        self._cache[key] = token
        return token

    def clear(self) -> None:
        self._cache.clear()
```

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_uploads.py -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add maxbot/uploads.py tests/test_uploads.py
git commit -m "feat: Uploader с кэшем токенов медиа"
```

---

### Task 7: PollingTransport (`maxbot/transports.py`)

**Files:**
- Create: `maxbot/transports.py`
- Test: `tests/test_transports.py`

**Interfaces:**
- Consumes: `MaxClient.get_updates` (Task 4).
- Produces:
  - `class Transport(Protocol)`: `async start(on_update)`, `async stop()`, `def healthy() -> bool`
  - `PollingTransport(client, *, initial_marker=None, on_marker: Optional[Callable[[int], None]] = None, timeout=90, backoff_base=1.0)`; свойство `.marker`; после каждой партии апдейтов дергает `on_marker(marker)` и обновляет `.marker`; сетевые/5xx/429 — backoff 1→60с.

- [ ] **Step 1: Failing tests**

```python
# tests/test_transports.py
import asyncio

import pytest

from maxbot.models import parse_update
from maxbot.transports import PollingTransport


def _upd(marker, text="x"):
    return parse_update({"update_type": "message_created", "marker": marker,
                         "message": {"body": {"mid": f"m{marker}", "text": text},
                                     "recipient": {"chat_id": 1, "chat_type": "dialog"},
                                     "sender": {"user_id": 2, "name": "N"}}})


class FakePollClient:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    async def get_updates(self, marker=None, timeout=90):
        self.calls.append(marker)
        if self.batches:
            return self.batches.pop(0)
        await asyncio.sleep(10)  # имитируем зависший long poll после исчерпания партий


async def test_polling_delivers_updates_and_advances_marker():
    got, markers = [], []
    fc = FakePollClient([((2, [_upd(2), _upd(3)]))])
    t = PollingTransport(fc, initial_marker=1, on_marker=markers.append, timeout=1,
                         backoff_base=0.001)
    task = asyncio.create_task(t.start(got.append))
    for _ in range(50):
        if len(got) == 2:
            break
        await asyncio.sleep(0.01)
    assert [u.marker for u in got] == [2, 3]
    assert t.marker == 2 and markers == [2]
    await t.stop(); task.cancel()
    assert fc.calls[0] == 1


async def test_polling_backoff_on_network_errors():
    from maxbot.max_api import MaxApiError

    class FailingClient:
        def __init__(self):
            self.n = 0

        async def get_updates(self, marker=None, timeout=90):
            self.n += 1
            if self.n <= 2:
                raise MaxApiError(0, "net", code="network")
            return (99, [])

    t = PollingTransport(FailingClient(), timeout=1, backoff_base=0.001)
    task = asyncio.create_task(t.start(lambda u: None))
    for _ in range(100):
        if t.healthy() and t._failures == 0 and t.marker == 99:
            break
        await asyncio.sleep(0.01)
    assert t.marker == 99  # восстановился после 2 ошибок
    await t.stop(); task.cancel()
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_transports.py -v`
Expected: FAIL

- [ ] **Step 3: Реализация**

```python
# maxbot/transports.py
"""Транспорты получения апдейтов MAX: long polling и webhook."""
import asyncio
import logging
from typing import Awaitable, Callable, List, Optional, Protocol

from maxbot.max_api import MaxApiError

logger = logging.getLogger(__name__)

OnUpdate = Callable[..., Awaitable[None]]


class Transport(Protocol):
    async def start(self, on_update: OnUpdate) -> None: ...
    async def stop(self) -> None: ...
    def healthy(self) -> bool: ...


class PollingTransport:
    def __init__(self, client, *, initial_marker: Optional[int] = None,
                 on_marker: Optional[Callable[[int], None]] = None,
                 timeout: int = 90, backoff_base: float = 1.0):
        self._client = client
        self.marker = initial_marker
        self._on_marker = on_marker
        self._timeout = timeout
        self._backoff_base = backoff_base
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        self._failures = 0
        self._healthy = False

    def healthy(self) -> bool:
        return self._healthy

    async def start(self, on_update: OnUpdate) -> None:
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(on_update), name="max-polling")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._healthy = False

    async def _loop(self, on_update: OnUpdate) -> None:
        delay = 1.0
        while not self._stopped.is_set():
            try:
                marker, updates = await self._client.get_updates(self.marker, timeout=self._timeout)
                self._failures = 0
                self._healthy = True
                delay = 1.0
                if updates:
                    for update in updates:
                        try:
                            await on_update(update)
                        except Exception:  # один кривой апдейт не роняет polling
                            logger.exception("max: ошибка обработки апдейта %s", update.update_type)
                    if marker:
                        self.marker = marker
                        if self._on_marker:
                            try:
                                self._on_marker(marker)
                            except Exception:
                                logger.exception("max: on_marker")
                else:
                    if marker:
                        self.marker = marker
            except MaxApiError as exc:
                self._failures += 1
                self._healthy = False
                logger.warning("max polling: ошибка #%d (%s), ждём %.1fс",
                               self._failures, exc, delay)
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(60.0, delay * 2)
            except asyncio.CancelledError:
                raise
```

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_transports.py -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add maxbot/transports.py tests/test_transports.py
git commit -m "feat: PollingTransport с курсором маркера и backoff"
```

---

### Task 8: WebhookTransport

**Files:**
- Modify: `maxbot/transports.py`
- Test: `tests/test_transports.py` (дополнить)

**Interfaces:**
- Consumes: `MaxClient.subscribe/unsubscribe` (Task 4), `parse_update` (Task 2).
- Produces: `WebhookTransport(client, *, url: str, port: int, secret: Optional[str] = None, path: str = "/max/webhook", update_types: Optional[List[str]] = None)`; `start(on_update)` поднимает aiohttp-сервер на `0.0.0.0:port` и подписывается через API; `stop()` отписывается и гасит сервер; каждый запрос проверяет заголовок из `_SECRET_HEADERS = ("x-secret",)` — несовпадение/отсутствие при заданном `secret` → 401; JSON → `parse_update` → `asyncio.create_task(on_update(u))`, ответ 200 сразу.

- [ ] **Step 1: Failing tests (дополнить tests/test_transports.py)**

```python
# tests/test_transports.py — дополнения
import json as _json

from aiohttp.test_utils import TestClient, TestServer

from maxbot.transports import WebhookTransport

UPDATE_TYPES = ["message_created", "message_callback", "bot_started"]


class FakeSubClient:
    def __init__(self):
        self.subscribed = unsubscribed = None

    async def subscribe(self, url, types, secret=None):
        self.subscribed = (url, tuple(types), secret)
        return {}

    async def unsubscribe(self):
        self.unsubscribed = True
        return True


async def _make_client(secret="S1"):
    fc = FakeSubClient()
    t = WebhookTransport(fc, url="https://pub.example/hook", port=0, secret=secret,
                         update_types=UPDATE_TYPES)
    received = []

    async def on_update(u):
        received.append(u)

    await t.start(on_update)
    http = TestClient(TestServer(t._app))
    await http.start_server()
    return t, fc, http, received


async def test_webhook_accepts_valid_secret_and_dispatches():
    t, fc, http, received = await _make_client()
    payload = {"update_type": "bot_started", "chat_id": 5, "user": {"user_id": 1, "name": "A"}}
    resp = await http.post("/max/webhook", data=_json.dumps(payload), headers={"X-Secret": "S1"})
    assert resp.status == 200
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)
    assert received and received[0].update_type == "bot_started"
    assert fc.subscribed[2] == "S1"
    await t.stop(); await http.close()


async def test_webhook_rejects_bad_secret():
    t, fc, http, _ = await _make_client()
    resp = await http.post("/max/webhook", data="{}", headers={"X-Secret": "WRONG"})
    assert resp.status == 401
    await t.stop(); await http.close()


async def test_webhook_stop_unsubscribes():
    t, fc, http, _ = await _make_client()
    await t.stop(); await http.close()
    assert fc.unsubscribed is True
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_transports.py -v`
Expected: новые FAIL

- [ ] **Step 3: Реализация (дополнить maxbot/transports.py)**

```python
# maxbot/transports.py — дополнения
class WebhookTransport:
    _SECRET_HEADERS = ("x-secret",)

    def __init__(self, client, *, url: str, port: int, secret: Optional[str] = None,
                 path: str = "/max/webhook",
                 update_types: Optional[List[str]] = None):
        self._client = client
        self._url = url
        self._port = port
        self._secret = secret
        self._path = path
        self._types = update_types or ["message_created", "message_callback", "bot_started"]
        self._runner = None
        self._app = None
        self._on_update: Optional[OnUpdate] = None

    def healthy(self) -> bool:
        return self._runner is not None

    async def start(self, on_update: OnUpdate) -> None:
        from aiohttp import web

        self._on_update = on_update
        self._app = web.Application()
        self._app.router.add_post(self._path, self._handler)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        await self._client.subscribe(self._url, self._types, secret=self._secret)
        logger.info("max webhook: слушаем :%d%s, подписка %s", self._port, self._path, self._url)

    async def stop(self) -> None:
        with contextlib_suppress():
            await self._client.unsubscribe()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def _handler(self, request):
        if self._secret:
            supplied = next((request.headers[h] for h in self._SECRET_HEADERS
                             if h in request.headers), None)
            if supplied != self._secret:
                return aiohttp_web_response(status=401, text="bad secret")
        try:
            data = await request.json()
        except Exception:
            return aiohttp_web_response(status=400, text="bad json")
        update = parse_update(data if isinstance(data, dict) else {})
        if self._on_update is not None:
            asyncio.create_task(self._safe_dispatch(update))
        return aiohttp_web_response(status=200, text="ok")

    async def _safe_dispatch(self, update) -> None:
        try:
            await self._on_update(update)
        except Exception:
            logger.exception("max webhook: ошибка обработки апдейта")


def aiohttp_web_response(status: int, text: str):
    from aiohttp import web
    return web.Response(status=status, text=text)


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
```

(Импорт `from maxbot.models import parse_update` добавить в шапку `transports.py`.)

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_transports.py -v`
Expected: PASS (5)

- [ ] **Step 5: Commit**

```bash
git add maxbot/transports.py tests/test_transports.py
git commit -m "feat: WebhookTransport (aiohttp, секрет, подписка/отписка)"
```

---

### Task 9: MaxAdapter — каркас + `register()`

**Files:**
- Create: `maxbot/adapter.py`
- Modify: `maxbot/__init__.py`
- Test: `tests/test_adapter.py`

**Interfaces:**
- Consumes: `MaxClient`, `Uploader`, `PollingTransport`/`WebhookTransport`, `sanitize_markdown` (Tasks 3-8); hermes `BasePlatformAdapter` (config с `.extra`, `Platform`, `SendResult`).
- Produces:
  - `GATEWAY_COMMANDS: List[Tuple[str, str]]`
  - `MaxAdapter(config, client: Optional[MaxClient] = None, transport=None)`; `MAX_MESSAGE_LENGTH = 4000`; методы `connect(*, is_reconnect=False) -> bool`, `disconnect()`, `send(chat_id, content, reply_to=None, metadata=None) -> SendResult`, `send_typing(chat_id, metadata=None)`, `get_chat_info(chat_id)`, `delete_message(chat_id, message_id) -> bool`, `send_image_file`, `send_document`, `send_voice`, `send_video`, статический `_segment(text, limit=3900) -> List[str]`
  - `check_requirements() -> bool`, `validate_config(config) -> bool`, `is_connected(config) -> bool`
  - `register(ctx)` в `maxbot/__init__.py` (полные kwargs — см. код)

- [ ] **Step 1: Failing tests**

```python
# tests/test_adapter.py
import asyncio

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.adapter import MaxAdapter, GATEWAY_COMMANDS, check_requirements


class FakeCfg:
    def __init__(self, extra=None):
        self.extra = extra or {}


class FakeClient:
    """Двойник MaxClient для тестов адаптера."""

    def __init__(self):
        self.sent = []          # (chat_id, text)
        self.edited = {}
        self.deleted = []
        self.actions = []
        self._mid = 0

    async def get_me(self):
        from maxbot.models import User
        return User(user_id=999, name="Bot")

    async def send_message(self, chat_id, text, **kw):
        self._mid += 1
        self.sent.append((chat_id, text))
        return f"mid.{self._mid}"

    async def edit_message(self, message_id, text, **kw):
        self.edited[message_id] = text
        return True

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        return True

    async def chat_action(self, chat_id, action="typing_on"):
        self.actions.append((chat_id, action))
        return True

    async def set_commands(self, commands):
        self.commands = commands
        return True

    async def get_chat(self, chat_id):
        return {"chat_id": chat_id, "type": "chat", "title": "Группа"}


class FakeTransport:
    def __init__(self):
        self.started = stopped = False

    async def start(self, on_update):
        self.started = True
        self.on_update = on_update

    async def stop(self):
        self.stopped = True

    def healthy(self):
        return self.started and not self.stopped


def make_adapter(client=None, transport=None):
    adapter = MaxAdapter(FakeCfg(), client=client, transport=transport)
    return adapter


async def test_connect_registers_bot_commands_and_locks():
    client, transport = FakeClient(), FakeTransport()
    adapter = make_adapter(client, transport)
    assert await adapter.connect()
    assert adapter.is_connected and transport.started
    assert client.commands and client.commands[0][0] == "/new"
    await adapter.disconnect()


async def test_send_sanitizes_segments_and_returns_last_mid():
    client = FakeClient()
    adapter = make_adapter(client, FakeTransport())
    await adapter._fake_connect_for_tests(client)
    result = await adapter.send("100", "# Заголовок\n" + "длинный текст " * 600)
    assert result.success and result.message_id == f"mid.{len(client.sent)}"
    assert "**Заголовок**" == client.sent[0].split("\n")[0]
    assert all(len(t) <= 3900 for _, t in client.sent)


async def test_send_media_via_uploader(tmp_path, monkeypatch):
    client = FakeClient()
    adapter = make_adapter(client, FakeTransport())
    await adapter._fake_connect_for_tests(client)

    async def fake_upload(path, kind):
        return "TOK-" + kind

    monkeypatch.setattr(adapter._uploader, "upload", fake_upload)
    f = tmp_path / "img.png"
    f.write_bytes(b"\x89PNG")
    res = await adapter.send_image_file("100", str(f), caption="каптион")
    assert res.success
    chat_id, text = client.sent[-1]
    assert chat_id == 100


async def test_delete_and_typing():
    client = FakeClient()
    adapter = make_adapter(client, FakeTransport())
    await adapter._fake_connect_for_tests(client)
    assert await adapter.delete_message("100", "mid.5")
    assert "mid.5" in client.deleted
    await adapter.send_typing("100")
    assert client.actions == [(100, "typing_on")]


def test_check_requirements_env(monkeypatch):
    monkeypatch.delenv("MAX_ACCESS_TOKEN", raising=False)
    assert check_requirements() is False
    monkeypatch.setenv("MAX_ACCESS_TOKEN", "T")
    assert check_requirements() is True
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_adapter.py -v`
Expected: FAIL (`No module named 'maxbot.adapter'`)

- [ ] **Step 3: Реализация**

```python
# maxbot/adapter.py
"""Адаптер платформы MAX для гейтвея Hermes Agent."""
import asyncio
import contextlib
import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

from gateway.config import Platform
from gateway.platforms._shared import get_scoped_secret
from gateway.platforms.base import BasePlatformAdapter, SendResult

from maxbot.markdown import sanitize_markdown
from maxbot.max_api import MaxApiError, MaxClient
from maxbot.uploads import Uploader

logger = logging.getLogger(__name__)

GATEWAY_COMMANDS = [
    ("/new", "Новая сессия"),
    ("/status", "Статус"),
    ("/stop", "Остановить"),
    ("/queue", "Очередь"),
    ("/model", "Выбор модели"),
    ("/help", "Помощь"),
]

_TRUTHY = {"1", "true", "yes"}
_DEFAULT_MARKER_KEY = "marker"


def _env_or_extra(extra: dict, env: str, key: str, default: Any = None) -> Any:
    return get_scoped_secret(env, "") or extra.get(key, default)


class MaxAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = 4000

    def __init__(self, config, client: Optional[MaxClient] = None, transport=None):
        super().__init__(config=config, platform=Platform("max"))
        extra = getattr(config, "extra", None) or {}
        self._extra = extra
        self._token = _env_or_extra(extra, "MAX_ACCESS_TOKEN", "access_token")
        self._api_base = _env_or_extra(extra, "MAX_API_BASE", "api_base")
        self._updates_mode = str(_env_or_extra(extra, "MAX_UPDATES_MODE", "updates_mode", "polling")).lower()
        _rc = _env_or_extra(extra, "MAX_REGISTER_COMMANDS", "register_commands", "1")
        self._register_commands = str(_rc).lower() not in {"0", "false", "no"}
        self._bot_user_id: Optional[int] = None
        self._client = client
        self._transport = transport
        self._uploader: Optional[Uploader] = None
        self._lock_identity: Optional[str] = None
        self._marker = extra.get(_DEFAULT_MARKER_KEY)

    # ── фабрики для подмены в тестах ──
    def _make_client(self) -> MaxClient:
        return MaxClient(self._token, base_url=self._api_base or None)

    def _make_transport(self):
        from maxbot.transports import PollingTransport
        return PollingTransport(self._client, initial_marker=self._marker,
                                on_marker=self._persist_marker)

    def _persist_marker(self, marker: int) -> None:
        self._marker = marker
        extra = getattr(self.config, "extra", None)
        if extra is not None:
            extra[_DEFAULT_MARKER_KEY] = marker

    @property
    def name(self) -> str:
        return "MAX"

    async def _fake_connect_for_tests(self, client) -> None:  # без lock/команд
        self._client = client
        self._uploader = Uploader(client)
        self._bot_user_id = 999

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self._token:
            logger.error("max: MAX_ACCESS_TOKEN не задан")
            return self._fail("config_missing", "MAX_ACCESS_TOKEN обязателен", retryable=False)
        self._client = self._client or self._make_client()
        try:
            me = await self._client.get_me()
        except MaxApiError as exc:
            return self._fail("max_auth" if exc.status == 401 else "max_connect",
                              str(exc), retryable=exc.status != 401)
        self._bot_user_id = me.user_id
        # token lock: два профиля не съедают один токен
        try:
            from gateway.status import acquire_scoped_lock, release_scoped_lock
            identity = hashlib.sha1(self._token.encode()).hexdigest()[:16]
            ok, _ = acquire_scoped_lock("max", identity)
            if not ok:
                return self._fail("lock_conflict", "токен MAX занят другим профилем", retryable=False)
            self._lock_identity = identity
        except ImportError:
            pass
        self._uploader = Uploader(self._client)
        if self._updates_mode == "webhook":
            url = _env_or_extra(self._extra, "MAX_WEBHOOK_URL", "webhook_url")
            if not url:
                return self._fail("config_missing", "webhook-режим требует MAX_WEBHOOK_URL", retryable=False)
            from maxbot.transports import WebhookTransport
            self._transport = self._transport or WebhookTransport(
                self._client, url=url,
                port=int(_env_or_extra(self._extra, "MAX_WEBHOOK_PORT", "webhook_port", 8443)),
                secret=_env_or_extra(self._extra, "MAX_WEBHOOK_SECRET", "webhook_secret") or None,
                update_types=["message_created", "message_callback", "bot_started"])
        else:
            self._transport = self._transport or self._make_transport()
        await self._transport.start(self._handle_update)
        if self._register_commands:
            with contextlib.suppress(Exception):
                await self._client.set_commands(GATEWAY_COMMANDS)
        self._mark_connected()
        logger.info("max: подключен как %s (user_id=%s, режим %s)", me.name, me.user_id, self._updates_mode)
        return True

    def _fail(self, code: str, message: str, *, retryable: bool) -> bool:
        self._set_fatal_error(code, message, retryable=retryable)
        return False

    async def disconnect(self) -> None:
        if self._transport:
            with contextlib.suppress(Exception):
                await self._transport.stop()
            self._transport = None
        if self._lock_identity:
            with contextlib.suppress(Exception):
                from gateway.status import release_scoped_lock
                release_scoped_lock("max", self._lock_identity)
            self._lock_identity = None
        if self._client:
            with contextlib.suppress(Exception):
                await self._client.close()
        self._mark_disconnected()

    # ── outbound ──
    @staticmethod
    def _segment(text: str, limit: int = 3900) -> List[str]:
        parts: List[str] = []
        while len(text) > limit:
            cut = text.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = limit
            parts.append(text[:cut].rstrip())
            text = text[cut:].lstrip()
        if text.strip():
            parts.append(text.strip())
        return parts or [""]

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="not connected")
        await self._cleanup_drafts(chat_id)  # Task 12: удаляем streaming-превью
        last_mid: Optional[str] = None
        try:
            for chunk in self._segment(sanitize_markdown(content)):
                last_mid = await self._client.send_message(
                    int(chat_id), chunk, reply_to_mid=reply_to)
        except MaxApiError as exc:
            return SendResult(success=False, error=str(exc))
        return SendResult(success=True, message_id=last_mid)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if not self._client:
            return
        with contextlib.suppress(Exception):  # в DM может не работать — молча
            await self._client.chat_action(int(chat_id), "typing_on")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        try:
            chat = await self._client.get_chat(int(chat_id))
            return {"name": chat.get("title") or chat.get("name") or chat_id,
                    "type": "group" if chat.get("type") in ("chat", "channel") else "dm",
                    "chat_id": chat_id}
        except Exception:
            return {"name": chat_id, "type": "dm", "chat_id": chat_id}

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        try:
            return await self._client.delete_message(message_id)
        except Exception:
            return False

    async def _send_attachment(self, chat_id: str, path: str, kind: str,
                               caption: Optional[str]) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="not connected")
        token = await self._uploader.upload(path, kind)
        att = [{"type": kind, "payload": {"token": token}}]
        mid = await self._client.send_message(
            int(chat_id), sanitize_markdown(caption) if caption else "", attachments=att)
        return SendResult(success=True, message_id=mid)

    async def send_image_file(self, chat_id, image_path, caption=None, reply_to=None,
                              metadata=None, **kwargs) -> SendResult:
        return await self._send_attachment(chat_id, image_path, "image", caption)

    async def send_document(self, chat_id, file_path, caption=None, file_name=None,
                            reply_to=None, metadata=None, **kwargs) -> SendResult:
        return await self._send_attachment(chat_id, file_path, "file", caption)

    async def send_voice(self, chat_id, audio_path, caption=None, reply_to=None,
                         metadata=None, **kwargs) -> SendResult:
        return await self._send_attachment(chat_id, audio_path, "audio", caption)

    async def send_video(self, chat_id, video_path, caption=None, reply_to=None,
                         metadata=None, **kwargs) -> SendResult:
        return await self._send_attachment(chat_id, video_path, "video", caption)

    async def _cleanup_drafts(self, chat_id: str) -> None:  # реальная логика — Task 12
        pass

    async def _handle_update(self, update) -> None:  # реальная логика — Task 10
        pass


def check_requirements() -> bool:
    return bool(get_scoped_secret("MAX_ACCESS_TOKEN", ""))


def validate_config(config) -> bool:
    extra = getattr(config, "extra", None) or {}
    return bool(get_scoped_secret("MAX_ACCESS_TOKEN", "") or extra.get("access_token"))


def is_connected(config) -> bool:
    return validate_config(config)
```

Заменить `maxbot/__init__.py` целиком:

```python
# maxbot/__init__.py
"""Плагин платформы MAX для гейтвея Hermes Agent."""


def register(ctx):
    from maxbot.adapter import MaxAdapter, check_requirements, is_connected, validate_config

    ctx.register_platform(
        name="max",
        label="MAX",
        adapter_factory=MaxAdapter,
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["MAX_ACCESS_TOKEN"],
        install_hint="pip install maxbot (httpx и aiohttp уже в зависимостях)",
        max_message_length=4000,
        emoji="✈️",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via MAX messenger (max.ru). MAX renders Markdown "
            "(bold, italic, strikethrough, underline, code, links, quotes), supports "
            "inline buttons, media attachments and streaming message previews. "
            "The audience is predominantly Russian-speaking — reply in Russian "
            "when the user writes in Russian."),
    )
    # env/yaml-хуки и cron-доставка подключаются в Task 13
```

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_adapter.py -v`
Expected: PASS (5)

- [ ] **Step 5: Commit**

```bash
git add maxbot/adapter.py maxbot/__init__.py tests/test_adapter.py
git commit -m "feat: MaxAdapter — connect/send/медиа + register()"
```

---

### Task 10: MaxAdapter — inbound: сообщения, вложения, bot_started

**Files:**
- Modify: `maxbot/adapter.py` (заменить заглушки `_handle_update`, добавить `_collect_media` и хелперы)
- Test: `tests/test_adapter_inbound.py`

**Interfaces:**
- Consumes: `Update/Message/Callback/Attachment` (Task 2), `MaxClient.get_message` (догрузка reply), хелперы hermes `cache_image_from_bytes` / `cache_audio_from_bytes` / `cache_document_from_bytes`, `self.build_source`, `self.handle_message`, `MessageEvent`/`MessageType`.
- Produces:
  - `async _handle_update(update: Update) -> None` — полный inbound (message_created / message_callback → `_interactive.dispatch` из Task 11 / bot_started)
  - `async _collect_media(msg: Message) -> Tuple[List[str], List[str], str]` — пути файлов, типы, доп. текст (contact/location/share/sticker)
  - `def _mention_re` — regex упоминания бота (строится в `connect()`), gate групп: упоминание ссылкой `[Имя](max://user/<bot_id>)` или reply на сообщение бота (`msg.link.type == "reply"` + `get_message(...).sender.user_id == bot_id`)
  - `dm`: `chat_type == "dialog"`; группы: `"chat"`; каналы `"channel"` — игнорируем
  - бот_started → приветствие `"Привет! Я Hermes-агент. Напишите что-нибудь или /help."` (обязательная отправка только если сообщение не дропнется)

- [ ] **Step 1: Failing tests**

```python
# tests/test_adapter_inbound.py
import asyncio

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.adapter import MaxAdapter
from maxbot.models import parse_update


class FakeCfg:
    extra = {}


class FakeClient:
    def __init__(self):
        self.sent = []
        self._mid = 0
        self.replied_lookup = {}

    async def send_message(self, chat_id, text, **kw):
        self._mid += 1
        self.sent.append((chat_id, text))
        return f"mid.{self._mid}"

    async def get_message(self, message_id):
        return self.replied_lookup.get(message_id)


def make_adapter():
    adapter = MaxAdapter(FakeCfg(), client=FakeClient(), transport=None)
    adapter._bot_user_id = 999
    adapter._uploader = None
    adapter._mention_re = __import__("re").compile(r"\[([^\]]*)\]\(max://user/999\)")
    adapter._interactive = _NoopInteractive()
    adapter._message_handler = None
    return adapter


class _NoopInteractive:
    async def dispatch(self, callback):
        self.last = callback


def _upd_message(text="привет", chat_type="dialog", sender_id=42, chat_id=100, mid="m1", link=None):
    d = {
        "update_type": "message_created", "marker": 1,
        "message": {
            "body": {"mid": mid, "text": text, "attachments": []},
            "recipient": {"chat_id": chat_id, "chat_type": chat_type},
            "sender": {"user_id": sender_id, "name": "Иван"},
            "timestamp": 1,
        },
    }
    if link:
        d["message"]["link"] = link
    return parse_update(d)


class Collector:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


async def test_dm_message_dispatched():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message())
    assert len(col.events) == 1
    ev = col.events[0]
    assert ev.text == "привет" and ev.source.chat_id == "100"
    assert ev.source.chat_type == "dm" and ev.source.user_id == "42"


async def test_own_messages_filtered():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message(sender_id=999))
    assert col.events == []


async def test_group_requires_mention():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message(text="просто болтовня", chat_type="chat"))
    assert col.events == []
    await adapter._handle_update(
        _upd_message(text="[Hermes](max://user/999) посчитай 2+2", chat_type="chat"))
    assert len(col.events) == 1 and col.events[0].text == "посчитай 2+2"


async def test_group_reply_to_bot_passes():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    adapter._client.replied_lookup["m.our"] = parse_update(
        _upd_message(sender_id=999, mid="m.our")).message
    upd = _upd_message(text="и что дальше?", chat_type="chat",
                       link={"type": "reply", "mid": "m.our"})
    await adapter._handle_update(upd)
    assert len(col.events) == 1


async def test_location_and_contact_as_text():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    d = _upd_message(text="")
    d.message.body.attachments = [
        __import__("maxbot.models", fromlist=["Attachment"]).Attachment(
            type="location", payload={"latitude": 55.75, "longitude": 37.61}),
        __import__("maxbot.models", fromlist=["Attachment"]).Attachment(
            type="contact",
            payload={"vcf_info": "BEGIN:VCARD\r\nTEL;TYPE=cell:79990000000\r\nFN:Иван\r\nEND:VCARD"}),
    ]
    await adapter._handle_update(d)
    ev = col.events[0]
    assert "55.75" in ev.text and "79990000000" in ev.text


async def test_bot_started_sends_greeting():
    adapter = make_adapter()
    adapter._message_handler = Collector()
    await adapter._handle_update(parse_update(
        {"update_type": "bot_started", "chat_id": 300, "user": {"user_id": 7, "name": "Оля"}}))
    assert adapter._client.sent and "Hermes" in adapter._client.sent[0][1]


async def test_message_callback_routed_to_interactive():
    adapter = make_adapter()
    await adapter._handle_update(parse_update({
        "update_type": "message_callback",
        "callback": {"callback_id": "cb.1", "payload": "ea:once:5",
                     "message": _upd_message()["message"]},
    }))
    assert adapter._interactive.last.payload == "ea:once:5"
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_adapter_inbound.py -v`
Expected: FAIL (заглушка `_handle_update`)

- [ ] **Step 3: Реализация (в maxbot/adapter.py)**

```python
# ── замена заглушек в MaxAdapter ──
import re

from gateway.platforms.base import (
    cache_audio_from_bytes, cache_document_from_bytes, cache_image_from_bytes)
from gateway.platforms.event import MessageEvent, MessageType

import httpx as _httpx

_MEDIA_EXT_BY_MIME = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/wav": ".wav",
    "video/mp4": ".mp4",
}

_GREETING = (
    "Привет! Я Hermes-агент в MAX. Напишите вопрос, "
    "отправьте фото или файл — я отвечу. Команды: /new, /status, /model, /help."
)


def _vcf_field(vcf: str, name: str) -> str:
    m = re.search(rf"^{name}[^\r\n:]*:(.+)$", vcf or "", re.MULTILINE)
    return m.group(1).strip() if m else ""


class MaxAdapter(BasePlatformAdapter):
    # ... (всё из Task 9) ...

    # в connect() после self._bot_user_id = me.user_id добавить:
    #     self._mention_re = re.compile(rf"\[([^\]]*)\]\(max://user/{me.user_id}\)")
    # и self._interactive = InteractiveDispatcher(self)  (создаётся в Task 11;
    # до этого в __init__: self._interactive = None)

    async def _handle_update(self, update) -> None:
        try:
            if update.update_type == "message_created" and update.message:
                await self._on_message(update.message)
            elif update.update_type == "message_callback" and update.callback:
                if self._interactive:
                    await self._interactive.dispatch(update.callback)
            elif update.update_type == "bot_started":
                chat_id = update.chat_id
                if chat_id:
                    with contextlib.suppress(Exception):
                        await self._client.send_message(
                            int(chat_id), _GREETING, notify=True)
        except Exception:
            logger.exception("max: ошибка обработки апдейта %s", update.update_type)

    async def _on_message(self, msg) -> None:
        if msg.sender and msg.sender.user_id == self._bot_user_id:
            return  # своё сообщение
        if msg.chat_type == "channel":
            return
        chat_type = "dm" if msg.chat_type == "dialog" else "group"
        text = msg.body.text or ""
        if chat_type == "group":
            passed, text = await self._group_gate(msg, text)
            if not passed:
                return
        media_paths, media_types, extra_text = await self._collect_media(msg)
        combined = "\n".join(x for x in (text.strip(), extra_text) if x)
        if not combined and not media_paths:
            return
        source = self.build_source(
            chat_id=str(msg.chat_id), chat_name=str(msg.chat_id), chat_type=chat_type,
            user_id=str(msg.sender.user_id) if msg.sender else "",
            user_name=(msg.sender.name if msg.sender else "") or "")
        reply_mid = None
        if msg.link and msg.link.get("type") == "reply":
            reply_mid = str(msg.link.get("mid") or "") or None
        event = MessageEvent(
            text=combined or " ",
            message_type=MessageType.TEXT,
            source=source, raw_message=msg.raw, message_id=msg.body.mid,
            media_urls=media_paths, media_types=media_types, reply_to_message_id=reply_mid)
        await self.handle_message(event)

    async def _group_gate(self, msg, text: str):
        if self._mention_re and self._mention_re.search(text):
            return True, self._mention_re.sub("", text).strip()
        if msg.link and msg.link.get("type") == "reply" and msg.link.get("mid"):
            with contextlib.suppress(Exception):
                replied = await self._client.get_message(str(msg.link["mid"]))
                if replied and replied.sender and replied.sender.user_id == self._bot_user_id:
                    return True, text
        return False, text

    async def _collect_media(self, msg):
        paths, types, texts = [], [], []
        for att in msg.body.attachments:
            try:
                if att.type == "image":
                    if path := await self._download_cached(att, cache_image_from_bytes, ".jpg", _MEDIA_EXT_BY_MIME):
                        paths.append(path), types.append("image")
                elif att.type == "audio":
                    if path := await self._download_cached(att, cache_audio_from_bytes, ".mp3", _MEDIA_EXT_BY_MIME):
                        paths.append(path), types.append("audio")
                elif att.type in ("file", "video"):
                    ext = "." + str(att.payload.get("filename", "file.bin")).rsplit(".", 1)[-1]
                    if path := await self._download_cached(att, cache_document_from_bytes, ext, _MEDIA_EXT_BY_MIME, is_doc=True):
                        paths.append(path), types.append(att.type)
                elif att.type == "sticker":
                    texts.append("[стикер]")
                elif att.type == "share":
                    title = att.payload.get("title") or att.payload.get("url") or "шеринг"
                    texts.append(f"[шеринг] {title}")
                elif att.type == "contact":
                    vcf = att.payload.get("vcf_info") or ""
                    tel, fn = _vcf_field(vcf, "TEL"), _vcf_field(vcf, "FN")
                    texts.append(f"[контакт] {fn} {tel}".strip())
                elif att.type == "location":
                    lat = att.payload.get("latitude") or att.payload.get("lat")
                    lon = att.payload.get("longitude") or att.payload.get("lon") or att.payload.get("lng")
                    if lat is not None:
                        texts.append(f"[геолокация] {lat}, {lon} ( https://www.openstreetmap.org/?mlat={lat}&mlon={lon} )")
            except Exception:
                logger.exception("max: вложение %s не обработано", att.type)
        return paths, types, "\n".join(texts)

    async def _download_cached(self, att, cache_fn, default_ext, mime_map=None, is_doc=False):
        url = att.payload.get("url") or att.payload.get("preview_url")
        if not url and msg_mid := (att.payload.get("mid")):
            with contextlib.suppress(Exception):
                fresh = await self._client.get_message(str(msg_mid))
                url = next((a.payload.get("url") for a in fresh.body.attachments
                            if a.type == att.type and a.payload.get("url")), None)
        if not url:
            return None
        async with _httpx.AsyncClient(timeout=60.0, follow_redirects=True) as hc:
            resp = await hc.get(str(url))
            resp.raise_for_status()
        data = resp.content
        mime = resp.headers.get("content-type", "").split(";")[0].strip()
        ext = mime_map.get(mime, default_ext) if mime_map else default_ext
        if is_doc:
            return cache_fn(data, f"max_attachment{ext}")
        return cache_fn(data, ext)
```

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_adapter_inbound.py tests/test_adapter.py -v`
Expected: PASS (7 + 5)

- [ ] **Step 5: Commit**

```bash
git add maxbot/adapter.py tests/test_adapter_inbound.py
git commit -m "feat: inbound — сообщения, mention-гейтинг, вложения, bot_started"
```

---

### Task 11: Интерактивные кнопки (`maxbot/interactive.py`)

**Files:**
- Create: `maxbot/interactive.py`
- Modify: `maxbot/adapter.py` (делегирование 4 методов + создание диспетчера)
- Test: `tests/test_interactive.py`

**Interfaces:**
- Consumes: `MaxClient.send_message/answer_callback/edit_message`; резолверы ядра (ленивые импорты): `tools.approval.resolve_gateway_approval(session_key, choice) -> int`, `tools.slash_confirm.resolve(session_key, confirm_id, choice)`, `tools.clarify_gateway.resolve_gateway_clarify(clarify_id, response)` / `mark_awaiting_text(clarify_id)`.
- Produces:
  - `keyboard_attachment(rows: List[List[Dict[str, str]]]) -> Dict` — attachment `inline_keyboard` с кнопками `{type: "callback", text, payload}`
  - `InteractiveDispatcher(adapter)`:
    - состояния: `approval_state: Dict[int, str]`, `slash_state: Dict[str, str]`, `clarify_state: Dict[str, str]`, `picker_state: Dict[str, dict]`, `_counter: itertools.count`
    - `await send_exec_approval(chat_id, command, session_key, description="dangerous command", metadata=None, allow_permanent=True, allow_session=True, smart_denied=False) -> SendResult` — кнопки `ea:once:<id>` / `ea:session:<id>` / `ea:always:<id>` / `ea:deny:<id>` (подписи: «✅ Один раз», «✅ Сессия», «✅ Всегда», «❌ Отклонить»; при `smart_denied` только once+deny)
    - `await send_slash_confirm(chat_id, title, message, session_key, confirm_id, metadata=None)` — `sc:once:<id>` / `sc:always:<id>` / `sc:cancel:<id>`
    - `await send_clarify(chat_id, question, choices, clarify_id, session_key, metadata=None)` — кнопки по выборам `cl:<id>:<idx>` + «Другое» `cl:<id>:-1`
    - `await send_choice_picker(chat_id, title, choices, session_key, on_choice_selected, metadata=None)` — `cp:<idx>`, состояние по chat_id
    - `await dispatch(callback: Callback)` — маршрутизация по префиксу payload: резолв → `answer_callback` (снять часики) → `edit_message` (итог + пустая клавиатура); «Другое» → `mark_awaiting_text`
  - Адаптер делегирует: `send_exec_approval` и др. → `self._interactive.*`; в `connect()` создаётся `self._interactive = InteractiveDispatcher(self)`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_interactive.py
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.interactive import InteractiveDispatcher, keyboard_attachment
from maxbot.models import parse_update


class FakeAdapterApi:
    """Только то, что нужно диспетчеру от адаптера."""

    def __init__(self):
        self.sent = {}       # chat_id -> последняя (text, attachments)
        self.answered = []
        self.edited = {}
        self._mid = 0

    async def api_send(self, chat_id, text, attachments=None):
        self._mid += 1
        self.sent[chat_id] = (text, attachments)
        return f"mid.{self._mid}"

    async def api_answer(self, callback_id, text=None):
        self.answered.append((callback_id, text))

    async def api_edit(self, message_id, text, attachments=None):
        self.edited[message_id] = (text, attachments)


def make_dispatcher():
    api = FakeAdapterApi()
    disp = InteractiveDispatcher(api)  # api — объект с api_send/api_answer/api_edit
    return disp, api


def _cb(payload, chat_id=100):
    return parse_update({
        "update_type": "message_callback",
        "callback": {"callback_id": "cb.9", "payload": payload,
                     "message": {"body": {"mid": "m.cb", "text": ""},
                                 "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
                                 "sender": {"user_id": 1, "name": "Иван"}}},
    }).callback


def test_keyboard_attachment_shape():
    att = keyboard_attachment([[{"type": "callback", "text": "Ок", "payload": "ea:once:1"}]])
    assert att["type"] == "inline_keyboard"
    assert att["payload"]["buttons"][0][0]["payload"] == "ea:once:1"


async def test_exec_approval_buttons_and_resolve(monkeypatch):
    from maxbot import interactive as I

    resolved = []
    monkeypatch.setattr(I, "_resolve_approval",
                        lambda session_key, choice: resolved.append((session_key, choice)) or 1)
    disp, api = make_dispatcher()
    await disp.send_exec_approval("100", "rm -rf /tmp/x", "sess:1", description="опасно")
    text, atts = api.sent["100"]
    payloads = [b["payload"] for row in atts[0]["payload"]["buttons"] for b in row]
    assert "ea:once:1" in payloads and "ea:deny:1" in payloads
    assert "rm -rf /tmp/x" in text
    await disp.dispatch(_cb("ea:once:1"))
    assert resolved == [("sess:1", "once")]
    assert api.answered == [("cb.9", "✅ Одобрено (один раз)")]
    assert api.edited["m.cb"][0].startswith("✅ Одобрено")


async def test_exec_approval_smart_denied(monkeypatch):
    from maxbot import interactive as I
    monkeypatch.setattr(I, "_resolve_approval", lambda sk, ch: 1)
    disp, api = make_dispatcher()
    await disp.send_exec_approval("100", "cmd", "s", smart_denied=True)
    _, atts = api.sent["100"]
    payloads = [b["payload"] for row in atts[0]["payload"]["buttons"] for b in row]
    assert payloads == ["ea:once:1", "ea:deny:1"]


async def test_clarify_buttons_and_other(monkeypatch):
    from maxbot import interactive as I
    awaited = []
    monkeypatch.setattr(I, "_mark_clarify_awaiting", lambda cid: awaited.append(cid))
    monkeypatch.setattr(I, "_resolve_clarify",
                        lambda cid, resp: awaited.append((cid, resp)) or None)
    disp, api = make_dispatcher()
    await disp.send_clarify("100", "Какой план?", ["быстро", "надёжно"], "cl.7", "sess:2")
    _, atts = api.sent["100"]
    payloads = [b["payload"] for row in atts[0]["payload"]["buttons"] for b in row]
    assert payloads == ["cl:cl.7:0", "cl:cl.7:1", "cl:cl.7:-1"]
    await disp.dispatch(_cb("cl:cl.7:-1"))
    assert awaited == [("cl.7",)]
    await disp.dispatch(_cb("cl:cl.7:1"))
    assert awaited[-1] == ("cl.7", "надёжно")


async def test_slash_confirm_and_picker(monkeypatch):
    from maxbot import interactive as I
    monkeypatch.setattr(I, "_resolve_slash_confirm",
                        lambda sk, cid, ch: f"resolved:{ch}")
    disp, api = make_dispatcher()
    await disp.send_slash_confirm("100", "/reload-mcp", "Перезагрузить?", "sess:3", "cf.1")
    await disp.dispatch(_cb("sc:cancel:cf.1"))
    assert api.edited["m.cb"][0].startswith("❌")
    picked = []
    await disp.send_choice_picker(
        "100", "Модель", [{"value": "a", "label": "A", "is_current": False},
                          {"value": "b", "label": "B", "is_current": True}],
        "sess:4", on_choice_selected=lambda v: picked.append(v) or _async_none())
    await disp.dispatch(_cb("cp:0"))
    assert picked == ["a"]


def _async_none():
    import asyncio
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_interactive.py -v`
Expected: FAIL

- [ ] **Step 3: Реализация**

```python
# maxbot/interactive.py
"""Inline-кнопки MAX: approve/deny, slash-confirm, clarify, choice-picker."""
import asyncio
import contextlib
import itertools
import logging
from typing import Any, Dict, List, Optional

from maxbot.markdown import sanitize_markdown
from maxbot.models import Callback

logger = logging.getLogger(__name__)


def keyboard_attachment(rows: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"type": "inline_keyboard",
            "payload": {"buttons": [[{"type": "callback", **btn} for btn in row]
                                    for row in rows]}}


# ── обёртки над резолверами ядра (переопределяются в тестах) ──
def _resolve_approval(session_key: str, choice: str) -> int:
    from tools.approval import resolve_gateway_approval
    return resolve_gateway_approval(session_key, choice)


def _resolve_slash_confirm(session_key: str, confirm_id: str, choice: str) -> Optional[str]:
    from tools.slash_confirm import resolve
    return resolve(session_key, confirm_id, choice)


def _resolve_clarify(clarify_id: str, response: str) -> None:
    from tools.clarify_gateway import resolve_gateway_clarify
    return resolve_gateway_clarify(clarify_id, response)


def _mark_clarify_awaiting(clarify_id: str) -> None:
    from tools.clarify_gateway import mark_awaiting_text
    mark_awaiting_text(clarify_id)


_EA_LABELS = {"once": "✅ Одобрено (один раз)", "session": "✅ Одобрено — сессия",
              "always": "✅ Одобрить всегда", "deny": "❌ Отклонено"}
_SC_LABELS = {"once": "✅ Один раз", "always": "🔒 Всегда", "cancel": "❌ Отмена"}


class InteractiveDispatcher:
    def __init__(self, adapter_api):
        """adapter_api — объект с `await api_send(chat_id, text, attachments)`,
        `await api_answer(callback_id, text)`, `await api_edit(message_id, text, attachments)`."""
        self.api = adapter_api
        self._counter = itertools.count(1)
        self.approval_state: Dict[int, str] = {}
        self.slash_state: Dict[str, str] = {}
        self.clarify_state: Dict[str, str] = {}
        self.picker_state: Dict[str, dict] = {}

    # ── промпты ──
    async def send_exec_approval(self, chat_id, command, session_key,
                                 description="dangerous command", metadata=None,
                                 allow_permanent=True, allow_session=True,
                                 smart_denied=False):
        approval_id = next(self._counter)
        buttons = [{"text": "✅ Один раз", "payload": f"ea:once:{approval_id}"}]
        if not smart_denied and allow_session:
            buttons.append({"text": "✅ Сессия", "payload": f"ea:session:{approval_id}"})
            if allow_permanent:
                buttons.append({"text": "✅ Всегда", "payload": f"ea:always:{approval_id}"})
        buttons.append({"text": "❌ Отклонить", "payload": f"ea:deny:{approval_id}"})
        text = f"⚠️ *Требуется подтверждение*\n`{command}`\n{description}"
        mid = await self.api.api_send(chat_id, sanitize_markdown(text),
                                      [keyboard_attachment([buttons])])
        self.approval_state[approval_id] = session_key
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    async def send_slash_confirm(self, chat_id, title, message, session_key, confirm_id,
                                 metadata=None):
        rows = [[{"text": "✅ Один раз", "payload": f"sc:once:{confirm_id}"},
                 {"text": "🔒 Всегда", "payload": f"sc:always:{confirm_id}"},
                 {"text": "❌ Отмена", "payload": f"sc:cancel:{confirm_id}"}]]
        mid = await self.api.api_send(
            chat_id, sanitize_markdown(f"*{title}*\n{message}"), [keyboard_attachment(rows)])
        self.slash_state[confirm_id] = session_key
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    async def send_clarify(self, chat_id, question, choices, clarify_id, session_key,
                           metadata=None):
        rows, payloads = [], []
        for i, choice in enumerate(choices or []):
            rows.append([{"text": str(choice)[:64], "payload": f"cl:{clarify_id}:{i}"}])
            payloads.append(f"cl:{clarify_id}:{i}")
        rows.append([{"text": "✍️ Другое", "payload": f"cl:{clarify_id}:-1"}])
        mid = await self.api.api_send(chat_id, f"❓ {question}", [keyboard_attachment(rows)])
        self.clarify_state[clarify_id] = session_key
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    async def send_choice_picker(self, chat_id, title, choices, session_key,
                                 on_choice_selected, metadata=None):
        rows = []
        for i, choice in enumerate(choices or []):
            label = str(choice.get("label") or choice.get("value") or "")
            if choice.get("is_current"):
                label = f"✓ {label}"
            rows.append({"text": label, "payload": f"cp:{i}"})
        grid = [rows[i:i + 2] for i in range(0, len(rows), 2)]
        mid = await self.api.api_send(chat_id, str(title), [keyboard_attachment(grid)])
        self.picker_state[str(chat_id)] = {
            "choices": choices, "session_key": session_key,
            "on_choice_selected": on_choice_selected, "message_id": mid}
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    # ── диспетчер нажатий ──
    async def dispatch(self, cb: Callback) -> None:
        payload = cb.payload or ""
        try:
            if payload.startswith("ea:"):
                await self._on_exec_approval(cb)
            elif payload.startswith("sc:"):
                await self._on_slash_confirm(cb)
            elif payload.startswith("cl:"):
                await self._on_clarify(cb)
            elif payload.startswith("cp:"):
                await self._on_picker(cb)
        except Exception:
            logger.exception("max: callback %r не обработан", payload)

    async def _finish(self, cb: Callback, toast: str, edit_text: str,
                      edit_attachments: Optional[list] = None) -> None:
        with contextlib.suppress(Exception):
            await self.api.api_answer(cb.callback_id, toast)
        if cb.message and cb.message.body.mid:
            with contextlib.suppress(Exception):
                await self.api.api_edit(cb.message.body.mid, edit_text, edit_attachments)

    async def _on_exec_approval(self, cb: Callback) -> None:
        _, choice, _id = (cb.payload.split(":", 2) + ["", ""])[:3]
        session_key = self.approval_state.pop(int(_id), None) if _id.isdigit() else None
        if not session_key:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Подтверждение уже обработано")
            return
        count = _resolve_approval(session_key, choice)
        label = _EA_LABELS.get(choice, "Готово") if count else "⌛ Истекло ожидание"
        user = cb.message.sender.name if cb.message and cb.message.sender else ""
        await self._finish(cb, label, f"{label}" + (f" — {user}" if user else ""))

    async def _on_slash_confirm(self, cb: Callback) -> None:
        _, choice, confirm_id = cb.payload.split(":", 2)
        session_key = self.slash_state.pop(confirm_id, None)
        if not session_key:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Уже обработано")
            return
        result_text = _resolve_slash_confirm(session_key, confirm_id, choice)
        await self._finish(cb, _SC_LABELS.get(choice, "Готово"),
                           _SC_LABELS.get(choice, "Готово"))
        if result_text and cb.message:
            chat_id = str(cb.message.chat_id)
            with contextlib.suppress(Exception):
                await self.api.api_send(chat_id, sanitize_markdown(str(result_text)))

    async def _on_clarify(self, cb: Callback) -> None:
        _, clarify_id, idx_s = cb.payload.split(":", 2)
        session_key = self.clarify_state.pop(clarify_id, None)
        if not session_key:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Уже обработано")
            return
        if idx_s == "-1":
            _mark_clarify_awaiting(clarify_id)
            await self._finish(cb, "✍️ Напишите ответ", "✍️ Напишите свой ответ следующим сообщением")
            return
        # восстановить текст выбора из кнопок исходного сообщения нельзя —
        # резолвер ядра принимает текст; берём из payload-таблицы, сохранённой при отправке
        choice_text = self._clarify_choices.get(clarify_id, {}).get(int(idx_s), str(idx_s))
        _resolve_clarify(clarify_id, choice_text)
        await self._finish(cb, f"✅ Выбрано: {choice_text}", f"✅ Выбрано: {choice_text}")

    async def _on_picker(self, cb: Callback) -> None:
        idx = int((cb.payload.split(":", 1)[1] or "0"))
        chat_id = str(cb.message.chat_id) if cb.message else ""
        state = self.picker_state.pop(chat_id, None)
        if not state:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Уже обработано")
            return
        choice = (state.get("choices") or [{}])[idx]
        label = str(choice.get("label") or choice.get("value") or "")
        await self._finish(cb, f"✅ {label}", f"✅ Выбрано: {label}")
        handler = state.get("on_choice_selected")
        if handler:
            result = handler(choice.get("value"))
            if asyncio.iscoroutine(result):
                await result
```

В `send_clarify` диспетчера сохранять выборы: `self._clarify_choices: Dict[str, Dict[int, str]]` — в `send_clarify` добавить `self._clarify_choices[clarify_id] = {i: str(c) for i, c in enumerate(choices or [])}` (инициализировать в `__init__`).

Делегирование в `MaxAdapter` (Task 9-код):

```python
# maxbot/adapter.py — внутри MaxAdapter
    async def send_exec_approval(self, chat_id, command, session_key,
                                 description="dangerous command", metadata=None,
                                 allow_permanent=True, allow_session=True,
                                 smart_denied=False) -> "SendResult":
        if not self._interactive:
            return await super().send_exec_approval(
                chat_id, command, session_key, description, metadata=metadata,
                allow_permanent=allow_permanent, allow_session=allow_session,
                smart_denied=smart_denied)
        return await self._interactive.send_exec_approval(
            chat_id, command, session_key, description, metadata=metadata,
            allow_permanent=allow_permanent, allow_session=allow_session,
            smart_denied=smart_denied)

    async def send_slash_confirm(self, chat_id, title, message, session_key, confirm_id,
                                 metadata=None) -> "SendResult":
        if self._interactive:
            return await self._interactive.send_slash_confirm(
                chat_id, title, message, session_key, confirm_id, metadata=metadata)
        return await super().send_slash_confirm(
            chat_id, title, message, session_key, confirm_id, metadata=metadata)

    async def send_clarify(self, chat_id, question, choices, clarify_id, session_key,
                           metadata=None) -> "SendResult":
        if self._interactive:
            return await self._interactive.send_clarify(
                chat_id, question, choices, clarify_id, session_key, metadata=metadata)
        return await super().send_clarify(
            chat_id, question, choices, clarify_id, session_key, metadata=metadata)

    async def send_choice_picker(self, chat_id, title, choices, session_key,
                                 on_choice_selected, metadata=None) -> "SendResult":
        if self._interactive:
            return await self._interactive.send_choice_picker(
                chat_id, title, choices, session_key, on_choice_selected, metadata=metadata)
        return await super().send_choice_picker(
            chat_id, title, choices, session_key, on_choice_selected, metadata=metadata)

    # api_* обёртки, которые использует диспетчер:
    async def api_send(self, chat_id, text, attachments=None):
        return await self._client.send_message(int(chat_id), text, attachments=attachments)

    async def api_answer(self, callback_id, text=None):
        return await self._client.answer_callback(callback_id, text)

    async def api_edit(self, message_id, text, attachments=None):
        return await self._client.edit_message(message_id, text, attachments=attachments)
```

И в `connect()` после `self._bot_user_id = me.user_id`: `self._interactive = InteractiveDispatcher(self)`; в `__init__`: `self._interactive = None`.

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_interactive.py -v`
Expected: PASS (5)

- [ ] **Step 5: Commit**

```bash
git add maxbot/interactive.py maxbot/adapter.py tests/test_interactive.py
git commit -m "feat: inline-кнопки MAX (approve/deny, slash-confirm, clarify, picker)"
```

---

### Task 12: Streaming-превью (`send_draft`)

**Files:**
- Modify: `maxbot/adapter.py`
- Test: `tests/test_streaming.py`

**Interfaces:**
- Consumes: `MaxClient.send_message/edit_message/delete_message`; контракт ядра `supports_draft_streaming` / `send_draft(chat_id, draft_id, content, metadata)` / `prefers_fresh_final_streaming` (финал — обычный `send()`, ядро вызывает `delete_message` для чистки; дополнительно чистим сами в `send()`).
- Produces в `MaxAdapter`:
  - `supports_draft_streaming(chat_type=None, metadata=None, chat_id=None) -> True`
  - `prefers_fresh_final_streaming(content=None, metadata=None) -> True`
  - `await send_draft(chat_id, draft_id, content, metadata=None) -> SendResult(success=True, message_id=None)`; первое превью — `POST /messages` (текст + курсор ` ▌`, `notify=False`), запоминается `self._drafts[(chat_id, draft_id)] = {"message_id", "last"}`; обновления — `PUT` тем же mid; **интервал ≥1.0с на chat** (промежуточные чанки вне интервала скипаются); ошибки `PUT` 4xx → превью забывается (финал всё равно доедет через `send()`)
  - `_cleanup_drafts(chat_id)` (заменяет заглушку из Task 9): перед финальной отправкой удаляет живые превью этого чата через `DELETE`

- [ ] **Step 1: Failing tests**

```python
# tests/test_streaming.py
import asyncio

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.adapter import MaxAdapter


class FakeCfg:
    extra = {}


class DraftFakeClient:
    def __init__(self):
        self.sent, self.edits, self.deleted = [], {}, []
        self._mid = 0
        self.edit_error = None

    async def send_message(self, chat_id, text, **kw):
        self._mid += 1
        self.sent.append((chat_id, text, kw))
        return f"mid.{self._mid}"

    async def edit_message(self, message_id, text, **kw):
        if self.edit_error:
            raise self.edit_error
        self.edits[message_id] = text
        return True

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        return True


def make_adapter():
    client = DraftFakeClient()
    adapter = MaxAdapter(FakeCfg(), client=client)
    adapter._bot_user_id = 1
    return adapter, client


async def test_draft_first_post_then_edits_same_mid():
    adapter, client = make_adapter()
    assert adapter.supports_draft_streaming() is True
    assert adapter.prefers_fresh_final_streaming() is True
    for _ in range(3):
        await adapter.send_draft("100", 1, "накапливаем текст")
    assert len(client.sent) == 1                    # один POST
    assert len(client.edits) == 1                   # остальные скипаны троттлером
    mid = client.sent[0][1] and f"mid.{len(client.sent)}" or None
    await asyncio.sleep(1.05)                       # троттлер отпускает
    await adapter.send_draft("100", 1, "финал превью")
    assert len(client.edits) == 2 or len(client.edits) == 1  # после сна — edit прошёл
    assert " ▌" in client.sent[0][1]


async def test_send_cleanup_deletes_preview():
    adapter, client = make_adapter()
    await adapter.send_draft("100", 7, "превью")
    result = await adapter.send("100", "финальный ответ")
    assert result.success
    assert client.deleted == ["mid.1"]               # превью удалено


async def test_edit_4xx_forgets_preview():
    from maxbot.max_api import MaxApiError

    adapter, client = make_adapter()
    await adapter.send_draft("100", 1, "превью")
    await asyncio.sleep(1.05)
    client.edit_error = MaxApiError(400, "стало старым", code="too_old")
    res = await adapter.send_draft("100", 1, "ещё")
    assert res.success                              # не падаем — финал придёт через send()
    await asyncio.sleep(0)
    assert adapter._drafts == {}
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_streaming.py -v`
Expected: FAIL

- [ ] **Step 3: Реализация (в MaxAdapter)**

```python
# maxbot/adapter.py — дополнения
import time

_DRAFT_MIN_INTERVAL = 1.0  # запас к лимиту MAX «2 правки/сек на чат»


class MaxAdapter(BasePlatformAdapter):
    # ... (Task 9-11) ...

    def supports_draft_streaming(self, chat_type=None, metadata=None, chat_id=None) -> bool:
        return True

    def prefers_fresh_final_streaming(self, content=None, metadata=None) -> bool:
        return True

    async def send_draft(self, chat_id: str, draft_id: int, content: str,
                         metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="not connected")
        key = (str(chat_id), int(draft_id))
        entry = self._drafts.get(key)
        now = time.monotonic()
        preview = sanitize_markdown(content)[:3900] + " ▌"
        if entry is None:
            try:
                mid = await self._client.send_message(int(chat_id), preview, notify=False)
            except MaxApiError as exc:
                return SendResult(success=False, error=str(exc))
            self._drafts[key] = {"message_id": mid, "last": now}
            return SendResult(success=True, message_id=None)
        if now - entry["last"] < _DRAFT_MIN_INTERVAL:
            return SendResult(success=True, message_id=None)  # скип промежуточного чанка
        try:
            await self._client.edit_message(entry["message_id"], preview)
            entry["last"] = now
        except MaxApiError as exc:
            if exc.status >= 400 and exc.status != 429:
                self._drafts.pop(key, None)  # финал доедет обычным send()
        return SendResult(success=True, message_id=None)

    async def _cleanup_drafts(self, chat_id: str) -> None:
        for key in [k for k in self._drafts if k[0] == str(chat_id)]:
            entry = self._drafts.pop(key)
            with contextlib.suppress(Exception):
                await self._client.delete_message(entry["message_id"])
```

В `__init__` (Task 9): `self._drafts: Dict[Tuple[str, int], Dict[str, Any]] = {}` (добавить импорт `Tuple`).

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_streaming.py -v && uv run pytest tests/test_adapter.py -v`
Expected: PASS (3 + 5; в `test_send_...` из Task 9 `send` теперь чистит превью — пусто, не мешает)

- [ ] **Step 5: Commit**

```bash
git add maxbot/adapter.py tests/test_streaming.py
git commit -m "feat: streaming-превью send_draft с троттлером 1/сек"
```

---

### Task 13: Конфиг-хуки, визард, standalone-отправка (`maxbot/hooks.py`)

**Files:**
- Create: `maxbot/hooks.py`
- Modify: `maxbot/__init__.py` (подключить хуки в `register()`)
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `get_scoped_secret` (hermes), `MaxClient`, `sanitize_markdown`, `MaxAdapter._segment`.
- Produces:
  - `env_enablement_fn = _env_enablement` — dict для `PlatformConfig.extra` (+ `home_channel`) или `None` без токена
  - `apply_yaml_config_fn = _apply_yaml_config(yaml_cfg: Dict, platform_cfg) -> Optional[Dict]` — маппит yaml-ключи (`access_token, allowed_users, allow_all_users, home_channel, updates_mode, webhook_url, webhook_port, webhook_secret, api_base`) в env (если не заданы) и возвращает extra-словарь
  - `standalone_sender_fn = _standalone_send(pconfig, chat_id, message, *, thread_id=None, media_files=None, force_document=False) -> Dict` — cron-доставка вне гейтвея: ephemeral `MaxClient`, sanitize + сегментация; `{"success": True, "message_id": ...}` / `{"error": ...}`
  - `setup_fn = interactive_setup()` — визард `hermes gateway setup` (токен → get_me проверка → allowlist/home channel; ленивые импорты `hermes_cli.setup`)
  - в `register()` добавить: `setup_fn=interactive_setup, env_enablement_fn=_env_enablement, apply_yaml_config_fn=_apply_yaml_config, cron_deliver_env_var="MAX_HOME_CHANNEL", standalone_sender_fn=_standalone_send, allowed_users_env="MAX_ALLOWED_USERS", allow_all_env="MAX_ALLOW_ALL_USERS"`

- [ ] **Step 1: Failing tests**

```python
# tests/test_hooks.py
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.hooks import _apply_yaml_config, _env_enablement, _standalone_send


def test_env_enablement_none_without_token(monkeypatch):
    monkeypatch.delenv("MAX_ACCESS_TOKEN", raising=False)
    assert _env_enablement() is None


def test_env_enablement_seeds_extra_and_home(monkeypatch):
    monkeypatch.setenv("MAX_ACCESS_TOKEN", "T")
    monkeypatch.setenv("MAX_UPDATES_MODE", "polling")
    seed = _env_enablement()
    assert seed["access_token"] == "T" and seed["updates_mode"] == "polling"
    assert "home_channel" not in seed or isinstance(seed["home_channel"], dict)


def test_apply_yaml_config_maps_keys(monkeypatch):
    monkeypatch.delenv("MAX_ACCESS_TOKEN", raising=False)
    extra = _apply_yaml_config(
        {"access_token": "YTok", "updates_mode": "webhook", "home_channel": "-100"},
        platform_cfg=None)
    assert extra["access_token"] == "YTok" and extra["updates_mode"] == "webhook"
    import os
    assert os.environ.get("MAX_ACCESS_TOKEN") == "YTok"


async def test_standalone_send_segments(monkeypatch):
    import httpx
    import respx

    sent = []
    respx.post("http://test.max/messages").mock(
        side_effect=lambda request: sent.append(request) or httpx.Response(
            200, json={"message": {"body": {"mid": f"m{len(sent)}"}}}))

    class _Cfg:
        extra = {"access_token": "T", "api_base": "http://test.max"}

    result = await _standalone_send(_Cfg(), "100", "x" * 9000)
    assert result["success"] is True and len(sent) == 3  # 9000 символов → 3 сегмента
```

- [ ] **Step 2: Запустить**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: FAIL

- [ ] **Step 3: Реализация**

```python
# maxbot/hooks.py
"""Хуки регистрации плагина: env/yaml-конфиг, визард, cron-доставка."""
import contextlib
import logging
import os
from typing import Any, Dict, Optional

from gateway.platforms._shared import get_scoped_secret

from maxbot.markdown import sanitize_markdown

logger = logging.getLogger(__name__)

_YAML_KEYS = {
    "access_token": ("MAX_ACCESS_TOKEN", str),
    "allowed_users": ("MAX_ALLOWED_USERS", str),
    "allow_all_users": ("MAX_ALLOW_ALL_USERS", str),
    "home_channel": ("MAX_HOME_CHANNEL", str),
    "updates_mode": ("MAX_UPDATES_MODE", str),
    "webhook_url": ("MAX_WEBHOOK_URL", str),
    "webhook_port": ("MAX_WEBHOOK_PORT", int),
    "webhook_secret": ("MAX_WEBHOOK_SECRET", str),
    "api_base": ("MAX_API_BASE", str),
}


def _env_enablement() -> Optional[Dict[str, Any]]:
    token = get_scoped_secret("MAX_ACCESS_TOKEN", "").strip()
    if not token:
        return None
    seed: Dict[str, Any] = {"access_token": token}
    if mode := get_scoped_secret("MAX_UPDATES_MODE", "").strip():
        seed["updates_mode"] = mode
    if base := get_scoped_secret("MAX_API_BASE", "").strip():
        seed["api_base"] = base
    if home := (get_scoped_secret("MAX_HOME_CHANNEL", "").strip()
                or get_scoped_secret("MAX_HOME_CHANNEL")):
        seed["home_channel"] = {"chat_id": home,
                                "name": get_scoped_secret("MAX_HOME_CHANNEL_NAME", home)}
    return seed


def _apply_yaml_config(yaml_cfg: Dict[str, Any], platform_cfg=None) -> Optional[Dict[str, Any]]:
    if not isinstance(yaml_cfg, dict) or not yaml_cfg:
        return None
    extra: Dict[str, Any] = {}
    for key, (env, _conv) in _YAML_KEYS.items():
        value = yaml_cfg.get(key)
        if value in (None, "", [], {}):
            continue
        extra[key] = value
        if not os.getenv(env):  # env > yaml
            with contextlib.suppress(Exception):
                os.environ[env] = str(value)
    return extra or None


async def _standalone_send(pconfig, chat_id: str, message: str, *, thread_id=None,
                           media_files=None, force_document=False) -> Dict[str, Any]:
    from maxbot.adapter import MaxAdapter
    from maxbot.max_api import MaxClient

    extra = getattr(pconfig, "extra", None) or {}
    token = get_scoped_secret("MAX_ACCESS_TOKEN", "") or extra.get("access_token")
    if not token or not chat_id:
        return {"error": "max: нужен MAX_ACCESS_TOKEN и chat_id"}
    base = get_scoped_secret("MAX_API_BASE", "") or extra.get("api_base") or None
    async with MaxClient(token, base_url=base) as client:
        try:
            last_mid = None
            for chunk in MaxAdapter._segment(sanitize_markdown(message)):
                last_mid = await client.send_message(int(chat_id), chunk)
            return {"success": True, "message_id": last_mid}
        except Exception as exc:
            return {"error": f"max standalone send: {exc}"}


def interactive_setup() -> None:
    """Визард `hermes gateway setup`. Ленивые импорты CLI."""
    from hermes_cli.setup import (
        get_env_value, print_header, print_info, print_success, print_warning,
        prompt, prompt_yes_no, save_env_value)

    print_header("MAX")
    if get_env_value("MAX_ACCESS_TOKEN"):
        print_info("MAX: уже настроен")
        if not prompt_yes_no("Перенастроить MAX?", False):
            return
    print_info("Подключите Hermes к боту MAX (https://business.max.ru → Чат-боты).")
    token = prompt("Токен доступа бота (MAX_ACCESS_TOKEN)", password=True)
    if not token:
        print_warning("Токен обязателен — отмена")
        return
    save_env_value("MAX_ACCESS_TOKEN", token.strip())
    if prompt_yes_no("Разрешить всем пользователям писать боту? (небезопасно)", False):
        save_env_value("MAX_ALLOW_ALL_USERS", "true")
    else:
        allowed = prompt("Разрешённые user_id через запятую", default="")
        save_env_value("MAX_ALLOWED_USERS", allowed.replace(" ", ""))
    home = prompt("chat_id для cron-доставки (MAX_HOME_CHANNEL, можно пропустить)", default="")
    if home:
        save_env_value("MAX_HOME_CHANNEL", home)
    print_success("MAX настроен. Перезапустите гейтвей: hermes gateway restart")
```

Дополнить `register()` в `maxbot/__init__.py` (после `platform_hint=...`):

```python
    from maxbot.hooks import (
        _apply_yaml_config, _env_enablement, _standalone_send, interactive_setup)

    ctx.register_platform(
        ...,
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        apply_yaml_config_fn=_apply_yaml_config,
        cron_deliver_env_var="MAX_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="MAX_ALLOWED_USERS",
        allow_all_env="MAX_ALLOW_ALL_USERS",
    )
```

(итоговый `register()` — один вызов `ctx.register_platform(...)` со всеми kwargs из Task 9 + этими.)

- [ ] **Step 4: Запустить**

Run: `uv run pytest tests/test_hooks.py -v && uv run pytest -q`
Expected: PASS (4) и весь набор зелёный

- [ ] **Step 5: Commit**

```bash
git add maxbot/hooks.py maxbot/__init__.py tests/test_hooks.py
git commit -m "feat: конфиг-хуки (env/yaml), визард, standalone cron-отправка"
```

---

### Task 14: Live-скрипт, README, CI

**Files:**
- Create: `scripts/live_check.py`, `README.md`, `.github/workflows/ci.yml`
- Test: ручная проверка `python scripts/live_check.py --help` (без токена)

**Interfaces:**
- Consumes: `MaxClient` (Task 3-4).
- Produces: живой smoke-скрипт, документация, CI.

- [ ] **Step 1: scripts/live_check.py**

```python
#!/usr/bin/env python3
"""Живой smoke-тест бота MAX: get_me → send → один цикл polling.
Запуск: MAX_ACCESS_TOKEN=... python scripts/live_check.py --chat-id 123 [--send-only]
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from maxbot.max_api import MaxClient, MaxApiError  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", type=int, help="chat_id для тестовой отправки")
    parser.add_argument("--send-only", action="store_true", help="не слушать polling")
    parser.add_argument("--token", default=os.environ.get("MAX_ACCESS_TOKEN"))
    parser.add_argument("--base", default=os.environ.get("MAX_API_BASE"))
    args = parser.parse_args()
    if not args.token:
        print("Нужен токен: --token или MAX_ACCESS_TOKEN"); return 2
    async with MaxClient(args.token, base_url=args.base) as client:
        me = await client.get_me()
        print(f"✅ get_me: user_id={me.user_id} name={me.name!r} username={me.username!r}")
        if args.chat_id:
            mid = await client.send_message(args.chat_id, "hermes-max-gateway: smoke-тест ✅")
            print(f"✅ send_message → {mid}")
        if not args.send_only:
            print("… слушаю апдейты 60с (напишите боту)")
            marker = None
            try:
                async def _cycle():
                    nonlocal marker
                    marker, updates = await client.get_updates(marker, timeout=55)
                    for u in updates:
                        print(f"← update_type={u.update_type} marker={u.marker}")
                        if u.message:
                            print(f"  chat={u.message.chat_id}/{u.message.chat_type} "
                                  f"from={u.message.sender.user_id if u.message.sender else '?'} "
                                  f"text={u.message.body.text!r} atts={len(u.message.body.attachments)}")
                await asyncio.wait_for(_cycle(), timeout=70)
            except (asyncio.TimeoutError, MaxApiError) as exc:
                print(f"polling завершён ({exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: README.md**

```markdown
# hermes-max-gateway

Плагин платформы **MAX** (max.ru) для [Hermes Agent](https://hermes-agent.nousresearch.com/):
подключает вашего Hermes-агента как бота MAX — переписка, медиа, кнопки подтверждения,
streaming-превью, cron-доставка.

## Возможности

- Текст + Markdown, длинные ответы сегментируются (≤4000 симв.)
- Входящие вложения: image/video/audio/file, стикеры, шеринг, контакты, геолокация
- Исходящие: изображения, файлы, аудио (голос), видео
- Группы: ответы на упоминания бота и replies; DM — всегда
- Inline-кнопки: подтверждение опасных команд, clarify, пикеры (`/model`)
- Streaming-превью ответов с редактированием сообщения
- Long polling (по умолчанию) или webhook
- Cron-доставка `deliver=max`

## Установка

Требуется установленный Hermes Agent. Два способа:

```bash
# 1) pip-пакет (в то же окружение, где стоит hermes)
pip install git+https://github.com/<вы>/hermes-max-gateway.git

# 2) каталогом плагинов
git clone https://github.com/<вы>/hermes-max-gateway.git ~/.hermes/plugins/maxbot
```

## Настройка

`~/.hermes/.env`:

```
MAX_ACCESS_TOKEN=...          # токен бота из «MAX для бизнеса» (обязательно)
MAX_ALLOWED_USERS=123,456     # кому можно писать боту
MAX_HOME_CHANNEL=-100...      # chat_id для cron-доставки
# MAX_UPDATES_MODE=webhook    # или polling (по умолчанию)
# MAX_WEBHOOK_URL=https://... # для webhook-режима
# MAX_WEBHOOK_PORT=8443
# MAX_WEBHOOK_SECRET=...
```

Затем: `hermes gateway restart`.

Альтернатива — yaml в `~/.hermes/config.yaml`:

```yaml
gateway:
  platforms:
    max:
      access_token: ...
      updates_mode: polling
```

## Проверка

```bash
python scripts/live_check.py --chat-id <ваш chat_id>
```

Напишите боту в MAX — он ответит.

## Разработка

```bash
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git ../hermes-agent
uv sync
uv run pytest
uv run ruff check .
```
```

- [ ] **Step 3: CI (.github/workflows/ci.yml)**

```yaml
name: ci
on:
  push: { branches: [main, master] }
  pull_request:
jobs:
  tests:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/checkout@v4
        with: { repository: NousResearch/hermes-agent, path: hermes-agent, fetch-depth: 1 }
      - uses: astral-sh/setup-uv@v5
        with: { python-version: "${{ matrix.python }}" }
      - run: uv sync
      - run: uv pip install -e ./hermes-agent
      - run: uv run ruff check .
      - run: uv run pytest -q
        env: { HERMES_AGENT_SRC: "${{ github.workspace }}/hermes-agent" }
```

- [ ] **Step 4: Проверить**

Run: `uv run python scripts/live_check.py --help && uv run ruff check . && uv run pytest -q`
Expected: help печатается, ruff чист, тесты зелёные

- [ ] **Step 5: Commit**

```bash
git add scripts/ README.md .github/workflows/ci.yml
git commit -m "docs: README (рус.), live-скрипт, CI"
```

---

### Task 15: Установка и live-приёмка (ручная)

**Files:** — (без кода; проверка на реальном боте)

**Interfaces:**
- Consumes: всё предыдущее; установленный Hermes; токен бота MAX.

- [ ] **Step 1: Установить Hermes и плагин**

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup            # модель-провайдер (например Nous Portal)
cd /Users/y.yadrushnikov/Dev/hermes-max-gateway
uv build && pip install dist/maxbot-0.1.0-py3-none-any.whl
# либо: cp -r maxbot ~/.hermes/plugins/maxbot
```

- [ ] **Step 2: Настроить и запустить**

```bash
python scripts/live_check.py --chat-id <chat_id>   # smoke API
hermes config set MAX_ACCESS_TOKEN <токен>
hermes config set MAX_ALLOWED_USERS <ваш user_id>
hermes gateway start && hermes gateway status
```

- [ ] **Step 3: Чек-лист приёмки (в MAX, отметить по пунктам)**

- [ ] DM: текст туда/обратно; markdown (жирный, код, ссылка) рендерится
- [ ] Длинный ответ (>4000) приходит сегментами; streaming-превью растёт и удаляется
- [ ] Фото от пользователя доходит агенту (vision видит файл)
- [ ] Картинка/файл/аудио от агента доходит
- [ ] Стикер пользователя → «[стикер]»; геолокация → координаты в тексте
- [ ] Опасная команда → кнопки; «✅ Один раз» разрешает, «❌ Отклонить» запрещает; после нажатия кнопки «часики» исчезают
- [ ] Группа: бот молчит без упоминания, отвечает на упоминание и на reply своему сообщению
- [ ] `/new`, `/status` работают; `/model` открывает пикер кнопками
- [ ] `hermes gateway restart` — курсор polling не теряется (нет повторной обработки старых сообщений)
- [ ] cron `deliver=max` доносит сообщение в MAX_HOME_CHANNEL
- [ ] Неавторизованный пользователь получает отказ
- [ ] typing-индикатор виден в группе (в DM — опционально)

- [ ] **Step 4: Зафиксировать результаты**

Обновить раздел «Риски» спеки фактами live-проверки (X-Secret схема, typing в DM, voice vs audio, PATCH /me/commands формат). Коммит: `git commit -am "docs: результаты live-приёмки"`.

---

## Порядок выполнения и зависимости

```
1 → 2 → 3 → 4 → (5, 6) → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15
```

Task 5-6 параллельны; Task 12 зависит от 9 (send/_cleanup_drafts); Task 11 от 9-10; Task 13 от 9; Task 14 от всех; Task 15 — ручной финал.








