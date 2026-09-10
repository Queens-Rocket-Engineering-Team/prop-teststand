import logging

import pytest


@pytest.fixture(autouse=True)
def silence_loggers() -> None:
    """Suppress log output from the vector package (server and mock device) during integration tests."""
    log = logging.getLogger("vector")
    log.handlers = [logging.NullHandler()]
    log.propagate = False
