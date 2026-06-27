from __future__ import annotations
import logging

import pytest

from libqretprop.runtime.logging import LOGGER_NAME


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_logging: use the real logging configuration in this test")


@pytest.fixture(autouse=True)
def silence_project_logging(request: pytest.FixtureRequest) -> None:
    """Keep unit tests quiet without requiring runtime log-stream wiring."""
    if request.node.get_closest_marker("real_logging") is not None:
        return

    project_logger = logging.getLogger(LOGGER_NAME)
    project_logger.handlers.clear()
    project_logger.addHandler(logging.NullHandler())
    project_logger.setLevel(logging.DEBUG)
    project_logger.propagate = False
