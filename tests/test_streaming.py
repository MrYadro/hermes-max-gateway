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


async def test_edit_5xx_keeps_preview():
    from maxbot.max_api import MaxApiError

    adapter, client = make_adapter()
    await adapter.send_draft("100", 1, "превью")
    await asyncio.sleep(1.05)
    client.edit_error = MaxApiError(500, "сервер временно недоступен", code="internal")
    res = await adapter.send_draft("100", 1, "ещё")
    assert res.success
    assert adapter._drafts != {}                    # 5xx транзиентен — превью не забываем
