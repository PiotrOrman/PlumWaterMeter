"""Fixtures for the Home Assistant tests (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Load custom_components/ewebtel, with the recorder it depends on.

    ``recorder_mock`` comes first: the recorder must be set up before the
    ``hass`` fixture that ``enable_custom_integrations`` needs.
    """
    return
