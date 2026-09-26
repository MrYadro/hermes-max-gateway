# MAX: официальные thread-сессии комментариев + переключалка профилей

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Перевести ветки комментариев каналов MAX с фейковых групп на официальный thread-механизм ядра; (2) добавить переключалку профилей (`source.profile`) в одном чате — у каждого профиля свой SOUL.md/скиллы/модель.

**Architecture:** Фаза 1: `_on_comment` строит source с `chat_id=channel_id, chat_type="channel", thread_id=post_id` → общий thread-session (как Discord), outbound-комментарии через `metadata["thread_id"]`, память канала — из ключа сессии (регstry `_channel_posts.json` удаляем). Фаза 2: модуль `profile_switch.py` хранит per-chat карту `chat_id → профиль` (JSON), команда `/assistant` показывает пикер (готовый `send_choice_picker`), `MaxAdapter.build_source` переопределён и штампует `source.profile` — официальный приоритет №1 маршрутизации ядра (base.py:2253, run.py:4290), требует `multiplex_profiles: true`.

**Tech Stack:** Python 3.11+, pytest-asyncio, aiohttp/httpx (уже в зависимостях), ядро hermes-agent (`gateway/platforms/base.py`).

**Spec:** Обсуждение в сессии 2026-09-26: «честные профили» через `source.profile` + аудит хаков (thread_id для комментариев).

## Global Constraints

- Тесты: `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q`; линт: `.venv/bin/python -m ruff check .`
- Новое поведение — только RED→GREEN; фикстуры только синтетические (chat_id вида 500/777, «Петя»)
- Коммиты на русском, conventional (`feat:`, `fix:`, `refactor:`)
- PII: в тестах/файлах никаких реальных id/токенов/имён
- Ядро hermes-agent НЕ трогаем — только maxbot/
- Совместимость: старые сессии комментариев (ключ `group:<post>:<user>`) остаются в сторе как есть — новые сообщения ветки пойдут в новую сессию `channel:<канал>:<пост>` (документированный разрыв)

## Review Focus

1. **Регресс: обычные группы и ЛС не должны сменить ключ сессии** — тест: group-сообщение → ключ содержит `group:` и прежний chat_id (Task 2 step 1c).
2. **`metadata["thread_id"]` без мультиплекса/в ЛС** — thread_id устанавливается ТОЛЬКО для комментариев; тест: обычный DM-send не превращается в comment (Task 3).
3. **Карта профилей указывает на удалённый профиль** — штамп не ставим, сообщение идёт в дефолт (Task 6).
4. **Мультиплекс выключен** — `/assistant` отвечает подсказкой, штампов нет, обычная работа не ломается (Task 6).
5. **Комментарий в канале без карты/мультиплекса** — прежнее поведение: общая thread-сессия канала (Task 2).

---

### Task 1: `_on_comment` — официальный source (thread_id, chat_type="channel")

**Files:**
- Modify: `maxbot/adapter.py` (`_on_comment`, ~строка 789-809)
- Test: `tests/test_channel.py`

**Interfaces:**
- Consumes: `self.build_source(chat_id, chat_name, chat_type, user_id, user_name, thread_id, parent_chat_id)` — сигнатура базового класса (base.py:4257)
- Produces: событие комментария с `source.chat_id=str(channel_id)`, `source.chat_type="channel"`, `source.thread_id=str(post_id)`, `source.parent_chat_id=str(channel_id)`

- [ ] **Step 1: RED — обновить тест маршрутизации**

В `tests/test_channel.py` заменить `test_comment_creates_post_session_and_routes_send` на:

```python
async def test_comment_uses_official_thread_source():
    import asyncio

    import pytest as _pytest

    _pytest.importorskip("gateway.platforms.base")
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._client = _FC()
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_comment_update())
    for _ in range(50):
        if col.events:
            break
        await asyncio.sleep(0.01)
    src = col.events[0].source
    assert src.chat_id == "500" and src.chat_type == "channel"
    assert src.thread_id == "mid.777" and str(src.parent_chat_id) == "500"
    assert "Петя" in col.events[0].text and "Отличный пост" in col.events[0].text
```

(замените `channel=500` в `_comment_update` на число 500 — уже так; `_FC.post_comment` остаётся для Task 3)

- [ ] **Step 2: Run — убедиться в падении**

Run: `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q tests/test_channel.py`
Expected: FAIL — `src.chat_id == "500"` не выполняется (сейчас там `"mid.777"`)

- [ ] **Step 3: Реализация `_on_comment`**

В `maxbot/adapter.py` заменить в `_on_comment` блок построения source (строки ~797-806) на:

```python
        channel_id = int(recipient.get("chat_id") or 0)
        msg = update.message
        author = (msg.sender.name or f"id{msg.sender.user_id}") if msg.sender else "канал"
        text = msg.body.text or ""
        if not text.strip():
            return
        source = self.build_source(
            chat_id=str(channel_id), chat_name=f"канал:{channel_id}",
            chat_type="channel", thread_id=str(post_id), parent_chat_id=str(channel_id),
            user_id=str(msg.sender.user_id) if msg.sender else "", user_name=author)
```

Удалить строки: `self._comment_posts[str(post_id)] = channel_id` и `self._remember_post_channel(str(post_id), channel_id)`. Удалить метод `_remember_post_channel` (строки 768-787), поле `self._comment_posts` из `__init__` (найти по grep `_comment_posts`) и ветку в `send()` (строки 461-468) — outbound переедет на metadata в Task 3. Пока `send()` для комментариев не маршрутизируется (тест из Task 3 закроет).

- [ ] **Step 4: GREEN + вся сюита**

Run: `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q tests/test_channel.py tests/test_chat_memory.py tests/test_adapter_inbound.py`
Expected: PASS (кроме теста `test_channel_posts_share_memory` — он про реестр, перепишем в Task 4; если падает сейчас — пометить xfail с reason "реестр удаляется в Task 4")

- [ ] **Step 5: Commit**

```bash
git add maxbot/adapter.py tests/test_channel.py
git commit -m "refactor: ветки комментариев каналов — официальный thread-механизм ядра (chat_id=канал, thread_id=пост)"
```

---

### Task 2: Ключи сессий не меняются у групп/ЛС + память канала нативно

**Files:**
- Modify: `maxbot/chat_memory.py` (`_SESSION_CHAT_RE`, `_bind`, удалить `_channel_of_post`)
- Modify: `maxbot/adapter.py` (удалить остатки `_comment_posts`, если остались)
- Test: `tests/test_chat_memory.py`, `tests/test_adapter_inbound.py`

**Interfaces:**
- Consumes: ключи сессий ядра: `agent:main:max:dm:<user>`, `agent:main:max:group:<chat>:<user>`, новый `agent:main:max:channel:<канал>:<пост>` (gateway/session.py:641)
- Produces: ключи памяти `max-dm-<id>`, `max-group-<id>`, `max-channel-<канал>`

- [ ] **Step 1: RED — тесты**

`tests/test_chat_memory.py`: заменить `test_channel_posts_share_memory` на:

```python
def test_channel_threads_share_memory_via_session_key(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(tmp_path))
    p = _provider(tmp_path)  # существующий хелпер файла, создаёт провайдер с home=tmp_path
    p._bind("agent:main:max:channel:500:mid.777")
    p.handle_tool_call("chat_memory_save", {"text": "правило канала: без спама"}, None)
    p._bind("agent:main:max:channel:500:mid.888")
    p.handle_tool_call("chat_memory_load", {}, None)
    assert "без спама" in (p._notes and " ".join(p._notes) or "")
```

(если хелпера `_provider` нет — создать по образцу соседних тестов файла)

В `tests/test_adapter_inbound.py` добавить:

```python
async def test_group_and_dm_session_keys_unchanged():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message(text="привет", chat_type="dialog", chat_id=77))
    await adapter._handle_update(
        _upd_message(text="@hermes_bot привет", chat_type="chat", chat_id=88, sender_id=1))
    import asyncio
    await asyncio.sleep(0.05)
    keys = [adapter._event_session_key(e) for e in col.events]
    assert any(":dm:77" in k for k in keys) and any(":group:88" in k for k in keys)
```

- [ ] **Step 2: Run RED**

Run: `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q tests/test_chat_memory.py tests/test_adapter_inbound.py`
Expected: FAIL (regex не знает `channel:`)

- [ ] **Step 3: Реализация**

`maxbot/chat_memory.py`:
```python
_SESSION_CHAT_RE = re.compile(r":max:(dm|group|channel):(?:chat:)?([A-Za-z0-9_.\-]+)")
```
В `_bind` удалить блок «ветки комментариев одного канала — ОБЩАЯ память канала» (строки 91-94): для `group(1) == "channel"` ключ уже `max-channel-<канал>` — общая память канала получается из самого ключа. Удалить `_channel_of_post` (строки 76-82) и импорт json, если больше не нужен.

- [ ] **Step 4: GREEN + сюита**

Run: `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q`
Expected: PASS (убрать xfail из Task 1, если ставили)

- [ ] **Step 5: Commit**

```bash
git add maxbot/chat_memory.py tests/test_chat_memory.py tests/test_adapter_inbound.py
git commit -m "refactor: память канала из официального ключа сессии (max-channel-<id>), реестр _channel_posts.json удалён"
```

---

### Task 3: Outbound-комментарии через `metadata["thread_id"]`

**Files:**
- Modify: `maxbot/adapter.py` (`send()`, ~строки 455-498)
- Test: `tests/test_channel.py`

**Interfaces:**
- Consumes: ядро передаёт `metadata={"thread_id": post_id}` (base.py:114-120 `_thread_metadata_for_source`)
- Produces: `send(chat_id=канал, content, metadata={"thread_id": пост})` → `client.post_comment(пост, chunk)`

- [ ] **Step 1: RED — тест**

В `tests/test_channel.py`:

```python
class _FC2:
    def __init__(self):
        self.commented = None

    async def post_comment(self, post_id, text):
        self.commented = (post_id, text)
        return "cm.new"


async def test_send_routes_to_comment_via_thread_metadata():
    import pytest as _pytest

    _pytest.importorskip("gateway.platforms.base")
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    fc = _FC2()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._uploader = None
    res = await adapter.send("500", "ответ модератора", metadata={"thread_id": "mid.777"})
    assert res.success and fc.commented == ("mid.777", "ответ модератора")
```

- [ ] **Step 2: Run RED** — `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q tests/test_channel.py` — FAIL (send уйдёт в send_message)

- [ ] **Step 3: Реализация**

В `send()` (adapter.py) перед сегментацией:

```python
        thread_id = str(((metadata or {}).get("thread_id") or "")).strip()
        last_mid: Optional[str] = None
        if thread_id:
            # ветка комментариев канала: ответ уходит комментарием к посту
            try:
                for chunk in self._segment(comment_markdown(content)):
                    last_mid = await self._client.post_comment(thread_id, chunk)
            except MaxApiError as exc:
                return SendResult(success=False, error=str(exc))
            return SendResult(success=True, message_id=last_mid)
```

(заменяет прежний блок `if str(chat_id) in self._comment_posts:`; импорт `comment_markdown` уже есть в файле — проверить)

- [ ] **Step 4: GREEN + сюита** — `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q`

- [ ] **Step 5: Commit** — `git commit -m "refactor: исходящие комментарии через metadata thread_id (официальный механизм ядра)"`

---

### Task 4: Модуль `profile_switch.py` — карта чат→профиль

**Files:**
- Create: `maxbot/profile_switch.py`
- Test: `tests/test_profile_switch.py`

**Interfaces:**
- Consumes: `hermes_constants.get_hermes_home()`; `hermes_cli.profiles.list_profile_names/normalize_profile_name` (лениво, с fallback на скан каталога)
- Produces:
  - `load_map(home: Path) -> dict[str, str]` — кэш в модуле по mtime
  - `set_profile(home: Path, chat_key: str, profile: str) -> None` — атомарная запись (`<home>/maxbot-chat-memory/_chat_profiles.json`), обновляет кэш
  - `available_profiles() -> list[str]` — `["default", ...именованные]`
  - `profile_exists(name: str) -> bool`

- [ ] **Step 1: RED — тесты**

`tests/test_profile_switch.py`:

```python
"""Переключалка профилей: карта чат→профиль, список профилей."""
import json

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot import profile_switch


def test_map_roundtrip(tmp_path):
    profile_switch.set_profile(tmp_path, "500", "work")
    assert profile_switch.load_map(tmp_path) == {"500": "work"}
    assert json.loads((tmp_path / "maxbot-chat-memory" / "_chat_profiles.json").read_text())["500"] == "work"


def test_map_unknown_profile_not_written(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    profile_switch.set_profile(tmp_path, "500", "ghost")
    assert profile_switch.load_map(tmp_path) == {}


def test_available_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work", "family"})
    assert profile_switch.available_profiles() == ["default", "family", "work"]
```

- [ ] **Step 2: Run RED** — FAIL: `ModuleNotFoundError: maxbot.profile_switch`

- [ ] **Step 3: Реализация**

```python
"""Переключалка профилей: per-chat карта chat_id → профиль (JSON в HERMES_HOME)."""
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_cache: Dict[Path, tuple] = {}  # home -> (mtime, map)


def _store(home: Path) -> Path:
    return home / "maxbot-chat-memory" / "_chat_profiles.json"


def _existing_profiles() -> Set[str]:
    """{'default', ...именованные}; каталоги профилей сканируем напрямую."""
    names: Set[str] = {"default"}
    try:
        from hermes_constants import get_default_hermes_root
        root = Path(get_default_hermes_root()) / "profiles"
        if root.is_dir():
            names |= {p.name for p in root.iterdir() if (p / "SOUL.md").exists()}
    except Exception:
        logger.debug("profile_switch: скан профилей не удался", exc_info=True)
    return names


def profile_exists(name: str) -> bool:
    return bool(name) and name in _existing_profiles()


def available_profiles() -> List[str]:
    return sorted(_existing_profiles())


def load_map(home: Path) -> Dict[str, str]:
    f = _store(home)
    try:
        mtime = f.stat().st_mtime_ns
    except OSError:
        _cache.pop(home, None)
        return {}
    if _cache.get(home, (0, None))[0] == mtime:
        return dict(_cache[home][1])
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        data = {str(k): str(v) for k, v in data.items() if _NAME_RE.match(str(v))}
    except Exception:
        data = {}
    _cache[home] = (mtime, data)
    return dict(data)


def set_profile(home: Path, chat_key: str, profile: str) -> None:
    if not _NAME_RE.match(profile or "") or not profile_exists(profile):
        return
    f = _store(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    data = load_map(home)
    data[str(chat_key)] = profile
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    _cache.pop(home, None)
```

- [ ] **Step 4: GREEN** — `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q tests/test_profile_switch.py`

- [ ] **Step 5: Commit** — `git commit -m "feat: карта чат→профиль для переключалки ассистентов (JSON, атомарно)"`

---

### Task 5: Команда `/assistant` — пикер профилей

**Files:**
- Modify: `maxbot/adapter.py` (`_on_message`, блок локальных команд рядом с geo/contact)
- Test: `tests/test_profile_switch.py`

**Interfaces:**
- Consumes: `InteractiveDispatcher.send_choice_picker(chat_id, title, choices, session_key, on_choice_selected)` (interactive.py:112); `profile_switch.load_map/set_profile/available_profiles`; `hermes_constants.get_hermes_home()`
- Produces: `/assistant` → пикер; выбор → карта обновлена, подтверждение текстом

- [ ] **Step 1: RED — тест**

```python
async def test_assistant_command_sends_picker(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter
    from maxbot.models import parse_update

    class _Cfg:
        extra = {}

    class _Interactive:
        def __init__(self):
            self.calls = []

        async def send_choice_picker(self, chat_id, title, choices, session_key,
                                     on_choice_selected, metadata=None):
            self.calls.append((chat_id, title, choices, on_choice_selected))
            return "m.pick"

    class _Runner:
        class config:
            multiplex_profiles = True

    inter = _Interactive()
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    upd = parse_update({
        "update_type": "message_created", "marker": 1,
        "message": {
            "body": {"mid": "m1", "text": "/assistant", "attachments": []},
            "recipient": {"chat_id": 500, "chat_type": "dialog"},
            "sender": {"user_id": 42, "name": "Петя"}, "timestamp": 1,
        },
    })
    await adapter._handle_update(upd)
    assert len(inter.calls) == 1
    chat_id, title, choices, handler = inter.calls[0]
    labels = [c["label"] for c in choices]
    assert "work" in labels and any(c.get("is_current") is False for c in choices)
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    handler("500", "work")
    assert profile_switch.load_map(tmp_path) == {"500": "work"}


async def test_assistant_without_multiplex_explains(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _FC:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            return "m.1"

    class _Runner:
        class config:
            multiplex_profiles = False

    fc = _FC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    upd = parse_update({
        "update_type": "message_created", "marker": 1,
        "message": {"body": {"mid": "m1", "text": "/assistant", "attachments": []},
                    "recipient": {"chat_id": 500, "chat_type": "dialog"},
                    "sender": {"user_id": 42, "name": "Петя"}, "timestamp": 1},
    })
    await adapter._handle_update(upd)
    assert fc.sent and "multiplex_profiles" in fc.sent[0]
```

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Реализация**

В `maxbot/adapter.py` добавить модульную функцию (рядом с другими хелперами):

```python
def _plugin_home():
    from hermes_constants import get_hermes_home
    return get_hermes_home()
```

В `_on_message`, в блок локальных команд (там где `stripped in {"geo", "contact"}`), ДО него добавить:

```python
        if stripped == "assistant":
            await self._assistant_command(str(msg.chat_id))
            return
```

И метод в классе:

```python
    def _multiplex_on(self) -> bool:
        runner = getattr(self, "gateway_runner", None)
        return bool(runner is not None
                    and getattr(getattr(runner, "config", None), "multiplex_profiles", False))

    async def _assistant_command(self, chat_id: str) -> None:
        from . import profile_switch
        if not self._multiplex_on():
            with contextlib.suppress(Exception):
                await self._client.send_message(
                    int(chat_id),
                    "⚙️ Переключение профилей требует gateway.multiplex_profiles: true "
                    "в config.yaml и рестарт гейтвея.")
            return
        if not self._interactive:
            return
        current = profile_switch.load_map(_plugin_home()).get(chat_id, "default")
        choices = [{"label": name, "value": name, "is_current": name == current}
                   for name in profile_switch.available_profiles()]

        def _select(chat, value):
            profile_switch.set_profile(_plugin_home(), str(chat), str(value))
            return (f"✅ Профиль «{value}» активен. Следующее сообщение начнёт новую "
                    f"сессию с его SOUL.md, скиллами и моделью. Переключение обратно — /assistant.")

        await self._interactive.send_choice_picker(
            chat_id, "🧭 Профиль для этого чата:", choices, session_key="",
            on_choice_selected=_select)
```

- [ ] **Step 4: GREEN + сюита** (`/geo`, `/contact`-тесты не должны пострадать)

- [ ] **Step 5: Commit** — `git commit -m "feat: /assistant — переключение профиля чата пикером (source.profile)"`

---

### Task 6: Штамп `source.profile` в `build_source`

**Files:**
- Modify: `maxbot/adapter.py` (переопределение `build_source`)
- Test: `tests/test_profile_switch.py`

**Interfaces:**
- Consumes: `base.build_source(...)` (возвращает SessionSource с полем `profile`); `profile_switch.load_map/profile_exists`
- Produces: события чатов с картой получают `source.profile = <имя>`; комментарии — по `chat_id` канала (канал-wide профиль)

- [ ] **Step 1: RED — тесты**

```python
async def test_message_stamps_source_profile(tmp_path, monkeypatch):
    import asyncio

    from maxbot.adapter import MaxAdapter
    from maxbot.models import parse_update

    class _Cfg:
        extra = {}

    class _Runner:
        class config:
            multiplex_profiles = True

    class Col:
        def __init__(self):
            self.events = []

        async def __call__(self, e):
            self.events.append(e)

    profile_switch.set_profile(tmp_path, "500", "work")
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    col = Col()
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    adapter._message_handler = col
    upd = parse_update({
        "update_type": "message_created", "marker": 1,
        "message": {"body": {"mid": "m1", "text": "привет", "attachments": []},
                    "recipient": {"chat_id": 500, "chat_type": "dialog"},
                    "sender": {"user_id": 42, "name": "Петя"}, "timestamp": 1},
    })
    await adapter._handle_update(upd)
    for _ in range(50):
        if col.events:
            break
        await asyncio.sleep(0.01)
    assert col.events and col.events[0].source.profile == "work"


async def test_no_stamp_without_multiplex_or_missing_profile(tmp_path, monkeypatch):
    # копия теста выше, но _Runner.config.multiplex_profiles = False → profile None;
    # вторая часть: multiplex=True, карта указывает на «ghost» → profile None
    ...
```

(вторая часть — полноценный тест по образцу первой, карта `{"500": "ghost"}`, `profile_exists` мокнут на `{"default"}`)

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Реализация**

В `MaxAdapter`:

```python
    def build_source(self, *args, **kwargs):
        source = super().build_source(*args, **kwargs)
        if not self._multiplex_on():
            return source
        from . import profile_switch
        from .profile_switch import profile_exists
        name = profile_switch.load_map(_plugin_home()).get(str(source.chat_id) or "")
        if name and profile_exists(name):
            source.profile = name
        return source
```

(для комментариев `source.chat_id` = канал — профиль канал-wide; для ЛС/групп — сам чат)

- [ ] **Step 4: GREEN + вся сюита + ruff**

Run: `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check .`

- [ ] **Step 5: Commit** — `git commit -m "feat: штамп source.profile из карты чатов — официальная маршрутизация на профиль (SOUL.md/скиллы/модель)"`

---

### Task 7: Документация

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: README** — раздел «Профили-ассистенты (один бот)»: включение `gateway.multiplex_profiles: true`, `hermes profile create <name>`, `/assistant`, ограничения (история не переезжает, ingress-allowlist на дефолте), разрыв старых сессий комментариев после обновления.
- [ ] **Step 2: AGENTS.md** — строка в «Специфику MAX»: «ветки комментариев = официальный thread-механизм (chat_id=канал, thread_id=пост); профили переключаются /assistant (source.profile, мультиплекс)».
- [ ] **Step 3: Финальная проверка** — `HERMES_AGENT_SRC=../hermes-agent .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check .`
- [ ] **Step 4: Commit** — `git commit -m "docs: профили-ассистенты и официальные thread-сессии комментариев"`

---

## Deployment (после проверки на сервере)

1. `gateway.multiplex_profiles: true` в config.yaml дефолтного профиля, `hermes profile create <имена>`
2. Перезапуск гейтвея; `/assistant` в чате
3. Проверить в логе: `max: групповое сообщение ... пропущен` для reply-ветки — отдельный баг, диагностика отдельно
