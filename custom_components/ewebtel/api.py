"""Client for the eWebtel (WebTel by Plum Sp. z o.o.) customer portal.

The portal has no public API. This client uses the same endpoints as the
portal's own web pages:

* ``GET  /Account/Login`` + ``POST /Account/Login``: form login (ASP.NET MVC,
  anti-forgery token, auth cookie valid for 24 h).
* ``GET  /Node/GetGuestNodeList``: the measuring points of the account (JSON).
* ``GET  /Node/Details/{id}``: HTML page that embeds the chart model
  (``graphModelPrimary``) with the device id and series parameters.
* ``POST /Graph/GetNodeGraphData``: chart data (JSON). With
  ``CalculationMethod = 0`` the hourly series holds the absolute meter reading.

This module does not import Home Assistant, so it can be tested on its own.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any

import aiohttp
from yarl import URL

from .processing import RawReading

_LOGGER = logging.getLogger(__name__)

BASE_URL = URL("https://ewebtel.com")
LOGIN_PATH = "/Account/Login"
NODE_LIST_PATH = "/Node/GetGuestNodeList"
NODE_DETAILS_PATH = "/Node/Details/{node_id}"
GRAPH_DATA_PATH = "/Graph/GetNodeGraphData"

# CalculationMethod 0 returns the meter reading itself; 1 and 2 return
# per-hour differences.
CALCULATION_METHOD_READING = 0
# The portal accepts DisplayPeriod (days) up to at least 365.
MAX_WINDOW_DAYS = 365
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)


class EwebtelError(Exception):
    """Base error of the eWebtel client."""


class EwebtelConnectionError(EwebtelError):
    """The portal could not be reached or answered with an error."""


class EwebtelAuthError(EwebtelError):
    """The portal rejected the credentials."""


class EwebtelSessionExpired(EwebtelError):
    """The portal redirected a data request to the login page."""


@dataclass(frozen=True, slots=True)
class Node:
    """A measuring point (one water meter) of the account."""

    node_id: int
    name: str
    meter_sn: str
    address: str


class _FormParser(HTMLParser):
    """Collect the input fields of the login form."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}
        self._in_form = False
        self.found_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "form" and LOGIN_PATH.lower() in attributes.get("action", "").lower():
            self._in_form = True
            self.found_form = True
        elif (
            tag == "input"
            and self._in_form
            and attributes.get("name")
            and attributes.get("type", "text").lower() not in ("submit", "button")
        ):
            self.fields[attributes["name"]] = attributes.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False


def parse_login_form(html: str) -> dict[str, str]:
    """Return the fields of the login form (hidden token included)."""
    parser = _FormParser()
    parser.feed(html)
    if not parser.found_form or "__RequestVerificationToken" not in parser.fields:
        raise EwebtelConnectionError("Login form not found on the eWebtel login page")
    return parser.fields


def parse_graph_model(html: str) -> dict[str, Any]:
    """Extract the ``graphModelPrimary`` JSON object from a node details page."""
    marker = "graphModelPrimary ="
    index = html.find(marker)
    if index < 0:
        raise EwebtelConnectionError("Chart model not found on the node details page")
    start = html.find("{", index)
    if start < 0:
        raise EwebtelConnectionError("Chart model not found on the node details page")
    try:
        model, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError as err:
        raise EwebtelConnectionError(f"Cannot parse the chart model: {err}") from err
    if not isinstance(model, dict) or not model.get("GraphSeries"):
        raise EwebtelConnectionError("The chart model has no data series")
    return model


def parse_nodes(payload: dict[str, Any]) -> list[Node]:
    """Turn the ``GetGuestNodeList`` answer into nodes."""
    rows = payload.get("aaData")
    if not isinstance(rows, list):
        raise EwebtelConnectionError("Unexpected answer from the node list")
    try:
        return [
            Node(
                node_id=int(row["Id"]),
                name=str(row.get("Name") or row["Id"]).strip(),
                meter_sn=str(row.get("MeterSN") or "").strip(),
                address=str(row.get("Adress") or "").strip(),
            )
            for row in rows
        ]
    except (AttributeError, KeyError, TypeError, ValueError) as err:
        raise EwebtelConnectionError(f"Unexpected row in the node list: {err!r}") from err


def parse_graph_readings(payload: dict[str, Any]) -> list[RawReading]:
    """Turn a ``GetNodeGraphData`` answer into raw readings."""
    if not payload.get("Success") or payload.get("IsApplicationError"):
        detail = payload.get("Data") if isinstance(payload.get("Data"), str) else ""
        raise EwebtelConnectionError(f"The portal returned an error {detail}".strip())
    data = payload.get("Data") or {}
    series = data.get("GraphSeries") or []
    if not series:
        return []
    readings: list[RawReading] = []
    for point in series[0].get("Data") or []:
        values = point.get("V") or []
        if not values or values[0] in (None, ""):
            continue
        try:
            value = float(values[0])
            moment = datetime.fromisoformat(point["D"])
        except (KeyError, TypeError, ValueError):
            _LOGGER.debug("Skipping a malformed point: %s", point)
            continue
        readings.append(RawReading(local_time=moment, value=value, status=int(point.get("S") or 0)))
    return readings


def _is_login_page(response: aiohttp.ClientResponse) -> bool:
    return response.url.path.lower().startswith(LOGIN_PATH.lower())


def _raise_if_maintenance(login_fields: dict[str, str]) -> None:
    """Raise if the login page announces a portal break (maintenance)."""
    if login_fields.get("isBreakMsgEnabled", "").lower() == "true":
        message = login_fields.get("BreakMessage") or "no details"
        raise EwebtelConnectionError(f"eWebtel announces a break: {message}")


class EwebtelClient:
    """Async client for one eWebtel account.

    The session must have its own cookie jar: the login cookie belongs to
    this account only.
    """

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._logged_in = False

    async def login(self) -> None:
        """Log in and keep the auth cookie in the session's cookie jar."""
        self._session.cookie_jar.clear()
        self._logged_in = False
        try:
            async with self._session.get(
                BASE_URL / LOGIN_PATH.lstrip("/"), timeout=REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                fields = parse_login_form(await response.text())
            _raise_if_maintenance(fields)
            fields["UserName"] = self._username
            fields["Password"] = self._password
            async with self._session.post(
                BASE_URL / LOGIN_PATH.lstrip("/"), data=fields, timeout=REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                if _is_login_page(response):
                    if any(not _is_login_page(step) for step in response.history):
                        # The portal accepted the login (302 to another page)
                        # but that page sent us back to the login page: a
                        # portal problem, not the password. A failed login
                        # stays on login URLs, even with Post-Redirect-Get.
                        raise EwebtelConnectionError(
                            "eWebtel accepted the login but sent the login page again"
                        )
                    try:
                        page_fields = parse_login_form(await response.text())
                    except EwebtelConnectionError:
                        page_fields = {}
                    _raise_if_maintenance(page_fields)
                    raise EwebtelAuthError("eWebtel rejected the user name or password")
        except aiohttp.ClientError as err:
            raise EwebtelConnectionError(f"Cannot reach eWebtel: {err}") from err
        except TimeoutError as err:
            raise EwebtelConnectionError("eWebtel did not answer in time") from err
        self._logged_in = True

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send a data request; log in again once if the session has expired."""
        for attempt in (1, 2):
            if not self._logged_in:
                await self.login()
            try:
                return await self._send_json(method, path, **kwargs)
            except EwebtelSessionExpired:
                self._logged_in = False
                if attempt == 2:
                    raise EwebtelConnectionError(
                        "eWebtel sent the login page again right after a successful login"
                    ) from None
                _LOGGER.debug("eWebtel session expired, logging in again")
        raise AssertionError("unreachable")

    async def _send_json(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with self._session.request(
                method, BASE_URL / path.lstrip("/"), timeout=REQUEST_TIMEOUT, **kwargs
            ) as response:
                if _is_login_page(response) or response.status in (401, 403):
                    raise EwebtelSessionExpired
                response.raise_for_status()
                if "json" not in response.content_type:
                    raise EwebtelSessionExpired
                return await response.json()
        except aiohttp.ClientError as err:
            raise EwebtelConnectionError(f"eWebtel request failed: {err}") from err
        except TimeoutError as err:
            raise EwebtelConnectionError("eWebtel did not answer in time") from err

    async def _request_text(self, path: str) -> str:
        for attempt in (1, 2):
            if not self._logged_in:
                await self.login()
            try:
                async with self._session.get(
                    BASE_URL / path.lstrip("/"), timeout=REQUEST_TIMEOUT
                ) as response:
                    if _is_login_page(response):
                        self._logged_in = False
                        if attempt == 2:
                            raise EwebtelConnectionError(
                                "eWebtel sent the login page again right after a successful login"
                            )
                        continue
                    response.raise_for_status()
                    return await response.text()
            except aiohttp.ClientError as err:
                raise EwebtelConnectionError(f"eWebtel request failed: {err}") from err
            except TimeoutError as err:
                raise EwebtelConnectionError("eWebtel did not answer in time") from err
        raise AssertionError("unreachable")

    async def async_get_nodes(self) -> list[Node]:
        """Return the measuring points of the account."""
        params = {
            "sEcho": "1",
            "iColumns": "5",
            "sColumns": ",,,,",
            "iDisplayStart": "0",
            "iDisplayLength": "100",
            "mDataProp_0": "Id",
            "bSortable_0": "false",
            "mDataProp_1": "Name",
            "bSortable_1": "true",
            "mDataProp_2": "Adress",
            "bSortable_2": "false",
            "mDataProp_3": "MeterSN",
            "bSortable_3": "true",
            "mDataProp_4": "Id",
            "bSortable_4": "false",
            "iSortCol_0": "0",
            "sSortDir_0": "asc",
            "iSortingCols": "1",
            "SearchText": "",
        }
        payload = await self._request_json("GET", NODE_LIST_PATH, params=params)
        return parse_nodes(payload)

    async def async_get_graph_model(self, node_id: int) -> dict[str, Any]:
        """Return the chart model of a node (needed for data requests)."""
        html = await self._request_text(NODE_DETAILS_PATH.format(node_id=node_id))
        return parse_graph_model(html)

    async def async_get_readings(
        self, model: dict[str, Any], start: datetime, end: datetime
    ) -> list[RawReading]:
        """Return hourly meter readings between two local (portal) times.

        ``start`` and ``end`` are naive local times of the portal. The range
        is fetched in windows of at most ``MAX_WINDOW_DAYS`` days.
        """
        readings: list[RawReading] = []
        window_start = start
        while window_start < end:
            days = min(MAX_WINDOW_DAYS, (end - window_start).days + 1)
            request = copy.deepcopy(model)
            request["GraphSeries"][0]["CalculationMethod"] = CALCULATION_METHOD_READING
            request["SelectedDateStr"] = ""
            request["SelectedDate"] = window_start.replace(microsecond=0).isoformat()
            request["DisplayPeriod"] = days
            payload = await self._request_json("POST", GRAPH_DATA_PATH, json=request)
            readings.extend(parse_graph_readings(payload))
            window_start += timedelta(days=days)
        return readings
