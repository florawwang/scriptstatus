"""Generic JSON status file for long-running jobs.

A job writes its progress to a small JSON file; anything else (a webhook
server, a CLI, another process) reads it back and formats it for humans.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

# Known top-level fields. Anything else passed to write_status is kept as-is,
# so jobs can attach arbitrary metadata.
_BASE_FIELDS = {
    "job_name": None,
    "state": "idle",  # idle | running | done | failed
    "phase": None,  # optional label for the current stage, e.g. "epoch 3"
    "current": None,  # progress numerator
    "total": None,  # progress denominator
    "unit": "steps",  # what current/total count, e.g. "photos", "epochs"
    "message": None,  # free-form human-readable note
    "error": None,
    "output": None,  # where results were saved
    "updated_at": None,
}

StatusFormatter = Callable[[dict[str, Any]], str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_status() -> dict[str, Any]:
    status = dict(_BASE_FIELDS)
    status["updated_at"] = _utc_now()
    return status


def read_status(status_path: str) -> dict[str, Any]:
    """Read the status file, falling back to an idle default."""
    if not os.path.isfile(status_path):
        return default_status()
    try:
        with open(status_path, encoding="utf-8") as f:
            data = default_status()
            data.update(json.load(f))
            return data
    except (json.JSONDecodeError, OSError):
        return default_status()


def write_status(status_path: str, **fields: Any) -> dict[str, Any]:
    """Merge fields into the status file (atomic write). Returns new status."""
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
    return status


def format_status(status: dict[str, Any]) -> str:
    """Default human-readable one-liner for a status dict."""
    name = status.get("job_name") or "Job"
    state = status.get("state", "idle")

    if state == "done":
        out = status.get("output")
        suffix = f" Saved to {out}" if out else ""
        return f"Done! {name} finished.{suffix}"

    if state == "failed":
        err = status.get("error") or "unknown error"
        return f"{name} failed: {err}"

    if state == "running":
        parts: list[str] = []
        phase = status.get("phase")
        if phase:
            parts.append(str(phase))
        current = status.get("current")
        total = status.get("total")
        if current is not None and total:
            unit = status.get("unit") or "steps"
            parts.append(f"{int(current)}/{int(total)} {unit}")
        message = status.get("message")
        if message:
            parts.append(str(message))
        if parts:
            return f"{name}: " + " — ".join(parts)
        return f"{name} is running (no progress reported yet)."

    return f"No {name.lower()} running right now."


class JobStatus:
    """Convenience wrapper: one status file, ergonomic update methods.

    Example:
        job = JobStatus("run_status.json", job_name="Inference", unit="photos")
        job.start(total=500)
        job.update(current=120, phase="mosquito 3/12")
        job.done(output="results.csv")
    """

    def __init__(self, status_path: str, *, job_name: str = "Job", unit: str = "steps"):
        self.status_path = status_path
        self.job_name = job_name
        self.unit = unit

    def start(self, **fields: Any) -> dict[str, Any]:
        return self.update(state="running", error=None, **fields)

    def update(self, **fields: Any) -> dict[str, Any]:
        fields.setdefault("job_name", self.job_name)
        fields.setdefault("unit", self.unit)
        return write_status(self.status_path, **fields)

    def done(self, **fields: Any) -> dict[str, Any]:
        return self.update(state="done", **fields)

    def failed(self, error: str, **fields: Any) -> dict[str, Any]:
        return self.update(state="failed", error=error, **fields)

    def read(self) -> dict[str, Any]:
        return read_status(self.status_path)

    def summary(self) -> str:
        return format_status(self.read())
