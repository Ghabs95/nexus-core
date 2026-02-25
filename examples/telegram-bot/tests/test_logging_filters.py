import logging

from nexus.core.utils.logging_filters import SecretRedactingFilter


def test_secret_redacting_filter_redacts_message_and_args():
    filt = SecretRedactingFilter(["token-123", "abc"])
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="using token token-123",
        args=("abc", {"nested": "token-123"}),
        exc_info=None,
    )

    ok = filt.filter(record)

    assert ok is True
    assert "token-123" not in str(record.msg)
    assert "abc" not in str(record.args)
    assert "[REDACTED_SECRET]" in str(record.msg)
