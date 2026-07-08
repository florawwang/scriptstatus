"""jobwatch — monitor long-running local jobs via Telegram.

Your job writes progress to a JSON status file; message "status" to your
Telegram bot from anywhere to see how it's going. No server, no tunnel,
no webhook — the bot long-polls Telegram over outbound HTTPS.

Quick start:

    from jobwatch import JobStatus, TelegramStatusBot

    job = JobStatus("run_status.json", job_name="Training", unit="epochs")

    bot = TelegramStatusBot(job.status_path)  # reads TELEGRAM_BOT_TOKEN
    bot.start()

    job.start(total=100)
    for epoch in range(100):
        ...
        job.update(current=epoch + 1)
    job.done(output="model.pt")
    bot.notify()  # push the final status to your phone
"""

from .bot import TelegramStatusBot
from .status import (
    JobStatus,
    default_status,
    format_status,
    read_status,
    write_status,
)
from .telegram import TelegramNotifier, discover_chat_id

__version__ = "0.2.0"

__all__ = [
    "JobStatus",
    "TelegramNotifier",
    "TelegramStatusBot",
    "default_status",
    "discover_chat_id",
    "format_status",
    "read_status",
    "write_status",
]
