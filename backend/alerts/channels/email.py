"""Email channel — SMTP via system-wide alerting_smtp_* settings.

The channel config holds the recipient list (``to``).  SMTP
credentials are read from the ``Setting`` table at send time:

  - alerting_smtp_host
  - alerting_smtp_port
  - alerting_smtp_user
  - alerting_smtp_pass
  - alerting_smtp_from
  - alerting_smtp_use_tls
  - alerting_enabled        (master switch)

If ``alerting_enabled`` is ``"false"`` or the host is empty, the
channel fails fast with a clear error so the dispatcher records it
in ``alert_events.last_error``.

SMTP I/O is synchronous in stdlib; we offload it to a thread via
``asyncio.to_thread`` so the dispatcher loop stays unblocked.
"""

import asyncio
import json
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from sqlalchemy.orm import Session

from ... import database, models
from ...logging_config import vulture_logger as logger


@dataclass
class EmailResult:
    success: bool
    error: Optional[str] = None


def _read_smtp_settings(db: Session) -> dict:
    """Pull all alerting_smtp_* keys from the Setting table in one query."""
    rows = (
        db.query(models.Setting)
        .filter(models.Setting.key.in_([
            "alerting_enabled",
            "alerting_smtp_host",
            "alerting_smtp_port",
            "alerting_smtp_user",
            "alerting_smtp_pass",
            "alerting_smtp_from",
            "alerting_smtp_use_tls",
        ]))
        .all()
    )
    out = {r.key: r.value for r in rows}
    return out


def _send_sync(host: str, port: int, user: str, password: str,
               from_addr: str, to_addrs: list, subject: str, body: str,
               use_tls: bool) -> None:
    """Blocking SMTP send.  Raises on transport or auth error."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)
    # The HTML alternative carries the same JSON so the operator can
    # paste it into a runbook without retyping.  Conservative default:
    # no HTML body — receivers see plain text.
    if use_tls:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)


async def deliver(config: dict, payload: dict) -> EmailResult:
    """Send ``payload`` as a plain-text email to the channel's recipients.

    The ``config`` is the channel's stored config (recipients + optional
    subject prefix).  SMTP creds are pulled from the Setting table.
    """
    recipients = (config or {}).get("to") or []
    if not recipients:
        return EmailResult(success=False, error="email channel has no recipients")

    # Offload the DB read to a worker thread so the dispatcher's
    # event loop isn't blocked by the synchronous SQLAlchemy call.
    # The SMTP send itself is already offloaded further down; this
    # just makes the read consistent with that pattern.
    db = database.SessionLocal()
    try:
        settings = await asyncio.to_thread(_read_smtp_settings, db)
    finally:
        db.close()

    # ``settings.get`` returns the stored value verbatim, so a row
    # present with value="" (an operator clearing the field) was
    # tripping the default branch and silently disabling all email
    # alerts. Normalize: only the literal "true" enables.
    enabled_raw = settings.get("alerting_enabled", "false")
    if not enabled_raw or str(enabled_raw).strip().lower() != "true":
        return EmailResult(success=False, error="alerting disabled (alerting_enabled=false)")
    host = settings.get("alerting_smtp_host", "")
    if not host:
        return EmailResult(success=False, error="alerting_smtp_host not configured")
    try:
        port = int(settings.get("alerting_smtp_port", "587"))
    except (TypeError, ValueError):
        port = 587
    user = settings.get("alerting_smtp_user", "")
    password = settings.get("alerting_smtp_pass", "")
    use_tls = settings.get("alerting_smtp_use_tls", "true").lower() == "true"

    # Per-channel overrides.  The global SMTP ``from`` is the default;
    # if the channel config specifies ``from_addr``, that wins.  This
    # lets one operator manage alerts from two brands (different
    # sender domains) through the same SMTP server.
    from_addr = (config or {}).get("from_addr") or settings.get(
        "alerting_smtp_from", user
    )
    if not from_addr:
        return EmailResult(success=False, error="alerting_smtp_from not configured")
    subject_prefix = (config or {}).get("subject_prefix") or "ParkVIS alert"
    rule_name = payload.get("rule_name", "alert")
    subject = f"{subject_prefix}: {rule_name}"
    body = json.dumps(payload, indent=2, default=str)

    try:
        await asyncio.to_thread(
            _send_sync, host, port, user, password,
            from_addr, recipients, subject, body, use_tls,
        )
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("Email delivery failed: {err}", err=exc)
        return EmailResult(success=False, error=f"smtp: {exc!s}")
    except Exception as exc:  # noqa: BLE001
        logger.error("Email delivery raised: {err}", err=exc)
        return EmailResult(success=False, error=f"unexpected: {exc!s}")

    return EmailResult(success=True)
