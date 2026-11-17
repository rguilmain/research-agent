"""Access-log hardening: keep the access log but never log query strings.

OAuth callbacks carry ``code`` and ``state`` in the query string, and the Wolfram
tool carries ``appid``. The access log keeps method, path and status while the
query is removed before the record is formatted.
"""

import copy
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG

ACCESS_LOGGER = "uvicorn.access"
REDACTED = "<redacted>"


def _strip_query(arguments: Any) -> Any:
    """Replace the URL element of a uvicorn access record's args with its path."""
    if not isinstance(arguments, tuple) or len(arguments) < 3:
        return arguments
    values = list(arguments)
    target = values[2]
    if isinstance(target, str):
        values[2] = target.split("?", 1)[0]
    else:
        values[2] = REDACTED
    return tuple(values)


class StripQueryFilter(logging.Filter):
    """Remove query strings from uvicorn access records; never drop a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.args = _strip_query(record.args)
        except Exception:  # pragma: no cover - defensive: never break logging
            record.args = ()
            record.msg = "access log record could not be sanitized"
        return True


def build_log_config() -> dict[str, Any]:
    """Copy uvicorn's logging config and attach the filter to the access handler.

    Built as a config dict rather than by mutating handlers at runtime, because
    uvicorn applies ``dictConfig`` itself and would discard a pre-attached filter.
    """
    config = copy.deepcopy(LOGGING_CONFIG)
    handler = config["handlers"]["access"]
    filters = list(handler.get("filters", []))
    if "strip_query" not in filters:
        filters.append("strip_query")
    handler["filters"] = filters
    config.setdefault("filters", {})["strip_query"] = {
        "()": "research_agent.logging.StripQueryFilter"
    }
    return config
