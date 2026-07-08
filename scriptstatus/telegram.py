"""Telegram notifier: push messages to a chat via a bot token."""

from __future__ import annotations

import os
from typing import Any


def _telegram_api(
    token: str, method: str, *, http_timeout: float = 10.0, **params: Any
) -> dict[str, Any]:
    """Call Telegram Bot API via requests (handles macOS SSL certs via certifi).

    http_timeout is the requests-level timeout; a `timeout` entry in params is
    Telegram's own long-polling parameter and is passed through untouched.
    """
    try:
        import requests
    except ImportError:
        print("[Telegram] install requests: pip install requests")
        return {"ok": False, "description": "requests not installed"}

    url = f"https://api.telegram.org/bot{token.strip()}/{method}"
    try:
        if params:
            resp = requests.post(url, data=params, timeout=http_timeout)
        else:
            resp = requests.get(url, timeout=http_timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"[Telegram] {method} failed: {exc}")
        return {"ok": False, "description": str(exc)}


def _is_numeric_chat_id(chat_id: str) -> bool:
    value = (chat_id or "").strip()
    return bool(value) and value.lstrip("-").isdigit()


def _latest_chat_id(bot_token: str) -> str | None:
    """Return the most recent user chat_id from bot updates."""
    data = _telegram_api(bot_token, "getUpdates")
    if not data.get("ok"):
        print(f"[Telegram] getUpdates error: {data}")
        return None
    updates = data.get("result") or []
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            return str(chat_id)
    print("No Telegram chat found. Open your bot in Telegram and send it 'hi' first.")
    return None


def discover_chat_id(bot_token: str | None = None) -> str | None:
    """Print chat IDs from recent messages to your bot. Message the bot first.

    Falls back to the TELEGRAM_BOT_TOKEN env var if no token is passed.
    """
    token = (bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN first.")
        return None

    data = _telegram_api(token, "getUpdates")
    if not data.get("ok"):
        print(f"[Telegram] getUpdates error: {data}")
        return None

    updates = data.get("result") or []
    if not updates:
        print("No messages yet. Open Telegram, find your bot, send it 'hi', then re-run.")
        return None

    seen: set[str] = set()
    latest_chat_id: str | None = None
    print("Recent chats that messaged your bot:")
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        chat_id_str = str(chat_id)
        latest_chat_id = chat_id_str
        if chat_id_str in seen:
            continue
        seen.add(chat_id_str)
        name = chat.get("first_name") or chat.get("title") or "unknown"
        username = chat.get("username")
        user_part = f" @{username}" if username else ""
        print(f"  chat_id={chat_id_str} ({name}{user_part})")

    if latest_chat_id:
        print(f'\nUse this (NOT the bot username): chat_id="{latest_chat_id}"')
    return latest_chat_id


class TelegramNotifier:
    """Sends messages to one Telegram chat.

    Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars when args are omitted.
    If chat_id is missing or non-numeric, it is auto-resolved from the bot's
    recent updates on first send (message your bot once beforehand).
    """

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = (bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

    @property
    def configured(self) -> bool:
        return bool(self.bot_token)

    def send(self, body: str) -> bool:
        if not self.bot_token:
            print("[Telegram] skipped — set TELEGRAM_BOT_TOKEN")
            return False
        if not _is_numeric_chat_id(self.chat_id):
            if self.chat_id:
                print(
                    f"[Telegram] invalid chat_id {self.chat_id!r} — must be numeric, "
                    "not a bot username. Trying to auto-resolve..."
                )
            resolved = _latest_chat_id(self.bot_token)
            if not resolved:
                return False
            self.chat_id = resolved
            print(f"[Telegram] auto-resolved chat_id={resolved}")
        result = _telegram_api(self.bot_token, "sendMessage", chat_id=self.chat_id, text=body)
        if result.get("ok"):
            print(f"[Telegram] sent to {self.chat_id}: {body!r}")
            return True
        print(f"[Telegram] failed: {result.get('description', result)}")
        return False

    def probe(self, body: str = "jobwatch Telegram probe — notifications work.") -> bool:
        """Send one test message (handy for debugging setup)."""
        return self.send(body)
