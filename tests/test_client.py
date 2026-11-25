"""Tests of EwebtelClient against a fake portal served by aiohttp.

The fake portal behaves like ewebtel.com as observed: form login with an
anti-forgery token, a 302 to ``/`` on success, the login page again on
failure, and a 302 to the login page for data requests without a session.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timedelta

import pytest
from aiohttp import ClientSession, CookieJar, web
from aiohttp.test_utils import TestServer
from conftest import api
from yarl import URL

USER = "user@example.com"
PASSWORD = "secret"

MODEL = {
    "DeviceId": 1,
    "GraphSeries": [{"NodeGuid": "g", "ParameterDpIndex": 100, "CalculationMethod": 2}],
    "DisplayPeriod": 7,
    "SelectedDate": None,
    "SelectedDateStr": "18/11/2026 10:16",
}


class FakePortal:
    """Minimal stand-in for the portal."""

    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.logins = 0
        self.graph_requests: list[dict] = []
        self.maintenance = False
        self.break_enabled = "False"
        self.break_message = ""
        self.prg_on_failure = False
        self.app = web.Application()
        self.app.router.add_get("/Account/Login", self.login_page)
        self.app.router.add_post("/Account/Login", self.login_post)
        self.app.router.add_get("/", self.home)
        self.app.router.add_get("/Node/GetGuestNodeList", self.node_list)
        self.app.router.add_get("/Node/Details/{node_id}", self.details)
        self.app.router.add_post("/Graph/GetNodeGraphData", self.graph)

    def expire_sessions(self) -> None:
        self.sessions.clear()

    async def login_page(self, request: web.Request) -> web.Response:
        token = request.cookies.get("__RequestVerificationToken") or secrets.token_hex(8)
        response = web.Response(
            text=(
                '<form action="/Account/Login" method="post">'
                f'<input name="__RequestVerificationToken" type="hidden" value="{token}-form" />'
                f'<input name="isBreakMsgEnabled" type="hidden" value="{self.break_enabled}" />'
                f'<input name="BreakMessage" type="hidden" value="{self.break_message}" />'
                '<input name="UserName" type="text" value="" />'
                '<input name="Password" type="password" /></form>'
            ),
            content_type="text/html",
        )
        response.set_cookie("__RequestVerificationToken", token)
        return response

    async def login_post(self, request: web.Request) -> web.StreamResponse:
        form = await request.post()
        cookie_token = request.cookies.get("__RequestVerificationToken")
        if (
            form.get("UserName") == USER
            and form.get("Password") == PASSWORD
            and form.get("__RequestVerificationToken") == f"{cookie_token}-form"
            and form.get("isBreakMsgEnabled") == "False"
        ):
            self.logins += 1
            auth = secrets.token_hex(8)
            self.sessions.add(auth)
            response = web.HTTPFound("/")
            response.set_cookie(".ASPXAUTH", auth)
            raise response
        if self.prg_on_failure:
            raise web.HTTPFound("/Account/Login")
        return await self.login_page(request)

    def _authorized(self, request: web.Request) -> None:
        if self.maintenance or request.cookies.get(".ASPXAUTH") not in self.sessions:
            raise web.HTTPFound(f"/Account/Login?ReturnUrl={request.path}")

    async def home(self, request: web.Request) -> web.Response:
        self._authorized(request)
        return web.Response(text="<html>home</html>", content_type="text/html")

    async def node_list(self, request: web.Request) -> web.Response:
        self._authorized(request)
        return web.json_response(
            {"aaData": [{"Id": 42, "Name": "12345", "MeterSN": "SN1", "Adress": "Street 1"}]}
        )

    async def details(self, request: web.Request) -> web.Response:
        self._authorized(request)
        return web.Response(
            text=f"<script>graphModelPrimary = {json.dumps(MODEL)};</script>",
            content_type="text/html",
        )

    async def graph(self, request: web.Request) -> web.Response:
        self._authorized(request)
        body = await request.json()
        self.graph_requests.append(body)
        start = datetime.fromisoformat(body["SelectedDate"])
        points = [
            {"D": (start + timedelta(hours=h)).isoformat(), "V": [f"{100 + h / 1000:.3f}"], "S": 8}
            for h in range(3)
        ]
        return web.json_response(
            {
                "Success": True,
                "IsApplicationError": False,
                "Data": {"GraphSeries": [{"Data": points}]},
            }
        )


async def _with_client(test, password: str = PASSWORD):
    portal = FakePortal()
    server = TestServer(portal.app)
    await server.start_server()
    original = api.BASE_URL
    api.BASE_URL = URL(str(server.make_url("/")))
    try:
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as session:
            client = api.EwebtelClient(session, USER, password)
            await test(client, portal)
    finally:
        api.BASE_URL = original
        await server.close()


def run(test, password: str = PASSWORD) -> None:
    asyncio.run(_with_client(test, password))


def test_login_and_nodes():
    async def test(client, portal):
        nodes = await client.async_get_nodes()
        assert nodes == [api.Node(42, "12345", "SN1", "Street 1")]
        assert portal.logins == 1

    run(test)


def test_wrong_password():
    async def test(client, portal):
        with pytest.raises(api.EwebtelAuthError):
            await client.async_get_nodes()

    run(test, password="wrong")


def test_expired_session_logs_in_again():
    async def test(client, portal):
        await client.async_get_nodes()
        portal.expire_sessions()
        model = await client.async_get_graph_model(42)
        assert model == MODEL
        portal.expire_sessions()
        readings = await client.async_get_readings(
            model, datetime(2026, 11, 24), datetime(2026, 11, 25)
        )
        assert len(readings) == 3
        assert portal.logins == 3

    run(test)


def test_readings_are_fetched_in_windows():
    async def test(client, portal):
        start = datetime(2025, 10, 1)
        await client.async_get_readings(MODEL, start, start + timedelta(days=400))
        sent = [(r["SelectedDate"], r["DisplayPeriod"]) for r in portal.graph_requests]
        assert sent == [("2025-10-01T00:00:00", 365), ("2026-10-01T00:00:00", 36)]
        for request in portal.graph_requests:
            assert request["GraphSeries"][0]["CalculationMethod"] == 0
            assert request["SelectedDateStr"] == ""
        # The caller's model is not changed.
        assert MODEL["GraphSeries"][0]["CalculationMethod"] == 2

    run(test)


def test_login_page_after_fresh_login_is_not_an_auth_error():
    async def test(client, portal):
        await client.async_get_nodes()
        portal.maintenance = True
        with pytest.raises(api.EwebtelConnectionError):
            await client.async_get_nodes()
        with pytest.raises(api.EwebtelConnectionError):
            await client.async_get_graph_model(42)

    run(test)


def test_announced_break_is_not_an_auth_error():
    async def test(client, portal):
        portal.break_enabled = "True"
        portal.break_message = "Prace serwisowe"
        with pytest.raises(api.EwebtelConnectionError, match="Prace serwisowe"):
            await client.async_get_nodes()

    run(test)


def test_wrong_password_with_post_redirect_get():
    async def test(client, portal):
        portal.prg_on_failure = True
        with pytest.raises(api.EwebtelAuthError):
            await client.async_get_nodes()

    run(test, password="wrong")


def test_accepted_login_bounced_back_is_not_an_auth_error():
    async def test(client, portal):
        portal.maintenance = True  # "/" sends the fresh session back to the login page
        with pytest.raises(api.EwebtelConnectionError, match="accepted the login"):
            await client.async_get_nodes()

    run(test)
