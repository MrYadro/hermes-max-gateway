# MAX Mini-App «Assistants»: UI переключения профилей — дизайн

Дата: 2026-09-27
Статус: согласован (блоки 1–2 одобрены в сессии)

## Цель

Мини-приложение MAX для управления профилями-ассистентами: просмотр чатов и их
текущих профилей, переключение в один тап, обзор карточек профилей. Управление
доступно только администратору (владельцу). In-chat `/assistant` остаётся как есть.

## Контекст (что уже есть)

- `maxbot/profile_switch.py` — карта `_chat_profiles.json` (chat_id → профиль),
  `available_profiles()`, `profile_description()` (первая строка SOUL.md)
- `MaxAdapter.build_source` штампует `source.profile` (мультиплекс)
- `WebhookTransport` (maxbot/transports.py) — свой aiohttp-app на `MAX_WEBHOOK_PORT`,
  в режиме вебхуков уже опубликован наружу (единый публичный ingress)
- Ядро: плагинных HTTP-хендлеров на api_server НЕ используем — роуты едут
  на webhook-слушатель плагина

## Архитектура

```
MAX клиент ──(мини-апп: React+MAX UI)──▶ /max/app/*  ┐
                                                      ├ один порт MAX_WEBHOOK_PORT
MAX Bot API ──(updates)──▶ /max/webhook (как сейчас) ┘
                                     │
                    aiohttp app (WebhookTransport + extra_routes)
                                     │
                     maxbot/webapp_api.py ──▶ profile_switch.py ──▶ _chat_profiles.json
                                          └▶ реестр _known_chats.json
```

Компоненты:
1. `maxbot/transports.py` — `WebhookTransport(..., extra_routes=[(method, path, handler)])`,
   роуты добавляются в тот же app до старта
2. `maxbot/webapp_api.py` (новый) — API-хендлеры, валидация initData, отдача статики
3. `maxbot/webapp/` (новый) — vite + TypeScript + React + `@maxhub/max-ui`;
   собранный `dist/` коммитится в репо; `node_modules` в .gitignore
4. `maxbot/profile_switch.py` — +реестр известных чатов (`_known_chats.json`)

## API (префикс `/max/app/`)

Все ответы JSON. Авторизация — заголовок `X-WebApp-InitData` со значением
`window.WebApp.initData` (см. «Безопасность»).

### `GET /max/app/state`
Ответ:
```json
{
  "chats": [{"chat_id": "500", "chat_type": "channel", "profile": "work"}],
  "profiles": [{"name": "default", "description": "первая строка SOUL.md"}],
  "me": {"user_id": 13, "is_admin": true}
}
```
- `chats` — объединение реестра known_chats и карты профилей
  (чаты из карты без реестра тоже показываются, тип `"unknown"`);
  `profile` = карта либо `"default"`
- `chat_type`: `"dm" | "group" | "channel" | "unknown"`

### `POST /max/app/set`
Тело: `{"chat_id": "500", "profile": "work"}`
- успех: `{"ok": true, "state": <как GET state>}`
- неизвестный профиль: `400 {"error": "unknown profile"}`
- не-админ: `403`

### Статика
`GET /max/app/` → `dist/index.html`; `GET /max/app/assets/...` → файлы бандла.
Публична (секретов не содержит), `Cache-Control: no-cache` для index.html.

## Безопасность

1. **Валидация initData** (официальная схема dev.max.ru/docs/webapps/validation):
   - parse пары `key=value` (URL-decode), `hash` ровно один — сохранён и исключён
   - сортировка по ключу, `launch_params = "k=v\n..."` (без `\n` в конце)
   - `secret_key = HMAC-SHA256(key="WebAppData", msg=BOT_TOKEN)`
   - `hex(HMAC-SHA256(secret_key, launch_params)) == hash`
   - `auth_date` свежесть ≤ 3600с
   - успех → `user.id`; иначе запрос отклонён (401)
2. **Админы**: env `MAX_ASSISTANT_ADMINS` (user_id через запятую); пуст →
   наследует `MAX_ALLOWED_USERS`. `is_admin` в `me` — для UI; мутации (`set`)
   только админам; `state` — тоже только админам (данные о чатах приватны).
3. **Dev-режим**: `MAX_ASSISTANT_DEV=1` + запрос с 127.0.0.1 → query-параметр
   `dev_user_id` заменяет initData (для локальной работы в браузере без MAX).
   В проде выключен.
4. Статика без авторизации; никаких секретов во фронте.
5. Токен бота для HMAC читается из scoped `MAX_ACCESS_TOKEN` (уже есть).

## Реестр известных чатов

`<home>/maxbot-chat-memory/_known_chats.json`: `{"<chat_id>": {"type": "dm"}}`.
- пополняется в `_on_message` (dm/group) и `_on_comment` (channel) — только тип
  и id, без имён/контента (PII-гигиена)
- атомарная запись + mtime-кэш (паттерн `profile_switch`)
- функции: `remember_chat(home, chat_key, chat_type)`, `known_chats(home)`

## Фронтенд

- Две вкладки (без роутера): «Чаты» и «Профили»
- **Чаты**: `CellList` + `CellSimple` — иконка типа (👤/👥/📢), chat_id, бейдж
  текущего профиля (`Counter`/`Typography.Label`); тап → раскрытие под ячейкой
  списка кнопок профилей (`Button`, текущий — с ✓) → `POST set` → локальное
  обновление и схлопывание
- **Профили**: карточки — имя, описание, default-бейдж
- `Spinner` при загрузке; ошибки — `Typography.Body` строка
- `X-WebApp-InitData` из `window.WebApp.initData`; dev: `?dev_user_id=`
- Тема/платформа — автоматически из провайдера `MaxUI`

## Интеграция с /assistant

Если задан `MAX_WEBHOOK_URL`, в ответ `/assistant` (пикер и прямое переключение)
добавляется строка: `🖥 Управлять в приложении: <MAX_WEBHOOK_URL>/max/app/`.

## Сборка и разработка

- `maxbot/webapp/package.json`: react, react-dom, @maxhub/max-ui, vite, typescript
- `npm run dev` — vite на :5173, прокси `/max/app/*` → `http://localhost:<MAX_WEBHOOK_PORT>`
- `npm run build` → `dist/` (коммитим; сборка только при изменении фронта)
- Юнит-тесты фронта не пишем (объём мал) — ручная проверка + smoke сборки

## Тестирование (бэкенд, RED→GREEN, синтетика)

В `tests/test_webapp_api.py`:
- валидная подпись initData → user_id извлечён (генерируем подпись по схеме в тесте)
- битая подпись / повторённый hash / просроченный auth_date → отклонено
- админ получает state/set; не-админ — 403
- dev-режим: с 127.0.0.1 работает, с «внешнего» remote — нет
- known_chats: remember/load, атомарность, объединение с картой в state
- set: переключение пишет карту, неизвестный профиль → 400
- статика: index.html отдаётся
- `/assistant` добавляет ссылку при заданном MAX_WEBHOOK_URL

## Ограничения и риски

- Мини-апп не публикуем в каталог MAX (личное использование, без модерации)
- Известный саботаж-риск: transports.py:134 `secret=self._secret` — плейсхолдер
  в подписке вебхуков; чиним отдельно при включении webhook-режима (не блокер
  спеки, но обязательный чек перед деплоем)
- Нужен node-окружение для сборки фронта (первый npm-пакет в репо)
- Мини-апп доступен только когда транспорт в webhook-режиме (в polling слушателя нет)

## Деплой (потом, когда сервер доступен)

1. `MAX_UPDATES_MODE=webhook` + `MAX_WEBHOOK_URL=https://<домен>` (+ починка SECRET_0__)
2. `npm run build` в maxbot/webapp → dist закоммитить/залить
3. `MAX_ASSISTANT_ADMINS=<твой user_id>`
4. Рестарт гейтвея → `/assistant` даёт ссылку → мини-апп открывается в MAX
