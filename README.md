# jobwatch

A lightweight Python package for monitoring long-running local jobs via Telegram.

Your job (training run, batch inference, data pipeline, ...) writes progress to a small JSON status file. Message **"status"** to your Telegram bot from anywhere and it replies with the current progress. Milestones (done / failed) can be pushed to your phone automatically.

No server, no tunnel, no webhook, no phone number. The bot long-polls Telegram over outbound HTTPS, so it works from laptops, lab machines, and cluster nodes behind firewalls.

## Setup (~2 minutes)

1. `pip install jobwatch`
2. Create a bot: message [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot`, copy the token.
3. `export TELEGRAM_BOT_TOKEN="123456:ABC-..."`
4. Open your new bot in Telegram and send it any message.

That's it — no chat id needed; the bot locks onto the first chat that messages it. (To pin it explicitly, set `TELEGRAM_CHAT_ID`, discoverable via `jobwatch.discover_chat_id()`.)

## Quick start

```python
from jobwatch import JobStatus, TelegramStatusBot

job = JobStatus("run_status.json", job_name="Training", unit="epochs")

bot = TelegramStatusBot(job.status_path)
bot.start()  # background thread; answers "status" messages from now on

job.start(total=100)
for epoch in range(100):
    train_one_epoch()
    job.update(current=epoch + 1, phase=f"lr={lr:.0e}")
job.done(output="model.pt")

bot.notify()  # push the final status to your phone
```

While it runs, message your bot `status` (or `progress`, `update`, `/status`) and it replies with something like:

> Training: lr=1e-4 — 37/100 epochs

On failure:

```python
try:
    run()
except Exception as exc:
    job.failed(str(exc))
    bot.notify()
    raise
```

## The status file

`JobStatus` wraps an atomically-written JSON file, so a separate process (or the bot's thread) can always read a consistent snapshot:

```python
job.start(total=500)                     # state="running"
job.update(current=120, phase="shard 3") # any extra kwargs are stored too
job.done(output="results.csv")           # state="done"
job.failed("CUDA out of memory")         # state="failed"

job.read()      # the raw dict
job.summary()   # human-readable one-liner
```

Standard fields: `job_name`, `state` (idle/running/done/failed), `phase`, `current`/`total`/`unit`, `message`, `error`, `output`, `updated_at`. Anything else you pass is kept as-is.

Because it's just a file, you can also instrument a job in one process and run the bot in another — point both at the same path.

## Push-only mode (no bot thread)

If you only want milestone notifications and no on-demand queries:

```python
from jobwatch import TelegramNotifier

TelegramNotifier().send("Training finished — model.pt saved.")
```

## Configuration

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) (required) |
| `TELEGRAM_CHAT_ID` | Numeric chat id; restricts replies to that chat (optional) |

Both can also be passed explicitly: `TelegramStatusBot(path, bot_token=..., chat_id=...)`.

If no chat id is set, the bot answers the first chat that messages it and ignores others from then on.

## Custom status formatting

Pass your own formatter to control the reply text:

```python
def my_format(status: dict) -> str:
    return f"{status['state']}: {status.get('message') or 'no message'}"

bot = TelegramStatusBot("run_status.json", formatter=my_format)
```

## Debugging

```python
from jobwatch import TelegramNotifier, discover_chat_id

discover_chat_id()           # print chat ids of recent messages to your bot
TelegramNotifier().probe()   # send a test message
```

## How it works

```
your job ──writes──▶ run_status.json ◀──reads── TelegramStatusBot (daemon thread)
                                                        ▲            │
you ── "status" in Telegram ──▶ Telegram API ◀──long-poll┘            │
you ◀───────────────── reply with progress ◀─────────────────────────┘
```

The status file is written atomically (write-to-temp + rename), so reads never see a half-written update. The bot thread is a daemon and dies with your process.

## License

MIT
