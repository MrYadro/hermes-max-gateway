# Parity: hermes-max-gateway vs MAX Bot API (OpenAPI-схема + live probes, 2026-10)

Сравнение поверхности Bot API и того, что реализует плагин. ✓ — реализовано,
◐ — частично, ✗ — не реализовано/невозможно.

Паритет машинно-проверяемый: официальная схема завендорена в
`tests/fixtures/max-api-schema.yaml` и сторожится тестами
(`test_parity.py` — типы событий по дискриминатору `Update`,
`test_schema_conformance.py` — пути/параметры/enum/ключи ответов всех методов
`MaxClient`). Обновление схемы → диф виден сразу.

Автоматизация: `scripts/update_schema.py` тянет свежую схему локально;
воркфлоу `.github/workflows/parity.yml` (план: понедельник 06:00 UTC) делает
это сам — при изменении схемы: тесты зелёные → фикстура коммитится в main,
тесты красные → открывается PR с новой схемой и логом падений.

## Методы API

| Метод | Плагин | Где |
|---|---|---|
| `GET /me` | ✓ | connect: проверка бота |
| `PATCH /me/commands` | ✓ | регистрация слеш-команд |
| `GET /chats/{id}` | ✓ | `get_chat_info`, `max_group info` |
| `PATCH /chats/{id}` | ✓ | `max_group rename` (гейт `change_chat_info`) |
| `POST /chats/{id}/actions` | ✓ | typing, sending_photo/video/audio/file, **mark_seen** |
| `GET/PUT/DELETE /chats/{id}/pin` | ✓ | `max_pin`, `max_group pin/unpin/pinned` (гейт `pin_message`) |
| `GET /chats/{id}/members/me` | ✓ | `max_group my_permissions`; ленивое наполнение кэша прав |
| `GET /chats/{id}/members` | ✓ | `max_group members` |
| `GET /chats/{id}/members/admins` | ✓ | `max_group admins` (ключ `members` по схеме) |
| `POST /chats/{id}/members/admins` | ✓ | `max_group add_admin` (гейт `add_admins`) |
| `DELETE /chats/{id}/members/admins/{uid}` | ✓ | `max_group remove_admin` (гейт `add_remove_members`) |
| `DELETE /chats/{id}/members?user_id=` | ✓ | `max_group kick` (гейт `add_remove_members`; path-варианта в схеме нет) |
| `DELETE /chats/{id}/members/me` | ✓ | `max_group leave` |
| `GET /chats` (список чатов) | ✗ | deprecated платформой с 06.2026 |
| `POST /chats/{id}/members` (добавить) | ✗ | удаляется платформой с 30.09.2026 |
| `GET/POST/DELETE /subscriptions` | ✓ | WebhookTransport; подписка всеми типами схемы, unsubscribe с required `url` |
| `GET /updates` | ✓ | PollingTransport (по умолчанию); types = схема минус webhook-only |
| `POST /uploads` | ✓ | curl-multipart; video/audio токен из слота, image/file — из загрузки |
| `POST /messages` | ✓ | текст/markdown + все вложения |
| `PUT /messages` | ✓ | правка: streaming-превью, снятие кнопок пустым массивом |
| `DELETE /messages` | ✓ | |
| `GET /messages/{id}` | ✓ | догрузка вложений, reply-контекст, forward-контент |
| `GET /videos/{token}` | ✓ | минимальная рендия + миниатюра для агента |
| `POST /answers` | ✓ | toast на нажатие кнопки |
| Комментарии каналов (GET/POST/PUT/DELETE) | ✓ | `max_channel`; GET читает ключ `messages` по схеме |

## Вложения: приём

| Тип | Плагин | Примечание |
|---|---|---|
| image | ✓ | авто-описание по-русски (vision) |
| video | ✓ | минимальная рендия + до 4 кадров (ffmpeg, 512px) для vision |
| audio/voice | ✓ | расшифровка локальным whisper (ru) до агента |
| file | ✓ | сниф магики → расширение; .md/.txt инлайном (с фреймингом «данные») |
| sticker | ✓ | каталог «Мишка» → описание без vision; неизвестные — превью-картинкой |
| share | ✓ | title/url текстом |
| contact | ✓ | vcf + max_info |
| location | ✓ | плоские `latitude`/`longitude` (fix: парсер терял) |
| forward (link.type=forward) | ✓ | контент инлайном из `link.message`, фрейминг «данные» |
| inline_keyboard | ✓ | диспетчер кнопок |

## Вложения: отправка

| Тип | Плагин | Примечание |
|---|---|---|
| text/markdown | ✓ | санитайзер: таблицы строками в инлайн-код (```-fence ломает парсер MAX после кириллицы) |
| image | ✓ | upload / внешний url |
| video / audio / file | ✓ | upload |
| sticker | ✓ | `code` = hex(id); без текста в сообщении |
| location | ✓ | плоские поля, без `payload` |
| contact | ✓ | vCard (`max_contact`) |
| inline_keyboard | ✓ | approve/deny, пикеры, страницы; исчезают после нажатия |
| **share** | ✗ | API: 200, но вложение вырезается даже в эхе создания (все комбинации url/token перепробованы) |
| **poll (опросы)** | ✗ | API ботам не даёт |
| **reactions (реакции)** | ✗ | эндпоинтов нет |
| **кружки (video notes)** | ✗ | не доставляются ботам (подпись приходит, вложение — нет); отправить нельзя: нет типа слота, доп. поля payload игнорируются, квадратное видео рендерится квадратом |

## Обновления

Подписка — полный паритет со схемой: webhook получает все типы из
дискриминатора `Update`, polling — все минус webhook-only.

| update_type | Плагин |
|---|---|
| message_created | ✓ |
| message_callback | ✓ (без объекта message — mid из стейта кнопок) |
| bot_started | ✓ (приветствие + callback-кнопки: /new, /status, /help) |
| message_edited | ✓ (повторная обработка; правки своих игнорируются) |
| message_removed | ✓ (interrupt хода + заметка-ретракция в сессию) |
| bot_added | ✓ (знакомство в группе) |
| bot_removed / dialog_removed | ✓ (purge кэшей чата, включая права) |
| comment_created | ✓ (ветки комментариев каналов — thread-сессии) |
| **bot_admin_permissions_changed** | ✓ (webhook-only; кэш `BOT_RIGHTS` + гейты прав, fail-open) |
| chat_title_changed | ✓ (кэш названий `_known_chats` не устаревает) |
| bot_stopped / dialog_cleared / dialog_muted / dialog_unmuted | ◐ подписаны, лог наблюдаемости |
| user_added / user_removed | ◐ подписаны, лог наблюдаемости |
| comment_edited / comment_removed | ◐ подписаны, лог наблюдаемости |

## Прочее

| Фича | Статус |
|---|---|
| Streaming-превью (PUT ≤1/сек при лимите 2/сек) | ✓ |
| Сегментация длинных ответов (≤4000) | ✓ |
| Reply-контекст (link.type=reply) | ✓ |
| Упоминания в группах (@username оба формата) | ✓ |
| Троттлинг 30 rps / 2 msg/сек | ✓ |
| Гейт прав `ChatAdminPermission` (кэш по событию + lazy membership) | ✓ |
| Формат html | ✗ (осознанно, только markdown) |
