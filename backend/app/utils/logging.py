"""Structured, secret-safe logging.

Output format (human mode):

    12:04:31.221 INFO  [ROUTER] selected_bot=dost reason=explicit_address conf=0.99

or, with LOG_JSON=true, one JSON object per line for log shipping.

Secrets are never passed through: ``redact`` masks anything that looks like a
key, and the config module is the only place that reads credentials.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

_SECRET_HINTS = ("key", "secret", "token", "password", "authorization", "api_key")

# Stage tags used across the pipeline. Kept in one place so log greps are stable.
TAG_ROOM = "ROOM"
TAG_STT = "STT"
TAG_ROUTER = "ROUTER"
TAG_CONTEXT = "CONTEXT"
TAG_LLM = "LLM"
TAG_TTS = "TTS"
TAG_PUBLISH = "PUBLISH"
TAG_INTERRUPT = "INTERRUPTION"
TAG_TURN = "TURN"
TAG_CHAT = "CHAT"
TAG_AGENT = "AGENT"
TAG_LATENCY = "LATENCY"
TAG_API = "API"
TAG_MEMORY = "MEMORY"


def redact(value: Any, *, keep: int = 4) -> str:
    """Mask a secret, keeping a short suffix so operators can tell keys apart."""
    text = str(value or "")
    if not text:
        return "<unset>"
    if len(text) <= keep:
        return "*" * len(text)
    return f"{'*' * 8}{text[-keep:]}"


def _scrub(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in fields.items():
        lowered = key.lower()
        if any(hint in lowered for hint in _SECRET_HINTS):
            out[key] = redact(val)
        else:
            out[key] = val
    return out


def _fmt_value(val: Any) -> str:
    if val is None:
        return "-"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float):
        return f"{val:.1f}"
    text = str(val)
    if " " in text or "=" in text:
        return json.dumps(text, ensure_ascii=False)
    return text


class _HumanFormatter(logging.Formatter):
    default_time_format = "%H:%M:%S"
    default_msec_format = "%s.%03d"

    def format(self, record: logging.LogRecord) -> str:
        tag = getattr(record, "tag", None)
        fields: dict[str, Any] = getattr(record, "fields", {}) or {}
        head = f"{self.formatTime(record)} {record.levelname:<5}"
        if tag:
            head += f" [{tag}]"
        parts = [head]
        if record.getMessage():
            parts.append(record.getMessage())
        if fields:
            parts.append(" ".join(f"{k}={_fmt_value(v)}" for k, v in fields.items()))
        line = " ".join(p for p in parts if p)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "tag": getattr(record, "tag", None),
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class StageLogger:
    """Thin wrapper that emits ``[TAG] k=v`` lines.

    Usage::

        log = get_logger(__name__)
        log.stage(TAG_ROUTER, selected_bot="dost", reason="explicit_address")
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def stage(self, tag: str, level: int = logging.INFO, /, **fields: Any) -> None:
        self._log.log(level, "", extra={"tag": tag, "fields": _scrub(fields)})

    def debug(self, msg: str = "", tag: str | None = None, **fields: Any) -> None:
        self._log.debug(msg, extra={"tag": tag, "fields": _scrub(fields)})

    def info(self, msg: str = "", tag: str | None = None, **fields: Any) -> None:
        self._log.info(msg, extra={"tag": tag, "fields": _scrub(fields)})

    def warning(self, msg: str = "", tag: str | None = None, **fields: Any) -> None:
        self._log.warning(msg, extra={"tag": tag, "fields": _scrub(fields)})

    def error(self, msg: str = "", tag: str | None = None, exc: bool = False, **fields: Any) -> None:
        self._log.error(msg, exc_info=exc, extra={"tag": tag, "fields": _scrub(fields)})

    def exception(self, msg: str = "", tag: str | None = None, **fields: Any) -> None:
        self._log.error(msg, exc_info=True, extra={"tag": tag, "fields": _scrub(fields)})


_configured = False


def configure_logging(level: str | None = None, json_mode: bool | None = None) -> None:
    """Install the root handler. Idempotent, so agents and API can both call it."""
    global _configured
    if _configured:
        return
    lvl = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    use_json = json_mode if json_mode is not None else os.getenv("LOG_JSON", "").lower() == "true"

    # Windows consoles default to cp1252, which cannot encode Devanagari.
    # Transcripts are logged verbatim, so force UTF-8 rather than lose them.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if use_json else _HumanFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, lvl, logging.INFO))

    # Third-party chatter that drowns out pipeline stages.
    for noisy in ("httpx", "httpcore", "websockets", "aiohttp", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("livekit").setLevel(logging.INFO)
    _configured = True


def get_logger(name: str) -> StageLogger:
    configure_logging()
    return StageLogger(logging.getLogger(name))
