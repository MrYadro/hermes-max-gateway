# Hermes MAX Gateway — дизайн

Дата: 2026-09-13
Статус: утверждён (обсуждение с автором)
Репозиторий: `hermes-max-gateway`

## 1. Обзор

Плагин платформы для [Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research),
подключающий мессенджер **MAX** (max.ru) к гейтвею Hermes. Пользователь пишет боту
в MAX — сообщение уходит агенту Hermes, ответ возвращается в чат. Поддерживаются DM,
групповые чаты, медиа (включая стикеры, шеринг и геолокацию на входе), интерактивные
кнопки (approve/deny, clarify, пикеры), streaming-редактирование ответов и доставка
cron-заданий.

Доставка: **внешний плагин** (никаких правок ядра Hermes). Устанавливается копированием
в `~/.hermes/plugins/maxbot/` или как pip-пакет через entry point. Живёт как
самостоятельный проект; публикация в апстрим hermes-agent не планируется.

## 2. Цели

- Полноценный обмен сообщениями MAX ↔ Hermes: текст, markdown, медиа в обе стороны
- Входящие вложения всех типов: image, video, audio/voice, file, **стикеры**,
  **share-вложения (шеринг)**, **контакты**, **геолокация**
- Оба способа получения апдейтов MAX Bot API: long polling (умолчание) и webhook,
  переключение конфигом; одновременно — нельзя (ограничение MAX)
- Интерактивные кнопки: approve/deny опасных команд, clarify, slash-confirm,
  choice-пикер (`/model` и т.п.) — по общему callback-convention Hermes
- Streaming-редактирование ответов (`send_draft` + `PUT /messages`) с троттлингом
  под лимит MAX «2 правки/сек на чат»
- Групповые чаты: mention-гейтинг, ответы на replies
- Крон-доставка (`deliver=max`) и `send_message` вне гейтвей-процесса
- Регистрация слеш-команд бота в MAX (`PATCH /me/commands`)
- Индикатор набора текста (`typing_on`)
- Соответствие паттернам Hermes: token lock, fatal/retryable ошибки, реконнект с backoff,
  редакция секретов, allowlist-авторизация через ядро

## 3. Не-цели (v1)

- **Отправка** стикеров ботом — в MAX Bot API не документирован способ получить
  payload стикера (uploads принимает только image/video/audio/file); стикеры —
  inbound-only. Пересмотрим, если MAX задокументирует
- **Отправка** геолокации — в интерфейсе `BasePlatformAdapter` нет outbound-метода
  для локации; локация — inbound-only
- Мини-приложения MAX (WebApp / MAX Bridge)
- Комментарии в каналах (`comment_created` и др.) — только `message_created`,
  `message_callback`, `bot_started`
- Шифрование/тёмная тема — не применимо
- Поддержка нескольких ботов в одном плагине (один токен = один плагин-инстанс;
  несколько профилей Hermes решают задачу)
- Публикация в апстрим hermes-agent (по решению автора — не планируем)

## 4. Внешние контрактны

### 4.1. Hermes Plugin API (ядро hermes-agent)

Адаптер наследует `BasePlatformAdapter` (`gateway/platforms/base.py`) и регистрируется
через `register(ctx)` → `ctx.register_platform(...)`. Ключевые элементы интерфейса:

- Обязательные методы: `__init__(config)`, `connect() -> bool`, `disconnect()`,
  `send(chat_id, content, ...)`, `send_typing(chat_id)`, `get_chat_info(chat_id)`
- Медиа: `send_image_file`, `send_document`, `send_voice`, `send_video` (опциональные
  в базе, реализуем)
- Интерактив: `send_exec_approval`, `send_clarify`, `send_slash_confirm`,
  `send_choice_picker` (degrade в текст при отсутствии)
- Streaming: `supports_draft_streaming()` → `True` + `send_draft(chat_id, draft_id,
  content, ...)` — превью ответа наращивается редактированием одного сообщения;
  при `False`/`NotImplementedError` ядро само откатывается на `send`+`edit_message`
- Inbound: апдейт нормализуется в `MessageEvent` (`gateway/platforms/event.py`) и
  передаётся `await self.handle_message(event)`; source строится через
  `self.build_source(chat_id, chat_name, chat_type, user_id, user_name)`
- Outbound результат: `SendResult`
- Ошибки: `_set_fatal_error(code, message, retryable=)` / `_notify_fatal_error()`;
  `_mark_connected()` / `_mark_disconnected()`
- Хуки регистрации: `env_enablement_fn`, `apply_yaml_config_fn`,
  `cron_deliver_env_var`, `standalone_sender_fn`, `allowed_users_env`,
  `allow_all_env`, `max_message_length`, `platform_hint`, `setup_fn`
- Session key строит ядро: `agent:main:max:{chat_type}:{chat_id}` — вручную не собирать
- Callback-id convention (разделяется с Telegram/Discord):
  `ea:<choice>:<id>` (exec approval), `cl:<id>:<idx>` (clarify),
  `sc:<choice>:<id>` (slash confirm), `cp:<choice>:<id>` (choice picker)

### 4.2. MAX Bot API (`https://platform-api2.max.ru`)

Авторизация: заголовок `Authorization: <access_token>` (query-параметры не поддерживаются).
Лимит: 30 rps на бота.

Используемые методы:

| Метод | Назначение |
|---|---|
| `GET /me` | идентификация бота (`user_id` для фильтра собственных сообщений) |
| `GET /updates?marker=` | long polling апдейтов |
| `POST /subscriptions` / `GET /subscriptions` / `DELETE /subscriptions` | webhook-режим (url, update_types, secret) |
| `POST /messages` | отправка: `text`, `attachments`, `format: "markdown"`, `notify` |
| `GET /messages/{id}` | догрузка полного сообщения при необходимости |
| `PUT /messages?message_id=` | редактирование сообщений бота: streaming-draft, обновление клавиатуры после нажатия |
| `POST /uploads` | загрузка медиа → `token` для attachment |
| `POST /answers` | подтверждение callback (снять «часики» кнопки) |
| `POST /chats/{chatId}/actions` | `typing_on` и др. sender actions |
| `PATCH /me/commands` | регистрация слеш-команд |
| `GET /chats/{chatId}` | `get_chat_info` |

Ограничения `PUT /messages` (критично для streaming): текст ≤4000 символов;
в DM редактируются сообщения младше 7 суток (с inline_keyboard — без ограничения),
в группах — без ограничения; **не более 2 правок/сек на чат** — наш троттлинг
строже (1 правка/сек, см. 5.3). Вложения при редактировании: пустое поле — без
изменений, пустой массив — удалить все.

Форматирование: `format: "markdown"` — `*em*`/`**strong**`/`__strong__`, `~~strike~~`,
`++underline++`, `` `code` ``, `[текст](url)`, `# заголовок`, `> цитата`,
`[Имя Фамилия](max://user/<id>)` упоминания. HTML-режим не используем.

Апдейты, на которые подписываемся: `message_created`, `message_callback`, `bot_started`
(приветствие при старте диалога с ботом).

Входящие вложения: `image` (≤50 МБ, ≤7680×7680), `video` (≤250 МБ), `audio`
(≤256 МБ / 60 мин), `file` (≤4 ГБ), `sticker`, `share` (медиа с превью),
`contact` (контакт с `hash` — HMAC-SHA256(access_token, vcf_info), если пользователь
поделился через кнопку), `location` (payload с координатами), `inline_keyboard`.

Исходящие вложения: preload через `POST /uploads` (типы только `image`, `video`,
`audio`, `file`; каждому типу — свой upload-домен). Для `image` допустима отправка
по внешнему `url` без загрузки. Токен **переиспользуемый** (доки MAX прямо
рекомендуют кэшировать и переиспользовать токены частых файлов), но жёстко связан
с одним загруженным файлом. Отправка сразу после загрузки может вернуть
`attachment.not.ready` — клиент делает паузу и ретраи с растущим интервалом.

#### Существующая обёртка над API (для информации)

Для MAX существует комьюнити-библиотека [maxapi](https://github.com/love-apples/maxapi)
([PyPI](https://pypi.org/project/maxapi/), MIT): асинхронный фреймворк в стиле aiogram —
`Bot`/`Dispatcher`/фильтры/FSM, polling и webhook (aiohttp/FastAPI/Litestar),
типизированные модели апдейтов, медиа, inline-кнопки. Активно развивается (505+
коммитов, тесты/mypy в CI).

Решение: **не берём как зависимость**, пишем собственный тонкий клиент
(`max_api.py`). Причины: адаптеру Hermes нужен низкоуровневый контроль (персист
курсора маркера, троттлинг 30 rps и 2 правки/сек, семантика fatal/retryable
ошибок ядра), а Dispatcher/FSM/фильтры — лишний слой; минимум зависимостей
упрощает установку плагина в окружение Hermes и исключает конфликты версий.
Используем maxapi как **референс**: сверяемся с их типизированными
моделями апдейтов (`maxapi/types`) при расхождениях документации и реальных ответов
API, и следим за их обновлениями при изменениях MAX Bot API.

Inline-клавиатура: attachment `inline_keyboard`, кнопки
`{type: "callback", text, payload}` (payload ≤128 байт — помещаются `ea:once:123456`).
До 30 рядов × 7 кнопок. После нажатия приходит апдейт `message_callback`
(`callback.timestamp`, `callback.callback_id`, `payload`, `message.location`…).

## 5. Архитектура

Выбранный вариант — **B: модульный пакет с одним адаптером и подменным транспортом**
(polling и webhook взаимоисключающи по ограничению MAX, поэтому один адаптер; транспорт —
внутренняя деталь). Монолит отвергнут из-за объёма логики; sibling-адаптеры (паттерн
WhatsApp) — из-за взаимоисключаемости режимов.

### 5.1. Структура репозитория

```
hermes-max-gateway/
├── pyproject.toml            # пакет maxbot; entry point "hermes.plugins": maxbot
├── README.md                 # установка и настройка (рус.)
├── maxbot/
│   ├── __init__.py           # re-export register(ctx)
│   ├── plugin.yaml           # kind: platform; requires_env / optional_env
│   ├── adapter.py            # MaxAdapter(BasePlatformAdapter)
│   ├── max_api.py            # MaxClient: HTTP-клиент + модели данных (dataclasses)
│   ├── transports.py         # Transport Protocol; PollingTransport; WebhookTransport
│   ├── markdown.py           # sanitize_markdown(text) → MAX-совместимый markdown
│   ├── interactive.py        # кнопки + callback-диспетчер
│   └── uploads.py            # upload + кэш токенов
├── scripts/
│   └── live_check.py         # live smoke против реального API (ручной запуск)
└── tests/
    ├── test_max_api.py
    ├── test_adapter.py
    ├── test_markdown.py
    ├── test_interactive.py
    └── test_transports.py
```

Имя пакета `maxbot` (не `max`): `max` затенял бы builtin `max()`. Имя платформы в
гейтвее: `"max"`.

### 5.2. Поток данных

Inbound:
```
MAX → (transport) → Update JSON → MaxAdapter._handle_update()
  → фильтры: свой sender? тип апдейта? mention-гейтинг в группах?
  → сборка MessageEvent (текст/медиа/reply-контекст)
  → await self.handle_message(event)  # дальше ядро: авторизация → сессия → AIAgent
```

Outbound:
```
ядро Hermes → MaxAdapter.send(chat_id, content)
  → markdown.sanitize(content)
  → сегментация ≤ MAX_MESSAGE_LENGTH (4000)
  → MaxClient.send_message(...) → SendResult
```

Кнопки:
```
агент запрашивает approval → send_exec_approval(chat_id, command, session_key, ...)
  → interactive строит inline_keyboard (callback payload "ea:<choice>:<id>")
  → пользователь нажал → апдейт message_callback → interactive.dispatch(payload)
  → tools.approval.resolve_gateway_approval(session_key, choice)
  → POST /answers (снять часики) + PUT /messages (текст «✅ Одобрено — <имя>»)
```

### 5.3. Компоненты

**`max_api.py` — MaxClient.** Единая точка доступа к HTTP API. `httpx.AsyncClient`
(aiohttp не тянем: httpx уже в зависимостях Hermes). Семфор-троттлинг ≤30 rps с
очередью; на 429 — уважать `Retry-After`. Ретраи (3×, экспоненциальный backoff+jitter)
для сетевых ошибок и 5xx; 4xx (кроме 429) — без ретрая, пробрасываются как
`MaxApiError(status, code, message)`. Таймауты: connect 10с, read 30с (для
`GET /updates` — 95с). `base_url` переопределяется (`MAX_API_BASE`) для тестов.
Модели: dataclasses `User`, `Chat`, `Message`, `MessageBody`, `Attachment`,
`Update`, `SenderAction`, `LinkableButton` + `parse_update(dict)`.

**`transports.py`.**
- `class Transport(Protocol): async start(on_update: Callable[[dict], Awaitable])`,
  `async stop()`, `def healthy() -> bool`
- `PollingTransport(client, on_update)`: цикл `GET /updates?marker=cursor&timeout=90`;
  курсор — последний обработанный marker; персистится адаптером в
  `config.extra["marker"]` (переживает рестарт гейтвея); ошибки сети/5xx →
  backoff-jitter (1с → 60с, множитель 2); пустой список апдейтов — норма (long poll
  истёк)
- `WebhookTransport(client, cfg)`: aiohttp-сервер на `MAX_WEBHOOK_PORT`
  (0.0.0.0), путь `/max/webhook`; при старте `POST /subscriptions`
  (`update_types=[...]`, `secret` из `MAX_WEBHOOK_SECRET`); при stop — `DELETE`.
  Каждый запрос: проверка секрет-заголовка (заголовок/схема проверяются на live-тесте;
  401 при несовпадении), парсинг JSON → `on_update`, ответ 200 быстро (тяжёлая
  обработка — в очереди адаптера, не в HTTP-хендлере)
- Выбор: `MAX_UPDATES_MODE` (env) или `gateway.platforms.max.extra.updates_mode`;
  default `polling`. Если выбран webhook без `MAX_WEBHOOK_URL` — fatal-ошибка конфигурации

**`adapter.py` — MaxAdapter.**
- `connect()`: `GET /me` (валидация токена; 401 → fatal не-ретрайбл) →
  `acquire_scoped_lock("max", sha1(token)[:16])` → выбор и запуск транспорта →
  регистрация команд (`PATCH /me/commands` из статического списка gateway-команд;
  ошибки — warning, не фаталь) → `_mark_connected()`
- `disconnect()`: транспорт stop, `_mark_disconnected()`, release lock
- Inbound `message_created`: `dialog → chat_type="dm"`, `chat → "group"`;
  пропуск своих сообщений (`sender.user_id == self._bot_user_id`); в группах —
  только упоминания бота (`[Имя](max://user/<bot_id>)`, упоминание по username) и
  replies к своим сообщениям; упоминание вырезается из текста
- `bot_started`: приветственное сообщение с подсказкой о pairing/командах
- Медиа inbound — по типу attachment:
  - `image` → скачать → `cache_image_from_bytes` → `MessageType.PHOTO`
  - `video` → скачать (если доступна ссылка; для тяжёлых — только текстовое
    описание) → `MessageType.VIDEO` / описание
  - `audio` → скачать → `cache_audio_from_bytes` → `MessageType.AUDIO`
    (голосовые MAX приходят как audio — мапим в `VOICE` по метаданным, если
    различимо live-тестом)
  - `file` → скачать → `cache_document_from_bytes` → `MessageType.DOCUMENT`
  - `sticker` → превью-изображение стикера в `media_urls` (если доступно) +
    текст `[стикер]` → `MessageType.STICKER`
  - `share` → скачать вложенное медиа при наличии; иначе текстовое описание
    шеринга (заголовок/ссылка) → соответствующий `MessageType`
  - `contact` → текст: имя + телефон (проверку HMAC `hash` делаем опционально,
    для достоверности номера) → `MessageType.TEXT` с описанием
  - `location` → текст `Геолокация: <lat>, <lon>` + ссылка на карту
    (osm/google) → `MessageType.LOCATION`
  - скачивание: ссылка из апдейта, при отсутствии — `GET /messages/{id}`
- `send()`: sanitize → сегментация → отправка; `send_typing()`: `typing_on`
  (в DM — если API отклонит, молча игнорировать — поведение проверим live)
- Streaming (контракт Hermes): `supports_draft_streaming() → True`; `send_draft(chat_id,
  draft_id, content, ...)`: первое превью — `POST /messages` (message_id запоминается
  по draft_id), обновления — `PUT /messages` тем же message_id; **троттлер 1 правка/сек
  на чат** (лимит MAX — 2/сек, берём запас), вне лимита промежуточные чанки скипаются.
  Финальный ответ ядро шлёт обычным `send()` (`prefers_fresh_final_streaming() →
  True`), после чего превью удаляем — `delete_message()` → `DELETE /messages`
- `get_chat_info()`: `GET /chats/{id}` → `{name, type, chat_id}`
- `MAX_MESSAGE_LENGTH = 4000`
- `platform_hint`: англ. текст про MAX: markdown поддержан, кнопки есть, streaming
  работает, аудитория преим. русскоязычная — можно отвечать по-русски

**`markdown.py`.** `sanitize_markdown(text)`: LLM-вывод → безопасный MAX-markdown:
экранирование не-парных `*`/`_`/`` ` ``, заголовки `#`/`##`/`###` → жирный текст
(MAX рендерит все уровни одинаково), таблицы → моноширинный код-блок, срез HTML-тегов,
обрезка ссылок >2048 символов, лимит длины кода-блока, эмодзи-преобразования не делаем.
Детали правил фиксируются тестами.

**`interactive.py`.** Реализации `send_exec_approval` / `send_clarify` /
`send_slash_confirm` / `send_choice_picker`: строим `inline_keyboard` c payload по
convention; состояние (approval_id → session_key, clarify_id → …) — в dict-ах
адаптера, как у Telegram-адаптера. `dispatch_callback(callback)`: префикс payload →
резолвер ядра → `POST /answers` + `PUT /messages` (итоговый текст + пустая клавиатура).
Кнопки с русскими подписями («Одобрить», «Отклонить»…).

**`uploads.py`.** `upload_file(path, type)` → token: `POST /uploads?type=` →
upload-URL (домен зависит от типа) → `POST <upload-url>` файлом (multipart) →
token (для video/audio — из ответа загрузки). Mime-детект по расширению.
Токены переиспользуемые: кэш in-memory `(path, size, mtime) → token` живёт до
рестарта процесса. Ретраи с растущим интервалом на `attachment.not.ready` при
отправке; повторная загрузка, если токен отклонён как невалидный.

**`register(ctx)`** — по образцу IRC-плагина: `name="max"`, `label="MAX"`,
`adapter_factory=MaxAdapter`, `check_fn` (env `MAX_ACCESS_TOKEN`), `setup_fn`
(интерактивный визард), `env_enablement_fn`, `apply_yaml_config_fn`,
`cron_deliver_env_var="MAX_HOME_CHANNEL"`, `standalone_sender_fn`,
`allowed_users_env="MAX_ALLOWED_USERS"`, `allow_all_env="MAX_ALLOW_ALL_USERS"`,
`max_message_length=4000`, `emoji="✈️"`, `pii_safe=False` (токен редаймим),
`platform_hint`.

### 5.4. Конфигурация

| Переменная | Обяз. | Назначение |
|---|---|---|
| `MAX_ACCESS_TOKEN` | да | токен бота из кабинета MAX для бизнеса |
| `MAX_ALLOWED_USERS` | нет | csv user_id |
| `MAX_ALLOW_ALL_USERS` | нет | разрешить всем (dev) |
| `MAX_HOME_CHANNEL` | нет | chat_id для cron-доставки |
| `MAX_UPDATES_MODE` | нет | `polling` (default) / `webhook` |
| `MAX_WEBHOOK_URL` | при webhook | публичный URL (HTTPS, доверенный сертификат) |
| `MAX_WEBHOOK_PORT` | при webhook | локальный порт сервера (default 8443) |
| `MAX_WEBHOOK_SECRET` | при webhook | секрет подписи запросов |
| `MAX_API_BASE` | нет | override endpoint (тесты) |
| `MAX_REGISTER_COMMANDS` | нет | отключить `PATCH /me/commands` (default: вкл) |

Yaml-эквивалент: `gateway.platforms.max.extra.*` с теми же ключами (camel/snake —
как у IRC: `access_token`, `updates_mode`, …). Приоритет env > yaml (гварды
`not os.getenv(...)`).

### 5.5. Обработка ошибок и надёжность

- 401 от `GET /me` при connect → fatal (`max_auth`), не-ретрайбл — гейтвей паркует
  платформу
- Сеть/5xx в рантайме → транспорт самовосстанавливается с backoff; при
  исчерпании — `_set_fatal_error(..., retryable=True)` + `_notify_fatal_error()`
- Конфликт подписки (webhook уже занят другим URL) — пробуем `DELETE` + повторный
  `POST`; не вышло — fatal с понятным сообщением
- Все исключения в `handle_update` — лог + skip (один кривой апдейт не роняет polling)
- Редакция: токен не логируется; в user_id/phone не нуждается, но логи сообщений
  проходят через стандартную редакцию ядра

## 6. Тестирование

- **Unit (pytest, без сети):**
  - `test_max_api.py` — respx-моки: хедеры авторизации, троттлинг, 429+Retry-After,
    ретраи 5xx, парсинг моделей, `parse_update`
  - `test_adapter.py` — апдейты → MessageEvent (dm/group, упоминания, replies,
    свои сообщения, все типы вложений: image/video/audio/file/sticker/share/
    contact/location), сегментация, bot_started, `get_chat_info`, wiring
    `register()` (все поля)
  - `test_markdown.py` — таблицы, заголовки, непарные символы, ссылки, HTML-остатки
  - `test_interactive.py` — payload convention, диспетчер, редактирование после
    нажатия, истёкшие состояния
  - `test_transports.py` — курсор маркера (персист/рестарт), backoff, webhook:
    подпись, 401, подписка при старте/отписка при stop
  - `test_streaming.py` — `send_draft`: POST → PUT тем же message_id, троттлер
    1/сек скипает промежуточные чанки, финал доезжает, fallback на `send` при
    ошибке редактирования
- **Live smoke (`scripts/live_check.py`, ручной):** `get_me` → send в указанный
  chat_id → один цикл polling → печать апдейтов. Токен из `MAX_ACCESS_TOKEN` или
  `--token`
- **Приёмка:** эмулятор не строим; проверка на реальном боте автора по чек-листу из
  README (текст, markdown, картинка туда/обратно, стикер/геолокация/шеринг на входе,
  кнопка approve, streaming длинного ответа, группа+упоминание, /new, cron-доставка,
  рестарт гейтвея без потери маркера)

CI (GitHub Actions): ruff + pytest unit на python 3.11/3.12.

## 7. Риски и открытые вопросы

| Риск/вопрос | Митигация |
|---|---|
| Схема секрет-заголовка webhook недокументирована точно | проверяем live; fallback — при несовпадении секрет не блокируем, логируем (кроме явного 401 от MAX при подписке) |
| `typing_on` в DM может не поддерживаться (док: «в групповой чат») | try/catch → молча no-op в DM |
| Структура `Update`/`Message` может отличаться от док-страниц деталями | модели терпимы к лишним полям; сверка с типами [maxapi](https://github.com/love-apples/maxapi); live smoke на реальном боте до имплементации интерактива |
| Long polling официально «не для production» | позиция: для личного гейтвея ок; webhook-режим для продакшена реализован в v1 |
| `GET /chats` удаляют (июнь 2026) | не используем; chat_id берём из апдейтов |
| payload кнопки ≤128 байт | convention короткий; гварды длины в interactive |
| Лимит 2 правки/сек (`PUT /messages`) при streaming | троттлер 1 правка/сек на чат со скипами промежуточных чанков; при 429 — пауза |
| `attachment.not.ready` сразу после загрузки | ретраи с растущим интервалом в клиенте |
| Различимость voice vs audio во входящих | мапим в AUDIO; уточняем на live-тесте, при различимости — VOICE |

### 7.1. Результаты live-приёмки (2026-09-13, LXC/Proxmox, hermes 0.21.2)

Подтверждено: контракт hermes 0.21.2 для exec-approval — `_send_exec_approval_prompt(prompt)`
(не send_exec_approval); `types` обязателен в GET /updates (message_callback не в дефолте);
`chat_id` — query-параметр POST /messages; команды — `{"name": ...}` без слэша; упоминания в
группах — текстом `@username`; сертификаты MAX — Минцифры (в Linux нужны корни + SSL_CERT_FILE
для httpx); группа требует админ-статус бота; голосовые — апдейт без контента (недоступны);
гео — payload={} всегда (координаты боту недоступны, даже через request_geo_location);
message_callback без message (chat_id в payload, user из callback.user); streaming требует
streaming.enabled в config.yaml. Полный список — в README.

## 8. Планы после v1

- Комментарии каналов, каналы как источник контекста
- Отправка стикеров и геолокаций ботом — если MAX задокументирует API / появятся
  outbound-методы в Hermes
- Голосовые сообщения боту как voice-input (транскрипция) — ЗАБЛОКИРОВАНО платформой:
  MAX не доставляет контент голосовых ботам (подтверждено live); пересмотреть при изменении API
