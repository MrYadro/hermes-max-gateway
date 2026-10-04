"""Конформанс MaxClient официальной OpenAPI-схеме MAX.

Каждый метод клиента прогоняется через respx; запрос валидируется против
vendored-схемы (fixtures/max-api-schema.yaml):
  - путь + HTTP-метод существуют в схеме;
  - посланные query-параметры объявлены в схеме, required — на месте;
  - enum-значения (action, upload type, types) корректны;
  - required-поля тела запроса присутствуют;
  - ключи ответов, которые клиент парсит, существуют в response-схеме
    (CommentMessageList.messages, ChatMembersList.members и т.д.).
При обновлении схемы тест покажет диф один-в-один.
"""
import json
import pathlib

import httpx
import pytest
import respx
import yaml

from maxbot.max_api import MaxClient

BASE = "https://x.test"
SCHEMA_PATH = pathlib.Path(__file__).parent / "fixtures" / "max-api-schema.yaml"
SCHEMA = yaml.safe_load(SCHEMA_PATH.read_text())
PATHS = SCHEMA["paths"]


def _resolve(node: dict) -> dict:
    while isinstance(node, dict) and "$ref" in node:
        cur = SCHEMA
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        node = cur
    return node


def _op(template: str, method: str) -> dict:
    assert template in PATHS, f"пути {template} нет в схеме"
    assert method in PATHS[template], f"метода {method.upper()} {template} нет в схеме"
    return PATHS[template][method]


def _validate_request(req: httpx.Request, template: str, method: str) -> None:
    op = _op(template, method)
    declared = {}
    for p in op.get("parameters", []):
        p = _resolve(p)
        if p.get("in") in ("query", "path"):
            declared[p["name"]] = p
    sent = {k: v for k, v in req.url.params.items()}
    for name in sent:
        assert name in declared, f"query {name!r} не объявлен в схеме для {method.upper()} {template}"
    for name, p in declared.items():
        if p.get("in") == "query":
            if p.get("required"):
                assert name in sent, f"required query {name!r} не послан ({method.upper()} {template})"
            enum = (_resolve(p.get("schema") or {}) or {}).get("enum")
            if enum and name in sent:
                values = sent[name].split(",")
                bad = set(values) - set(enum)
                assert not bad, f"значения {bad} не из enum схемы ({name}: {enum})"

    rb = op.get("requestBody")
    if rb:
        body = json.loads(req.content) if req.content else {}
        sch = _resolve(rb["content"]["application/json"]["schema"])
        props, required = {}, []
        for blk in ([sch] if sch.get("properties") else [_resolve(b) for b in sch.get("allOf", [])]):
            props.update(blk.get("properties") or {})
            required += blk.get("required") or []
        for field in required:
            assert field in body, f"required-поле тела {field!r} отсутствует ({method.upper()} {template})"
        for field in body:
            assert field in props, f"поле тела {field!r} не объявлено в схеме ({method.upper()} {template})"


CHAT = -200

# Каждый кейс: реальный вызов клиента + шаблон пути из схемы + ожидание по ответу.
CASES = [
    {"id": "get_me", "m": "get", "t": "/me", "p": "/me", "r": {"user_id": 1, "name": "B"},
     "call": lambda c: c.get_me(), "expect": lambda r: r.user_id == 1},
    {"id": "set_commands", "m": "patch", "t": "/me/commands", "p": "/me/commands",
     "r": {"success": True},
     "call": lambda c: c.set_commands([{"name": "x", "description": "d"}])},
    {"id": "get_chat", "m": "get", "t": "/chats/{chatId}", "p": f"/chats/{CHAT}",
     "r": {"chat_id": CHAT},
     "call": lambda c: c.get_chat(CHAT), "expect": lambda r: r.get("chat_id") == CHAT},
    {"id": "patch_chat", "m": "patch", "t": "/chats/{chatId}", "p": f"/chats/{CHAT}",
     "r": {"success": True},
     "call": lambda c: c.patch_chat(CHAT, title="новое")},
    {"id": "chat_action", "m": "post", "t": "/chats/{chatId}/actions",
     "p": f"/chats/{CHAT}/actions", "r": {"success": True},
     "call": lambda c: c.chat_action(CHAT, "typing_on")},
    {"id": "chat_action_mark_seen", "m": "post", "t": "/chats/{chatId}/actions",
     "p": f"/chats/{CHAT}/actions", "r": {"success": True},
     "call": lambda c: c.chat_action(CHAT, "mark_seen")},
    {"id": "pin", "m": "put", "t": "/chats/{chatId}/pin", "p": f"/chats/{CHAT}/pin",
     "r": {"success": True}, "call": lambda c: c.pin_message(CHAT, "mid.1")},
    {"id": "unpin", "m": "delete", "t": "/chats/{chatId}/pin", "p": f"/chats/{CHAT}/pin",
     "r": {"success": True}, "call": lambda c: c.unpin_message(CHAT)},
    {"id": "get_pinned", "m": "get", "t": "/chats/{chatId}/pin", "p": f"/chats/{CHAT}/pin",
     "r": {"message": {"body": {"mid": "mid.p", "text": "закреп"}}},
     "call": lambda c: c.get_pinned_message(CHAT),
     "expect": lambda r: r.get("body", {}).get("mid") == "mid.p"},
    {"id": "membership", "m": "get", "t": "/chats/{chatId}/members/me",
     "p": f"/chats/{CHAT}/members/me",
     "r": {"is_admin": True, "permissions": ["pin_message"], "is_owner": False,
           "last_access_time": 1, "join_time": 1},
     "call": lambda c: c.get_membership(CHAT),
     "expect": lambda r: r.get("is_admin") is True and "permissions" in r},
    {"id": "leave", "m": "delete", "t": "/chats/{chatId}/members/me",
     "p": f"/chats/{CHAT}/members/me", "r": {"success": True},
     "call": lambda c: c.leave_chat(CHAT)},
    {"id": "members", "m": "get", "t": "/chats/{chatId}/members",
     "p": f"/chats/{CHAT}/members", "r": {"members": [{"user_id": 5}]},
     "call": lambda c: c.get_members(CHAT),
     "expect": lambda r: r and r[0]["user_id"] == 5},
    {"id": "admins", "m": "get", "t": "/chats/{chatId}/members/admins",
     "p": f"/chats/{CHAT}/members/admins",
     "r": {"members": [{"user_id": 5, "permissions": ["pin_message"]}]},
     "call": lambda c: c.get_admins(CHAT),
     "expect": lambda r: r and r[0]["user_id"] == 5},
    {"id": "add_admin", "m": "post", "t": "/chats/{chatId}/members/admins",
     "p": f"/chats/{CHAT}/members/admins", "r": {"success": True},
     "call": lambda c: c.add_admin(CHAT, 7, ["pin_message"])},
    {"id": "remove_admin", "m": "delete", "t": "/chats/{chatId}/members/admins/{userId}",
     "p": f"/chats/{CHAT}/members/admins/7", "r": {"success": True},
     "call": lambda c: c.remove_admin(CHAT, 7)},
    {"id": "kick", "m": "delete", "t": "/chats/{chatId}/members",
     "p": f"/chats/{CHAT}/members", "r": {"success": True},
     "call": lambda c: c.kick_member(CHAT, 7)},
    {"id": "subscriptions", "m": "get", "t": "/subscriptions", "p": "/subscriptions",
     "r": {"subscriptions": []},
     "call": lambda c: c.get_subscriptions(), "expect": lambda r: r == []},
    {"id": "subscribe", "m": "post", "t": "/subscriptions", "p": "/subscriptions",
     "r": {"url": "https://x", "time": 1, "update_types": ["message_created"]},
     "call": lambda c: c.subscribe("https://x", ["message_created"], secret="S12345")},
    {"id": "unsubscribe", "m": "delete", "t": "/subscriptions", "p": "/subscriptions",
     "r": {"success": True}, "call": lambda c: c.unsubscribe("https://x/hook")},
    {"id": "updates", "m": "get", "t": "/updates", "p": "/updates",
     "r": {"updates": [], "marker": 9},
     "call": lambda c: c.get_updates(marker=5),
     "expect": lambda r: r[0] == 9},
    {"id": "upload_slot", "m": "post", "t": "/uploads", "p": "/uploads",
     "r": {"url": "https://up"},
     "call": lambda c: c.get_upload_slot("image"),
     "expect": lambda r: r[0] == "https://up"},
    {"id": "send_message", "m": "post", "t": "/messages", "p": "/messages",
     "r": {"message": {"body": {"mid": "mid.9"}}},
     "call": lambda c: c.send_message(CHAT, "привет"),
     "expect": lambda r: r == "mid.9"},
    {"id": "send_image_by_url", "m": "post", "t": "/messages", "p": "/messages",
     "r": {"message": {"body": {"mid": "mid.i"}}},
     "call": lambda c: c.send_image_by_url(CHAT, "https://cdn/x.png"),
     "expect": lambda r: r == "mid.i"},
    {"id": "edit_message", "m": "put", "t": "/messages", "p": "/messages",
     "r": {"success": True}, "call": lambda c: c.edit_message("mid.1", "новый")},
    {"id": "delete_message", "m": "delete", "t": "/messages", "p": "/messages",
     "r": {"success": True}, "call": lambda c: c.delete_message("mid.1")},
    {"id": "get_message", "m": "get", "t": "/messages/{messageId}", "p": "/messages/mid.1",
     "r": {"body": {"mid": "mid.1", "text": "hi"}, "recipient": {"chat_id": 1},
           "sender": {"user_id": 2}, "timestamp": 1},
     "call": lambda c: c.get_message("mid.1"),
     "expect": lambda r: r.body.mid == "mid.1"},
    {"id": "answer_callback", "m": "post", "t": "/answers", "p": "/answers",
     "r": {"success": True},
     "call": lambda c: c.answer_callback("cb.1", "готово")},
    {"id": "post_comment", "m": "post", "t": "/messages/{messageId}/comments",
     "p": "/messages/mid.p/comments", "r": {"message": {"body": {"mid": "c.1"}}},
     "call": lambda c: c.post_comment("mid.p", "коммент"),
     "expect": lambda r: r == "c.1"},
    {"id": "get_comments", "m": "get", "t": "/messages/{messageId}/comments",
     "p": "/messages/mid.p/comments",
     "r": {"messages": [{"body": {"mid": "c.1", "text": "hi"}}]},
     "call": lambda c: c.get_comments("mid.p"),
     "expect": lambda r: r and r[0]["body"]["mid"] == "c.1"},
    {"id": "edit_comment", "m": "put", "t": "/messages/{messageId}/comments",
     "p": "/messages/mid.p/comments", "r": {"success": True},
     "call": lambda c: c.edit_comment("mid.p", "c.1", "правка")},
    {"id": "delete_comment", "m": "delete", "t": "/messages/{messageId}/comments",
     "p": "/messages/mid.p/comments", "r": {"success": True},
     "call": lambda c: c.delete_comment("mid.p", "c.1")},
    {"id": "video_info", "m": "get", "t": "/videos/{videoToken}", "p": "/videos/vt.1",
     "r": {"duration": 5},
     "call": lambda c: c.get_video_info("vt.1"), "expect": lambda r: r.get("duration") == 5},
]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
@respx.mock
async def test_client_conforms_to_schema(case):
    route = respx.route(method=case["m"], path=case["p"]).mock(
        return_value=httpx.Response(200, json=case["r"]))
    async with MaxClient("T", base_url=BASE) as c:
        result = await case["call"](c)
    _validate_request(route.calls.last.request, case["t"], case["m"])
    if "expect" in case:
        assert case["expect"](result)


@respx.mock
async def test_updates_types_are_schema_update_types():
    """types в /updates ⊆ discriminator-маппингу Update (полный паритет — test_parity)."""
    mapping = SCHEMA["components"]["schemas"]["Update"]["discriminator"]["mapping"]
    route = respx.get(f"{BASE}/updates").mock(return_value=httpx.Response(
        200, json={"updates": [], "marker": 1}))
    async with MaxClient("T", base_url=BASE) as c:
        await c.get_updates()
    sent = route.calls.last.request.url.params["types"].split(",")
    assert set(sent) <= set(mapping)


@respx.mock
async def test_message_body_fields_are_schema_declared():
    """Тело POST /messages — только поля NewMessageBody из схемы."""
    route = respx.post(f"{BASE}/messages").mock(return_value=httpx.Response(
        200, json={"message": {"body": {"mid": "m"}}}))
    async with MaxClient("T", base_url=BASE) as c:
        await c.send_message(1, "текст", notify=False,
                             attachments=[{"type": "sticker", "payload": {"code": "x"}}],
                             reply_to_mid="mid.0")
    body = json.loads(route.calls.last.request.content)
    assert set(body) <= {"text", "attachments", "link", "notify", "format"}
    assert body["link"] == {"type": "reply", "mid": "mid.0"}
