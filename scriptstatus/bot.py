"""Telegram status bot: message "status" to your bot, get job progress back.

Uses long polling (outbound HTTPS only), so it works from laptops, lab
machines, and cluster nodes behind firewalls — no public URL, tunnel, or
webhook needed.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from .status import StatusFormatter, format_status, read_status
from .telegram import TelegramNotifier, _telegram_api

STATUS_KEYWORDS = re.compile(r"(?:^/status\b|\b(status|progress|update)\b)", re.IGNORECASE)

_HELP_TEXT = "Send 'status' (or /status) to get the current job progress."


class TelegramStatusBot:
    """Answers "status" messages sent to your Telegram bot.

    Example:
        bot = TelegramStatusBot("run_status.json")  # reads TELEGRAM_BOT_TOKEN
        bot.start()
        # ... run your job; message the bot "status" from your phone anytime.

    If chat_id is configured (arg or TELEGRAM_CHAT_ID), only that chat gets
    replies. Otherwise the bot locks onto the first chat that messages it.
    """

    def __init__(
        self,
        status_path: str,
        *,
        bot_token: str = "",
        chat_id: str = "",
        formatter: StatusFormatter = format_status,
        poll_timeout: int = 25,
    ):
        self.status_path = status_path
        self.notifier = TelegramNotifier(bot_token, chat_id)
        self.formatter = formatter
        self.poll_timeout = poll_timeout
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- message handling ---------------------------------------------------

    def _handle_message(self, chat_id: str, text: str) -> str | None:
        """Return the reply for one inbound message, or None to stay silent."""
        allowed = self.notifier.chat_id
        if allowed and chat_id != allowed:
            print(f"[Bot] ignored message from unauthorized chat {chat_id}")
            return None
        if not allowed:
            self.notifier.chat_id = chat_id
            print(f"[Bot] locked onto chat_id={chat_id}")

        if text.startswith("/start"):
            return _HELP_TEXT
        if STATUS_KEYWORDS.search(text):
            reply = self.formatter(read_status(self.status_path))
            print(f"[Bot] status request from {chat_id}: {reply}")
            return reply
        return _HELP_TEXT

    # -- polling loop ---------------------------------------------------------

    def _latest_update_id(self) -> int | None:
        """Fetch the newest update id so old messages aren't replayed on start."""
        data = _telegram_api(self.notifier.bot_token, "getUpdates", offset=-1, limit=1)
        if not data.get("ok"):
            return None
        updates = data.get("result") or []
        return updates[-1]["update_id"] if updates else 0

    def _poll_loop(self, first_offset: int) -> None:
        offset = first_offset
        while not self._stop.is_set():
            data = _telegram_api(
                self.notifier.bot_token,
                "getUpdates",
                http_timeout=self.poll_timeout + 10,
                offset=offset,
                timeout=self.poll_timeout,
            )
            if not data.get("ok"):
                # Network blip or API error: back off briefly, then retry.
                self._stop.wait(5.0)
                continue
            for update in data.get("result") or []:
                offset = max(offset, update["update_id"] + 1)
                message = update.get("message") or update.get("edited_message") or {}
                chat = message.get("chat") or {}
                chat_id = chat.get("id")
                text = (message.get("text") or "").strip()
                if chat_id is None or not text:
                    continue
                reply = self._handle_message(str(chat_id), text)
                if reply:
                    _telegram_api(
                        self.notifier.bot_token,
                        "sendMessage",
                        chat_id=str(chat_id),
                        text=reply,
                    )

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> bool:
        """Start polling in a daemon thread. Returns True if running."""
        if not self.notifier.bot_token:
            print("[Bot] set TELEGRAM_BOT_TOKEN first (create a bot via @BotFather).")
            return False
        if self._thread is not None and self._thread.is_alive():
            print("[Bot] already running.")
            return True

        latest = self._latest_update_id()
        if latest is None:
            print("[Bot] could not reach Telegram — check the bot token and network.")
            return False

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(latest + 1 if latest else 0,),
            name="jobwatch-telegram-bot",
            daemon=True,
        )
        self._thread.start()
        if self.notifier.chat_id:
            print(f"[Bot] listening — message your bot 'status' (chat_id={self.notifier.chat_id}).")
        else:
            print("[Bot] listening — open your bot in Telegram and send it 'status'.")
        return True

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    # -- push notifications -----------------------------------------------------

    def notify(self, body: str | None = None) -> bool:
        """Push a message to the chat; defaults to the current status summary."""
        if body is None:
            body = self.formatter(read_status(self.status_path))
        return self.notifier.send(body)
