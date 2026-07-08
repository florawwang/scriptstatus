"""SMS status replies for long-running localinf2 inference."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs

STATUS_KEYWORDS = re.compile(r"\b(status|progress|update)\b", re.IGNORECASE)
_HTTPD: HTTPServer | None = None
_SERVER_THREAD: threading.Thread | None = None
_ACTIVE_PORT: int | None = None
_HANDLER_CONFIG: dict[str, str] = {
    "status_path": "",
    "allowed_phone": "",
    "account_sid": "",
    "auth_token": "",
    "twilio_from": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
}


class _TwilioSmsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        cfg = _HANDLER_CONFIG
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        params = parse_qs(raw)
        from_num = _normalize_phone(params.get("From", [""])[0])
        body = (params.get("Body", [""])[0] or "").strip()
        allowed = _normalize_phone(cfg.get("allowed_phone", ""))
        print(f"[SMS] from={from_num} body={body!r} allowed={allowed}")

        reply: str | None = None
        if from_num != allowed:
            print("[SMS] ignored — number not authorized")
        elif STATUS_KEYWORDS.search(body):
            reply = format_status_reply(read_status(cfg.get("status_path", "")))
            print(f"[SMS] reply: {reply}")
        else:
            print("[SMS] ignored — text 'status' to get progress")

        if reply:
            if _send_telegram_reply(reply):
                twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
            else:
                # TwiML reply when Telegram is not configured (paid Twilio + 10DLC).
                twiml = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    f"<Response><Message>{_xml_escape(reply)}</Message></Response>"
                )
        else:
            twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

        payload = twiml.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _telegram_api(token: str, method: str, **params: str) -> dict[str, Any]:
    """Call Telegram Bot API via requests (handles macOS SSL certs via certifi)."""
    try:
        import requests
    except ImportError:
        print("[Telegram] install requests: pip install requests")
        return {"ok": False, "description": "requests not installed"}

    url = f"https://api.telegram.org/bot{token.strip()}/{method}"
    try:
        if params:
            resp = requests.post(url, data=params, timeout=10)
        else:
            resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"[Telegram] {method} failed: {exc}")
        return {"ok": False, "description": str(exc)}


def _send_telegram_reply(body: str) -> bool:
    cfg = _HANDLER_CONFIG
    token = (cfg.get("telegram_bot_token") or "").strip()
    chat_id = (cfg.get("telegram_chat_id") or "").strip()
    if not token:
        print("[Telegram] skipped — set TELEGRAM_BOT_TOKEN")
        return False
    if not chat_id:
        print("[Telegram] skipped — set TELEGRAM_CHAT_ID (run discover_telegram_chat_id)")
        return False
    if not _is_numeric_telegram_chat_id(chat_id):
        print(
            f"[Telegram] invalid chat_id {chat_id!r} — must be a numeric id, "
            "not a bot username. Run discover_telegram_chat_id()."
        )
        resolved = _latest_telegram_chat_id(token)
        if not resolved:
            return False
        chat_id = resolved
        _HANDLER_CONFIG["telegram_chat_id"] = resolved
        print(f"[Telegram] auto-resolved chat_id={resolved}")
    result = _telegram_api(token, "sendMessage", chat_id=chat_id, text=body)
    if result.get("ok"):
        print(f"[Telegram] sent to {chat_id}: {body!r}")
        return True
    print(f"[Telegram] failed: {result.get('description', result)}")
    return False


def _is_numeric_telegram_chat_id(chat_id: str) -> bool:
    value = chat_id.strip()
    return bool(value) and value.lstrip("-").isdigit()


def _latest_telegram_chat_id(bot_token: str) -> str | None:
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
    print(
        "No Telegram chat found. Open your bot in Telegram, send 'hi', then text status again."
    )
    return None


def discover_telegram_chat_id(bot_token: str) -> str | None:
    """Print chat IDs from recent messages to your bot. Message the bot first."""
    token = bot_token.strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN first.")
        return None

    data = _telegram_api(token, "getUpdates")
    if not data.get("ok"):
        print(f"[Telegram] getUpdates error: {data}")
        return None

    updates = data.get("result") or []
    if not updates:
        print(
            "No messages yet. Open Telegram, find your bot, send it 'hi', then re-run."
        )
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
        print(
            f"\nPaste this into the inference cell (NOT the bot username):\n"
            f'TELEGRAM_CHAT_ID = "{latest_chat_id}"'
        )
    return latest_chat_id


def probe_telegram(bot_token: str, chat_id: str, *, body: str = "Telegram probe — status replies work.") -> bool:
    """Send one test Telegram message (run from notebook to debug)."""
    _HANDLER_CONFIG.update(
        {"telegram_bot_token": bot_token, "telegram_chat_id": chat_id}
    )
    return _send_telegram_reply(body)


def _send_sms_reply(to: str, body: str) -> str | None:
    """Send outbound SMS and poll until delivered/failed. Returns message SID."""
    cfg = _HANDLER_CONFIG
    sid = cfg.get("account_sid", "")
    token = cfg.get("auth_token", "")
    from_num = cfg.get("twilio_from", "")
    if not all([sid, token, from_num]):
        print("[SMS] missing Twilio creds — cannot send reply")
        return None
    try:
        from twilio.rest import Client

        client = Client(sid, token)
        msg = client.messages.create(body=body, from_=from_num, to=to)
        print(f"[SMS] queued sid={msg.sid} status={msg.status}")
        _log_message_delivery(client, msg.sid)
        return msg.sid
    except Exception as exc:
        print(f"[SMS] text failed: {exc}")
        return None


def _log_message_delivery(client: Any, message_sid: str, *, wait_seconds: float = 8.0) -> None:
    """Poll Twilio until a message is delivered or failed."""
    deadline = time.monotonic() + wait_seconds
    last_status = ""
    while time.monotonic() < deadline:
        msg = client.messages(message_sid).fetch()
        last_status = msg.status or ""
        if last_status in {"delivered", "failed", "undelivered"}:
            if msg.error_code or msg.error_message:
                print(
                    f"[SMS] delivery {last_status}: "
                    f"{msg.error_code} {msg.error_message}"
                )
                if str(msg.error_code) == "30034":
                    print(
                        "[SMS] Fix: register +16504560878 for US A2P 10DLC in Twilio Console.\n"
                        "  Messaging → Regulatory Compliance → Onboarding (Sole Proprietor).\n"
                        "  Until approved, outbound replies to US phones will be blocked."
                    )
            else:
                print(f"[SMS] delivery {last_status}")
            return
        time.sleep(1.0)
    print(f"[SMS] delivery still {last_status or 'pending'} after {wait_seconds:.0f}s")


def probe_outbound_sms(
    account_sid: str,
    auth_token: str,
    twilio_from: str,
    to_phone: str,
    *,
    body: str = "Twilio SMS probe — if you see this, outbound delivery works.",
) -> None:
    """Send one test SMS and print delivery result (run from notebook to debug)."""
    _HANDLER_CONFIG.update(
        {
            "account_sid": account_sid,
            "auth_token": auth_token,
            "twilio_from": twilio_from,
        }
    )
    print(f"Sending probe SMS from {twilio_from} to {to_phone} ...")
    _send_sms_reply(to_phone, body)


def show_recent_sms_logs(
    account_sid: str,
    auth_token: str,
    *,
    limit: int = 5,
) -> None:
    """Print recent Twilio messages with delivery status (debug failed replies)."""
    try:
        from twilio.rest import Client
    except ImportError:
        print("Install twilio first.")
        return
    client = Client(account_sid, auth_token)
    print(f"Last {limit} messages:")
    for msg in client.messages.list(limit=limit):
        err = ""
        if msg.error_code or msg.error_message:
            err = f" | ERR {msg.error_code} {msg.error_message}"
        print(
            f"  {msg.date_created} {msg.direction} "
            f"{msg.from_} -> {msg.to} | {msg.status}{err} | {msg.body!r}"
        )


class _ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_status() -> dict[str, Any]:
    return {
        "state": "idle",
        "mosquito_num": None,
        "mosquito_total": None,
        "current_photo": 0,
        "total_photos": 0,
        "error": None,
        "output_file": None,
        "updated_at": _utc_now(),
    }


def write_status(status_path: str, **fields: Any) -> None:
    status = default_status()
    if os.path.isfile(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                status.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    status.update(fields)
    status["updated_at"] = _utc_now()
    os.makedirs(os.path.dirname(status_path) or ".", exist_ok=True)
    tmp = f"{status_path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f)
    os.replace(tmp, status_path)


def read_status(status_path: str) -> dict[str, Any]:
    if not os.path.isfile(status_path):
        return default_status()
    try:
        with open(status_path, encoding="utf-8") as f:
            data = default_status()
            data.update(json.load(f))
            return data
    except (json.JSONDecodeError, OSError):
        return default_status()


def format_status_reply(status: dict[str, Any]) -> str:
    state = status.get("state", "idle")
    if state == "done":
        out = status.get("output_file") or "output CSV"
        return f"Done! Inference finished. Saved to {out}"

    if state == "failed":
        err = status.get("error") or "unknown error"
        return f"Failed: {err}"

    if state == "running":
        mosquito_num = status.get("mosquito_num")
        mosquito_total = status.get("mosquito_total")
        current = int(status.get("current_photo") or 0)
        total = int(status.get("total_photos") or 0)
        if mosquito_num is not None and total:
            mosquito_part = f"Mosquito {mosquito_num}"
            if mosquito_total is not None:
                mosquito_part += f"/{mosquito_total}"
            return f"{mosquito_part}: {current}/{total} photos"
        return "Inference is running (warming up)."

    return "No inference running right now."


def _normalize_phone(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if digits.startswith("1") and len(digits) == 11:
        return f"+{digits}"
    return number


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _public_webhook_url(
    local_port: int,
    *,
    ngrok_authtoken: str = "",
    status_webhook_url: str = "",
) -> str | None:
    manual = _normalize_webhook_base(
        status_webhook_url or os.environ.get("STATUS_WEBHOOK_URL", "")
    )
    if manual:
        return manual

    ngrok_token = (ngrok_authtoken or os.environ.get("NGROK_AUTHTOKEN", "")).strip()
    if not ngrok_token:
        return None

    try:
        from pyngrok import conf, ngrok

        conf.get_default().auth_token = ngrok_token
        tunnel = ngrok.connect(local_port, "http")
        return str(tunnel.public_url).rstrip("/")
    except Exception as exc:
        print(f"ngrok failed: {exc}")
        return None


def _normalize_webhook_base(url: str) -> str:
    """Accept base URL or full .../sms URL from Twilio/ngrok setup."""
    base = (url or "").strip().rstrip("/")
    if base.endswith("/sms"):
        base = base[: -len("/sms")]
    return base


def configure_twilio_sms_webhook(
    account_sid: str,
    auth_token: str,
    twilio_from: str,
    webhook_base_url: str,
) -> str | None:
    """Point Twilio inbound SMS at webhook_base_url/sms. Returns final sms_url."""
    base = _normalize_webhook_base(webhook_base_url)
    if not base:
        print("No webhook URL provided.")
        return None
    _configure_twilio_sms_webhook(account_sid, auth_token, twilio_from, base)
    return f"{base}/sms"


def _configure_twilio_sms_webhook(
    account_sid: str,
    auth_token: str,
    twilio_from: str,
    webhook_url: str,
) -> bool:
    """Point Twilio inbound SMS at webhook_url/sms. Returns True on success."""
    try:
        from twilio.base.exceptions import TwilioException
        from twilio.rest import Client
    except ImportError:
        print("Install twilio to auto-configure SMS webhook.")
        return False

    base = _normalize_webhook_base(webhook_url)
    sms_url = f"{base}/sms"
    try:
        client = Client(account_sid, auth_token)
        for number in client.incoming_phone_numbers.list(limit=50):
            if _normalize_phone(number.phone_number) == _normalize_phone(twilio_from):
                number.update(sms_url=sms_url, sms_method="POST")
                print(f"Twilio SMS webhook set to: {sms_url}")
                return True
        print(f"Could not find Twilio number {twilio_from} to configure webhook.")
        print(f"Set it manually in Twilio Console to: {sms_url}")
        return False
    except Exception as exc:
        exc_name = type(exc).__name__
        if exc_name == "TwilioException" or "401" in str(exc) or "Authenticate" in str(exc):
            print(
                "Twilio webhook setup failed — invalid credentials (HTTP 401).\n"
                "  1. Go to https://console.twilio.com → Account → API keys & tokens\n"
                "  2. Copy a fresh Auth Token into TWILIO_AUTH_TOKEN in the notebook\n"
                f"  3. Or set the webhook manually to: {sms_url}\n"
                "Inference will continue — Telegram status still works if webhook is already set."
            )
        else:
            print(f"Twilio webhook setup failed: {exc}")
            print(f"Set webhook manually to: {sms_url}")
        return False


def start_status_webhook_server(
    status_path: str,
    allowed_phone: str,
    *,
    port: int = 5050,
    account_sid: str = "",
    auth_token: str = "",
    twilio_from: str = "",
    ngrok_authtoken: str = "",
    status_webhook_url: str = "",
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
) -> str | None:
    """Start background HTTP server for inbound status texts. Returns public webhook base URL."""
    global _HTTPD, _SERVER_THREAD, _ACTIVE_PORT

    _HANDLER_CONFIG.update(
        {
            "status_path": status_path,
            "allowed_phone": allowed_phone,
            "account_sid": account_sid,
            "auth_token": auth_token,
            "twilio_from": twilio_from,
            "telegram_bot_token": telegram_bot_token,
            "telegram_chat_id": telegram_chat_id,
        }
    )

    if telegram_bot_token and telegram_chat_id:
        if _is_numeric_telegram_chat_id(telegram_chat_id):
            print(f"Telegram status fallback enabled (chat_id={telegram_chat_id}).")
        else:
            print(
                f"Telegram token set but chat_id {telegram_chat_id!r} looks wrong "
                "(must be numeric). Will try auto-resolve when you text status."
            )
    elif telegram_bot_token:
        print(
            "Telegram bot token set but no chat_id — "
            "message your bot, then run discover_telegram_chat_id()."
        )

    if _SERVER_THREAD is not None and _SERVER_THREAD.is_alive():
        print(
            f"Status SMS server already running on "
            f"http://127.0.0.1:{_ACTIVE_PORT or port}/sms (config updated)"
        )
    else:
        try:
            httpd = _ReuseHTTPServer(("0.0.0.0", port), _TwilioSmsHandler)
            _SERVER_THREAD = threading.Thread(
                target=httpd.serve_forever,
                name="inference-sms-status",
                daemon=True,
            )
            _HTTPD = httpd
            _ACTIVE_PORT = port
            _SERVER_THREAD.start()
            print(f"Status SMS server listening on http://127.0.0.1:{port}/sms")
        except OSError as exc:
            if getattr(exc, "errno", None) != 48:
                raise
            _ACTIVE_PORT = port
            print(
                f"Port {port} already in use (from a previous run). "
                "Reusing that server — no need to restart."
            )

    public_url = _public_webhook_url(
        port,
        ngrok_authtoken=ngrok_authtoken,
        status_webhook_url=status_webhook_url,
    )
    if public_url:
        if account_sid and auth_token and twilio_from:
            _configure_twilio_sms_webhook(
                account_sid, auth_token, twilio_from, public_url
            )
        else:
            print(f"Set Twilio inbound SMS webhook to: {public_url}/sms")
        return public_url

    print(
        "Could not create a public URL — SMS status replies will NOT work yet.\n"
        "  1. Sign up at https://dashboard.ngrok.com/get-started/your-authtoken\n"
        "  2. Paste your token into NGROK_AUTHTOKEN in this notebook and re-run\n"
        f"  OR run `ngrok http {port}` in Terminal and set STATUS_WEBHOOK_URL to that https URL"
    )
    return None
