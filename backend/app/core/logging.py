"""JSON logs on stdout, without request addresses that carry tenant detail.

At INFO, the HTTP client used for Microsoft Graph (``httpx``) and the Azure SDK
log every request's full address: group and user ids, ``$filter`` queries.
Those loggers are held at WARNING unless ``LOG_LEVEL=DEBUG``. Uvicorn's
access log keeps the path but drops the query string (searches, caller
names, place ids). Rotation is the container runtime's job (``logging:`` in
docker/docker-compose.yml).
"""

import logging

from pythonjsonlogger import jsonlogger

from app.core.config import settings

# Log every request address at INFO; quiet unless debugging.
CHATTY = ("httpx", "httpcore", "azure", "msal", "urllib3")


class DropQueryString(logging.Filter):
    """Uvicorn access lines without ``?…``: ``(client, method, path, version, status)``."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (args[0], args[1], args[2].split("?", 1)[0], args[3], args[4])
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    level = settings.log_level.upper()
    root.setLevel(level)
    if level != "DEBUG":
        for name in CHATTY:
            logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, DropQueryString) for f in access.filters):
        access.addFilter(DropQueryString())
