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
# 1) через менеджер плагинов hermes (после публикации репо на GitHub)
hermes plugins install <ваш-логин>/hermes-max-gateway/maxbot
hermes plugins enable max-platform

# 2) pip-пакетом (в то же окружение, где стоит hermes)
pip install git+https://github.com/<вы>/hermes-max-gateway.git

# 3) каталогом плагинов
git clone https://github.com/<вы>/hermes-max-gateway.git /tmp/hmg
cp -r /tmp/hmg/maxbot ~/.hermes/plugins/maxbot
```

Для per-chat памяти дополнительно: `memory.provider: maxbot` в config.yaml.

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

## Особенности и ограничения MAX Bot API (проверено live, 2026-09)

| Что | Поведение | Обход в плагине |
|---|---|---|
| Сертификаты | `*.max.ru` подписан **Russian Trusted Root CA (Минцифры)** | в Linux-контейнере: корни в `/usr/local/share/ca-certificates/` + `update-ca-certificates`, для httpx/certifi — `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt` в env |
| Группы | бот получает события **только будучи админом** чата | назначьте бота администратором группы |
| Упоминания в группах | приходят текстом `@username`, не markdown-ссылкой | гейт понимает оба формата |
| Голосовые | апдейт-пустышка — **известный баг бэкенда**, не ограничение дизайна: [max-bot-api-client-ts#250](https://github.com/max-messenger/max-bot-api-client-ts/issues/250) | плагин уже умеет принимать audio (`payload.url` + `transcription`) — заработает автоматически после фикса MAX |
| Геолокация | `location.payload={}` даже через `request_geo_location` и догрузку сообщения | заглушка «координаты недоступны»; `/geo` шлёт request-кнопку |
| `message_callback` | без объекта `message` | chat_id зашит в payload кнопки, user — из `callback.user` |
| `chat_id` в POST /messages | только query-параметром | — |
| `types` в GET /updates | `message_callback` НЕ входит в дефолт | передаём `types=message_created,message_callback,bot_started` |
| Команды бота | формат `{"name": "new"}` без слэша | — |
| Streaming | требует `streaming.enabled: true` в config.yaml | редактирование превью ≤1 правки/сек (лимит MAX 2/сек) |
| Вложения | payload пуст в апдейте | догрузка `GET /messages/{mid}` |

**Приватность/доступ (осознанные решения):** per-chat память (провайдер `maxbot`, `memory.provider:
maxbot`): группы и каналы видят только свои записи; DM — свои + глобальный блок. При этом
`session_search` остаётся включённым на всех платформах — агент МОЖЕТ читать прошлые сессии
профиля по просьбе из любого чата (включая группы): доступ к памяти не ограничен.
Для жёсткого запрета — `platform_toolsets.max` без toolset `session_search`.

Live-приёмка пройдена: текст/markdown/сегментация/streaming/фото/файл/стикер(с превью)/контакт(vcf+max_info)/кнопки(approve/clarify/пикеры/страницы)/группы/typing/allowlist/маркер-рестарт ✓

## Установка на Linux-сервер (bare metal)

```bash
# под пользователем, от которого работает hermes
hermes plugins install <ваш-логин>/hermes-max-gateway/maxbot   # или cp -r maxbot ~/.hermes/plugins/
grep -q SSL_CERT_FILE ~/.hermes/.env || echo "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt" >> ~/.hermes/.env
hermes plugins enable max-platform
hermes gateway restart
```

## Разработка

```bash
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git ../hermes-agent
uv sync
uv run pytest
uv run ruff check .
```
