"""
alerting.py — Email and log alerts for pipeline failures.

Sends an alert when a pipeline run fails.  Supports:
  - Structured log events (always emitted)
  - Email via SMTP (only when ALERT_EMAIL_TO env var is set)

Configuration (environment variables)
--------------------------------------
ALERT_EMAIL_TO      Comma-separated recipient addresses.  Required for email.
ALERT_EMAIL_FROM    Sender address (default: sepa-pipeline@noreply)
SMTP_HOST           SMTP server hostname (default: localhost)
SMTP_PORT           SMTP server port (default: 25)
SMTP_USER           SMTP username (optional)
SMTP_PASSWORD       SMTP password (optional)
SMTP_USE_TLS        Enable STARTTLS: "true" or "1" (default: false)
"""

from __future__ import annotations

import os
import smtplib
from datetime import date
from email.mime.text import MIMEText

import structlog

log = structlog.get_logger(__name__)


def notify_failure(
    run_id: int,
    message: str,
    fecha: date | None = None,
) -> None:
    """
    Emit a structured log alert and, if configured, send an email.

    Parameters
    ----------
    run_id:
        ID of the failed ingestion run.
    message:
        Short description of the failure.
    fecha:
        Date of the data being processed (for context in the alert body).
    """
    log.error(
        "pipeline.alert.failure",
        run_id=run_id,
        fecha=str(fecha) if fecha else None,
        message=message,
    )

    recipients = _get_recipients()
    if not recipients:
        return

    _send_email(run_id, message, fecha, recipients)


# ── Private helpers ────────────────────────────────────────────────────────────


def _get_recipients() -> list[str]:
    raw = os.environ.get("ALERT_EMAIL_TO", "").strip()
    if not raw:
        return []
    return [r.strip() for r in raw.split(",") if r.strip()]


def _send_email(
    run_id: int,
    message: str,
    fecha: date | None,
    recipients: list[str],
) -> None:
    from_addr = os.environ.get("ALERT_EMAIL_FROM", "sepa-pipeline@noreply")
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    use_tls = os.environ.get("SMTP_USE_TLS", "").lower() in ("true", "1")

    subject = f"[SEPA Pipeline] Run {run_id} failed"
    if fecha:
        subject += f" — {fecha}"

    body = (
        f"Pipeline run {run_id} failed.\n\n"
        f"Date: {fecha or 'unknown'}\n"
        f"Error:\n{message}\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if smtp_user and smtp_password:
                smtp.login(smtp_user, smtp_password)
            smtp.sendmail(from_addr, recipients, msg.as_string())
        log.info(
            "pipeline.alert.email_sent",
            run_id=run_id,
            recipients=recipients,
        )
    except Exception as exc:
        log.error(
            "pipeline.alert.email_failed",
            run_id=run_id,
            error=str(exc),
        )
