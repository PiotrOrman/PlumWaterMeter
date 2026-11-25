"""The eWebtel water meter integration."""

from __future__ import annotations

from aiohttp import CookieJar
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import EwebtelClient
from .coordinator import EwebtelConfigEntry, EwebtelCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: EwebtelConfigEntry) -> bool:
    """Set up eWebtel from a config entry."""
    # Own cookie jar: the portal's auth cookie belongs to this account only.
    # A session created during entry setup is closed when the entry unloads.
    session = async_create_clientsession(hass, cookie_jar=CookieJar())
    client = EwebtelClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = EwebtelCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EwebtelConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
