# Parity: hermes-max-gateway vs MAX Bot API (docs + live probes, 2026-09)

Сравнение поверхности Bot API (документация dev.max.ru + живые пробы) и того,
что реализует плагин. ✓ — реализовано, ◐ — частично, ✗ — не реализовано/невозможно.

## Методы API

| Метод | Плагин | Где |
|---|---|---|
| `GET /me` | ✓ | connect: проверка бота |
| `PATCH /me/commands` | ✓ | регистрация слеш-команд |
| `GET /chats/{id}` | ✓ | `get_chat_info`, `max_group info` |
| `PATCH /chats/{id}` | ✓ | `max_group rename` |
| `POST /chats/{id}/actions` | ✓ | typing, sending_photo/video/audio/file, **mark_seen** |
| `GET/PUT/DELETE /chats/{id}/pin` | ✓ | `max_group pin/unpin/pinned` |
| `GET /chats/{id}/members/me` | ✓ | `max_group my_permissions` |
| `GET /chats/{id}/members`, `/admins` | ✓ | `max_group members/admins` |
| `POST/DELETE /chats/{id}/members/admins` | ✓ | `max_group add_admin/remove_admin` |
| `DELETE /chats/{id}/members/{uid}` | ✓ | `max_group kick` |
| `DELETE /chats/{id}/members/me` | ✓ | `max_group leave` |
| `GET /chats` (список чатов) | ✗ | deprecated платформой с 06.2026 |
| `POST /chats/{id}/members` (добавить) | ✗ | удаляется платформой с 30.09.2026 |
| `GET/POST/DELETE /subscriptions` | ✓ | WebhookTransport |
| `GET /updates` | ✓ | PollingTransport (по умолчанию) |
| `POST /uploads` | ✓ | curl-multipart; video/audio токен из слота, image/file — из загрузки |
| `POST /messages` | ✓ | текст/markdown + все вложения |
| `PUT /messages` | ✓ | правка: streaming-превью, снятие кнопок пустым массивом |
| `DELETE /messages` | ✓ | |
| `GET /messages/{id}` | ✓ | догрузка вложений, reply-контекст, forward-контент |
| `GET /videos/{token}` | ✓ | минимальная рендия + миниатюра для агента |
| `POST /answers` | ✓ | toast на нажатие кнопки |
| Комментарии каналов (GET/POST/PUT/DELETE) | ✓ | `max_channel` |

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

| update_type | Плагин |
|---|---|
| message_created | ✓ |
| message_callback | ✓ (без объекта message — mid из стейта кнопок) |
| bot_started | ✓ (приветствие + callback-кнопки: /new, /status, /help) |
| message_edited | ✓ (повторная обработка; правки своих игнорируются) |
| message_removed | ✓ (interrupt хода + заметка-ретракция в сессию) |
| bot_added | ✓ (знакомство в группе) |
| chat events | ✗ не подписаны |

## Прочее

| Фича | Статус |
|---|---|
| Streaming-превью (PUT ≤1/сек при лимите 2/сек) | ✓ |
| Сегментация длинных ответов (≤4000) | ✓ |
| Reply-контекст (link.type=reply) | ✓ |
| Упоминания в группах (@username оба формата) | ✓ |
| Троттлинг 30 rps / 2 msg/сек | ✓ |
| Формат html | ✗ (осознанно, только markdown) |
