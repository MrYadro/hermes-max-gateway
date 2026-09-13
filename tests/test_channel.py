"""Комментарии каналов: клиент, markdown, inbound-маршрутизация."""
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.markdown import comment_markdown
from maxbot.models import parse_update


def test_comment_markdown_flattens_links():
    assert comment_markdown("смотри [док](https://x.ru/a_b)") == "смотри док: https://x.ru/a_b"


def _comment_update(text="Отличный пост!", post_id="mid.777", channel=500, sender_id=13):
    return parse_update({
        "update_type": "comment_created", "marker": 9,
        "message": {
            "body": {"mid": "cm.1", "text": text},
            "recipient": {"chat_id": channel, "post_id": post_id},
            "sender": {"user_id": sender_id, "name": "Петя"},
            "timestamp": 1,
        },
    })


class _FC:
    async def post_comment(self, post_id, text):
        self.commented = (post_id, text)
        return "cm.new"


class Collector:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


async def test_comment_creates_post_session_and_routes_send():
    import re

    import pytest as _pytest

    _pytest.importorskip("gateway.platforms.base")
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._mention_re = re.compile(r"\[([^\]]*)\]\(max://user/999\)")
    adapter._interactive = None
    fc = _FC()
    adapter._client = fc
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_comment_update())
    for _ in range(50):
        if col.events:
            break
        await __import__("asyncio").sleep(0.01)
    assert col.events and col.events[0].source.chat_id == "mid.777"
    assert "Петя" in col.events[0].text and "Отличный пост" in col.events[0].text
    res = await adapter.send("mid.777", "ответ модератора")
    assert res.success and fc.commented[0] == "mid.777"
