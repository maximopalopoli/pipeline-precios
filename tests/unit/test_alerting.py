from etl import alerting


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout):
        self.address = (host, port)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def sendmail(self, from_addr, recipients, message):
        _FakeSMTP.sent.append((self.address, from_addr, recipients))


def test_blank_smtp_settings_keep_their_defaults(monkeypatch):
    """Docker Compose passes unset settings as blank strings."""
    for name in ("ALERT_EMAIL_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
                 "SMTP_PASSWORD", "SMTP_USE_TLS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.test")
    monkeypatch.setattr(alerting.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []

    alerting.notify_failure(7, "boom")

    assert _FakeSMTP.sent == [
        (("localhost", 25), "sepa-pipeline@noreply", ["ops@example.test"])
    ]
