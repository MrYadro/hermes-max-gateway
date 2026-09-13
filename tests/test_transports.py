import asyncio
import json as _json

from aiohttp.test_utils import TestClient, TestServer

from maxbot.models import parse_update
from maxbot.transports import PollingTransport, WebhookTransport


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
    await t.stop()
    task.cancel()
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
    await t.stop()
    task.cancel()


async def test_polling_survives_unexpected_exception():
    from maxbot.transports import PollingTransport

    class SurpriseClient:
        def __init__(self):
            self.n = 0

        async def get_updates(self, marker=None, timeout=90):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("surprise!")  # не MaxApiError
            return (5, [])

    t = PollingTransport(SurpriseClient(), timeout=1, backoff_base=0.001)
    task = asyncio.create_task(t.start(lambda u: None))
    for _ in range(200):
        if t.marker == 5:
            break
        await asyncio.sleep(0.01)
    assert t.marker == 5 and t.healthy()  # пережил неожиданное исключение и продолжил
    await t.stop()
    task.cancel()


UPDATE_TYPES = ["message_created", "message_callback", "bot_started"]


class FakeSubClient:
    def __init__(self):
        self.subscribed = self.unsubscribed = None

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
    await t.stop()
    await http.close()


async def test_webhook_rejects_bad_secret():
    t, fc, http, _ = await _make_client()
    resp = await http.post("/max/webhook", data="{}", headers={"X-Secret": "WRONG"})
    assert resp.status == 401
    await t.stop()
    await http.close()


async def test_webhook_stop_unsubscribes():
    t, fc, http, _ = await _make_client()
    await t.stop()
    await http.close()
    assert fc.unsubscribed is True
