"""Config flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewebtel.api import (
    EwebtelAuthError,
    EwebtelConnectionError,
    Node,
)
from custom_components.ewebtel.const import DOMAIN

NODES = [Node(node_id=42, name="12345", meter_sn="SN1", address="Street 1")]
GET_NODES = "custom_components.ewebtel.api.EwebtelClient.async_get_nodes"


@pytest.fixture
def mock_setup_entry():
    with patch("custom_components.ewebtel.async_setup_entry", return_value=True) as mock:
        yield mock


async def test_user_flow(hass: HomeAssistant, mock_setup_entry) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch(GET_NODES, AsyncMock(return_value=NODES)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: " User@Example.com ", CONF_PASSWORD: "pw"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "User@Example.com"
    assert result["data"] == {CONF_USERNAME: "User@Example.com", CONF_PASSWORD: "pw"}
    assert result["result"].unique_id == "user@example.com"


@pytest.mark.parametrize(
    ("side_effect", "nodes", "error"),
    [
        (EwebtelAuthError("no"), None, "invalid_auth"),
        (EwebtelConnectionError("down"), None, "cannot_connect"),
        (RuntimeError("boom"), None, "unknown"),
        (None, [], "no_meters"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_setup_entry, side_effect, nodes, error
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(GET_NODES, AsyncMock(side_effect=side_effect, return_value=nodes)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # The form recovers once the portal accepts the login.
    with patch(GET_NODES, AsyncMock(return_value=NODES)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(hass: HomeAssistant, mock_setup_entry) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="u@example.com",
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "pw"},
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "U@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_setup_entry) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u@example.com",
        data={CONF_USERNAME: "u@example.com", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(GET_NODES, AsyncMock(side_effect=EwebtelAuthError("no"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "still-wrong"}
        )
    assert result["errors"] == {"base": "invalid_auth"}
    with patch(GET_NODES, AsyncMock(return_value=NODES)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
