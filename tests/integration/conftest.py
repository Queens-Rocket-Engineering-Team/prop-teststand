import logging

import pytest


@pytest.fixture(autouse=True)
def silence_loggers() -> None:
    """Suppress log output from both libqretprop and the mock device during integration tests."""
    for name in ("libqretprop", "qretproptools"):
        log = logging.getLogger(name)
        log.handlers = [logging.NullHandler()]
        log.propagate = False
