"""Logs carry no request addresses with tenant detail (core/logging.py)."""

import logging

import pytest

from app.core import logging as app_logging
from app.core.config import settings


def _access_line(path: str) -> str:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:0", "GET", path, "1.1", 200),
        None,
    )
    app_logging.DropQueryString().filter(record)
    return record.getMessage()


def test_the_access_log_keeps_the_path_but_not_the_query():
    assert _access_line("/api/activity/events?caller=ada%40example.org&limit=50") == (
        '10.0.0.1:0 - "GET /api/activity/events HTTP/1.1" 200'
    )
    assert _access_line("/api/health") == '10.0.0.1:0 - "GET /api/health HTTP/1.1" 200'


@pytest.mark.parametrize(("level", "quiet"), [("INFO", True), ("DEBUG", False)])
def test_request_address_loggers_are_quiet_unless_debugging(monkeypatch, level, quiet):
    monkeypatch.setattr(settings, "log_level", level)
    root = logging.getLogger()
    saved = (
        root.handlers[:],
        root.level,
        {n: logging.getLogger(n).level for n in app_logging.CHATTY},
    )
    try:
        for n in app_logging.CHATTY:
            logging.getLogger(n).setLevel(logging.NOTSET)
        app_logging.configure_logging()
        graph = logging.getLogger("httpx")
        sdk = logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
        assert graph.isEnabledFor(logging.INFO) is not quiet
        assert sdk.isEnabledFor(logging.INFO) is not quiet
        assert logging.getLogger("app.tools.activity").isEnabledFor(logging.INFO)
        assert (
            sum(
                isinstance(f, app_logging.DropQueryString)
                for f in logging.getLogger("uvicorn.access").filters
            )
            == 1
        )
    finally:
        root.handlers, lvl, levels = saved
        root.setLevel(lvl)
        for n, v in levels.items():
            logging.getLogger(n).setLevel(v)
