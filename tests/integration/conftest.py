import logging

import pytest


@pytest.fixture(autouse=True)
def silence_loggers() -> None:
    """Suppress log output from the prop_teststand package (server and mock device) during integration tests."""
    log = logging.getLogger("prop_teststand")
    log.handlers = [logging.NullHandler()]
    log.propagate = False
