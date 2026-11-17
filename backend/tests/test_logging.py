"""Access-log hardening: query strings never reach the log record."""

import logging

from uvicorn.config import LOGGING_CONFIG

from research_agent.logging import (
    ACCESS_LOGGER,
    REDACTED,
    StripQueryFilter,
    _strip_query,
    build_log_config,
)


def test_strip_query_removes_the_query_string() -> None:
    args = ("127.0.0.1:1234", "GET", "/auth/github/callback?code=abc&state=xyz", "1.1")
    assert _strip_query(args) == ("127.0.0.1:1234", "GET", "/auth/github/callback", "1.1")


def test_strip_query_keeps_a_bare_path() -> None:
    args = ("127.0.0.1:1234", "GET", "/api/v1/threads", "1.1")
    assert _strip_query(args) == args


def test_strip_query_redacts_non_string_targets() -> None:
    args = ("127.0.0.1:1234", "GET", 42, "1.1")
    assert _strip_query(args) == ("127.0.0.1:1234", "GET", REDACTED, "1.1")


def test_strip_query_ignores_other_shapes() -> None:
    assert _strip_query("not a tuple") == "not a tuple"
    assert _strip_query(("only", "two")) == ("only", "two")


def test_filter_keeps_the_record_and_strips_the_query() -> None:
    record = logging.LogRecord(
        name=ACCESS_LOGGER,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", "GET", "/callback?code=secret", "1.1", 200),
        exc_info=None,
    )
    assert StripQueryFilter().filter(record) is True
    assert "code=secret" not in str(record.args)
    assert "/callback" in str(record.args)


def test_filter_never_breaks_on_unexpected_args() -> None:
    record = logging.LogRecord(
        name=ACCESS_LOGGER,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="plain message",
        args=None,
        exc_info=None,
    )
    assert StripQueryFilter().filter(record) is True
    assert record.msg == "plain message"


def test_build_log_config_attaches_the_filter() -> None:
    config = build_log_config()
    assert "strip_query" in config["handlers"]["access"]["filters"]
    assert config["filters"]["strip_query"]["()"] == "research_agent.logging.StripQueryFilter"


def test_build_log_config_does_not_mutate_uvicorns_config() -> None:
    config = build_log_config()
    assert "filters" not in LOGGING_CONFIG["handlers"]["access"]
    assert "filters" not in LOGGING_CONFIG
    assert config is not LOGGING_CONFIG
