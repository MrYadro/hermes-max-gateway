#!/usr/bin/env python3
"""Живой smoke-тест бота MAX: get_me → send → один цикл polling.
Запуск: MAX_ACCESS_TOKEN=... python scripts/live_check.py --chat-id 123 [--send-only]
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from maxbot.max_api import MaxApiError, MaxClient  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", type=int, help="chat_id для тестовой отправки")
    parser.add_argument("--send-only", action="store_true", help="не слушать polling")
    parser.add_argument("--token", default=os.environ.get("MAX_ACCESS_TOKEN"))
    parser.add_argument("--base", default=os.environ.get("MAX_API_BASE"))
    args = parser.parse_args()
    if not args.token:
        print("Нужен токен: --token или MAX_ACCESS_TOKEN")
        return 2
    async with MaxClient(args.token, base_url=args.base) as client:
        me = await client.get_me()
        print(f"✅ get_me: user_id={me.user_id} name={me.name!r} username={me.username!r}")
        if args.chat_id:
            mid = await client.send_message(args.chat_id, "hermes-max-gateway: smoke-тест ✅")
            print(f"✅ send_message → {mid}")
        if not args.send_only:
            print("… слушаю апдейты 60с (напишите боту)")
            marker = None
            try:
                async def _cycle():
                    nonlocal marker
                    marker, updates = await client.get_updates(marker, timeout=55)
                    for u in updates:
                        print(f"← update_type={u.update_type} marker={u.marker}")
                        if u.message:
                            print(f"  chat={u.message.chat_id}/{u.message.chat_type} "
                                  f"from={u.message.sender.user_id if u.message.sender else '?'} "
                                  f"text={u.message.body.text!r} atts={len(u.message.body.attachments)}")
                await asyncio.wait_for(_cycle(), timeout=70)
            except (asyncio.TimeoutError, MaxApiError) as exc:
                print(f"polling завершён ({exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
