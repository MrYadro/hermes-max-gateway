"""Per-chat память для MAX: у каждого чата — свои записи; группы не видят личное.

Активация: ``memory.provider: maxbot`` в config.yaml. При активации встроенная
глобальная память перестаёт инжектиться ядром; для DM-чатов провайдер сам читает
``memories/MEMORY.md``/``USER.md`` (глобальные факты), для групп — только их записи.
"""
import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider, RecallStatus, is_trivial_prompt

logger = logging.getLogger(__name__)

_SESSION_CHAT_RE = re.compile(r":max:(dm|group):(?:chat:)?([A-Za-z0-9_.\-]+)")
_BUILTIN_CAP = 2600  # символ на глобальный блок для DM


def _safe_key(key: str) -> str:
    return "".join(c if unicodedata.category(c)[0] not in "CPZ" else "_" for c in key)[:80] or "default"


class ChatMemoryProvider(MemoryProvider):
    """Записи чата (JSON на чат) + глобальный блок только для DM."""

    def __init__(self):
        self._home: Optional[Path] = None
        self._key = "default"
        self._notes: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "maxbot"

    # ── жизненный цикл ──
    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._home = Path(kwargs.get("hermes_home") or "~/.hermes").expanduser()
        (self._home / "maxbot-chat-memory").mkdir(parents=True, exist_ok=True)
        self._bind(session_id)

    def on_session_switch(self, new_session_id: str, *, reset: bool = False, **kwargs) -> None:
        self._bind(new_session_id, flush=reset)

    def shutdown(self) -> None:
        self._save()

    # ── ключ чата и хранение ──
    def _sid_index(self) -> Dict[str, str]:
        """session_id -> session_key (из sessions.json; кэш по mtime)."""
        f = self._home / "sessions" / "sessions.json"
        try:
            mtime = f.stat().st_mtime
        except OSError:
            return {}
        cache = getattr(self, "_idx_cache", None)
        if cache and cache[0] == mtime:
            return cache[1]
        idx: Dict[str, str] = {}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for key, entry in data.items():
                if isinstance(entry, dict) and entry.get("session_id"):
                    idx[str(entry["session_id"])] = str(key)
        except Exception:
            idx = {}
        self._idx_cache = (mtime, idx)
        return idx

    def _channel_of_post(self, post_id: str) -> Optional[int]:
        try:
            reg = json.loads((self._home / "maxbot-chat-memory" / "_channel_posts.json")
                             .read_text(encoding="utf-8"))
            return int(reg[post_id])
        except Exception:
            return None

    def _bind(self, session_id: str, *, flush: bool = False) -> None:
        sid = session_id or ""
        m = _SESSION_CHAT_RE.search(sid)
        if not m and self._home:
            # провайдеру приходит UUID сессии — резолвим чат через индекс сессий
            m = _SESSION_CHAT_RE.search(self._sid_index().get(sid, ""))
        key = f"max-{m.group(1)}-{m.group(2)}" if m else _safe_key(sid or "default")
        # ветки комментариев одного канала — ОБЩАЯ память канала
        if m and m.group(1) == "group" and self._home:
            if (ch := self._channel_of_post(m.group(2))) is not None:
                key = f"max-channel-{ch}"
        if key != self._key:
            self._save()
            self._key = key
            self._load()
        elif flush:
            self._notes = []

    @property
    def _store(self) -> Path:
        return self._home / "maxbot-chat-memory" / f"{_safe_key(self._key)}.json"

    def _load(self) -> None:
        try:
            self._notes = json.loads(self._store.read_text(encoding="utf-8")).get("notes", [])
        except Exception:
            self._notes = []

    def _save(self) -> None:
        if not self._home:
            return
        try:
            tmp = self._store.with_suffix(".tmp")
            tmp.write_text(json.dumps({"notes": self._notes}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self._store)
        except Exception:
            logger.exception("chat-memory: не удалось сохранить %s", self._store)

    # ── инжект в контекст ──
    def system_prompt_block(self) -> str:
        return ("У тебя есть память ТЕКУЩЕГО чата (per-chat): то, что важно сохранить, "
                "записывай инструментом chat_memory_save — записи видны только этому чату. "
                "Глобальная личная память в групповых чатах недоступна по дизайну.")

    def _builtin_block(self) -> str:
        parts = []
        for fname, title in (("MEMORY.md", "Долговременная память"), ("USER.md", "Профиль пользователя")):
            try:
                text = (self._home / "memories" / fname).read_text(encoding="utf-8").strip()
            except Exception:
                text = ""
            if text:
                parts.append(f"[{title}]\n{text}")
        blob = "\n\n".join(parts)
        return blob[:_BUILTIN_CAP]

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if is_trivial_prompt(query):  # «ок», «спасибо», команды — не тратим контекст на память
            return ""
        if session_id:
            self._bind(session_id)
        blocks = []
        if self._key.startswith("max-dm-"):
            builtin = self._builtin_block()
            if builtin:
                blocks.append(builtin)
        if self._notes:
            listed = "\n".join(f"- ({n['id']}) {n['text']}" for n in self._notes[-40:])
            blocks.append(f"[Память этого чата]\n{listed}")
        return "\n\n".join(blocks)[:4500]

    def recall_status(self) -> Optional[RecallStatus]:
        # индикатор «💬 recalled N memory» в чате — только по явному желанию
        import os
        if os.environ.get("MAX_MEMORY_INDICATOR", "").lower() not in ("1", "true", "yes"):
            return None
        if self._notes or (self._key.startswith("max-dm-") and self._builtin_block()):
            return RecallStatus("чат", max(1, len(self._notes)), glyph="💬")
        return None

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  **kwargs) -> None:
        if session_id:
            self._bind(session_id)

    # ── инструменты ──
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {"name": "chat_memory_save",
             "description": "Сохранить факт в память ТЕКУЩЕГО чата (виден только этому чату).",
             "parameters": {"type": "object",
                            "properties": {"text": {"type": "string", "description": "факт/заметка"}},
                            "required": ["text"]}},
            {"name": "chat_memory_list",
             "description": "Показать все записи памяти текущего чата.",
             "parameters": {"type": "object", "properties": {}}},
            {"name": "chat_memory_forget",
             "description": "Удалить запись из памяти текущего чата по id.",
             "parameters": {"type": "object",
                            "properties": {"id": {"type": "integer"}},
                            "required": ["id"]}},
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if session_id := kwargs.get("session_id") or "":
            self._bind(session_id)
        if tool_name == "chat_memory_save":
            text = str(args.get("text") or "").strip()
            if not text:
                return json.dumps({"ok": False, "error": "пустой текст"}, ensure_ascii=False)
            nid = (self._notes[-1]["id"] + 1) if self._notes else 1
            self._notes.append({"id": nid, "text": text, "ts": int(time.time())})
            self._save()
            return json.dumps({"ok": True, "id": nid}, ensure_ascii=False)
        if tool_name == "chat_memory_list":
            return json.dumps({"notes": self._notes}, ensure_ascii=False)
        if tool_name == "chat_memory_forget":
            fid = args.get("id")
            before = len(self._notes)
            self._notes = [n for n in self._notes if n["id"] != fid]
            self._save()
            return json.dumps({"ok": True, "removed": before - len(self._notes)}, ensure_ascii=False)
        raise NotImplementedError(f"chat-memory: неизвестный инструмент {tool_name}")
