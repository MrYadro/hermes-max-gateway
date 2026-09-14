"""Транспорты получения апдейтов MAX: long polling и webhook."""
import asyncio
import logging
from typing import Awaitable, Callable, List, Optional, Protocol

from .max_api import MaxApiError
from .models import parse_update

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
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("max polling: фоновая задача упала")
        self._healthy = False

    async def _pause_backoff(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stopped.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def _loop(self, on_update: OnUpdate) -> None:
        delay = self._backoff_base
        while not self._stopped.is_set():
            await asyncio.sleep(0)
            try:
                marker, updates = await self._client.get_updates(self.marker, timeout=self._timeout)
                self._failures = 0
                self._healthy = True
                delay = self._backoff_base
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
                await self._pause_backoff(delay)
                delay = min(60.0, delay * 2)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._failures += 1
                self._healthy = False
                logger.exception("max polling: неожиданная ошибка #%d, ждём %.1fс",
                                 self._failures, delay)
                await self._pause_backoff(delay)
                delay = min(60.0, delay * 2)


class WebhookTransport:
    _SECRET_HEADERS = ("x-max-bot-api-secret", "x-secret")

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
