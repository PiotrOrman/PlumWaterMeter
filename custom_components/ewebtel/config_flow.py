"""Config flow of the eWebtel integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from aiohttp import CookieJar
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import EwebtelAuthError, EwebtelClient, EwebtelError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


class EwebtelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for eWebtel."""

    VERSION = 1

    async def _async_validate(self, username: str, password: str) -> dict[str, str]:
        """Log in and list the meters; return form errors (empty when fine)."""
        session = async_create_clientsession(self.hass, auto_cleanup=False, cookie_jar=CookieJar())
        try:
            nodes = await EwebtelClient(session, username, password).async_get_nodes()
        except EwebtelAuthError:
            return {"base": "invalid_auth"}
        except EwebtelError as err:
            _LOGGER.warning("Cannot connect to eWebtel: %s", err)
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error while validating eWebtel login")
            return {"base": "unknown"}
        finally:
            # HA sessions share a connector: detach(), not close().
            session.detach()
        if not nodes:
            return {"base": "no_meters"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the portal login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            errors = await self._async_validate(username, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(
                    title=username,
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the portal rejected the password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            username = entry.data[CONF_USERNAME]
            errors = await self._async_validate(username, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )
