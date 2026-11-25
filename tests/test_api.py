"""Tests of the portal page and JSON parsers."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from conftest import api

LOGIN_HTML = """
<html><body>
<form action="/Home/SetCulture" method="get"><input name="culture" value="pl-PL"></form>
<form action="/Account/Login" class="form-group w-100" method="post" role="form">
<input name="__RequestVerificationToken" type="hidden" value="TOKEN123" />
<input data-val="true" id="isBreakMsgEnabled" name="isBreakMsgEnabled" type="hidden" value="False" />
<input id="BreakMessage" name="BreakMessage" type="hidden" value="" />
<button class="btn" type="button" id="lang">EN-US</button>
<input class="form-control" id="UserName" maxlength="256" name="UserName" type="text" value="" />
<input class="form-control" id="Password" name="Password" type="password" />
<input type="submit" value="Log in" />
</form>
</body></html>
"""

MODEL = {
    "DeviceId": 1,
    "ValueMultiplier": 0.0,
    "GraphSeries": [
        {
            "DeviceId": 0,
            "NodeGuid": "00000000-0000-0000-0000-000000000000",
            "ParameterDpIndex": 100,
            "CalculationMethod": 2,
            "Name": "Q",
            "Data": None,
        }
    ],
    "Id": "x",
    "Title": "Graph_Flow",
    "DisplayPeriod": 7,
    "SelectedDate": None,
    "SelectedDateStr": "18/11/2026 10:16",
}


def details_html(model: dict) -> str:
    body = json.dumps(model, indent=2).replace("\n", "\r\n")
    return (
        "<script>\r\n let graphModelPrimary;\r\n let graphModelSecondary;\r\n"
        f"         graphModelPrimary = {body};\r\n"
        '         graphModelSecondary = {"GraphSeries": [{"Name": "dV"}]};\r\n</script>'
    )


def test_parse_login_form():
    fields = api.parse_login_form(LOGIN_HTML)
    assert fields == {
        "__RequestVerificationToken": "TOKEN123",
        "isBreakMsgEnabled": "False",
        "BreakMessage": "",
        "UserName": "",
        "Password": "",
    }


def test_parse_login_form_missing():
    with pytest.raises(api.EwebtelConnectionError):
        api.parse_login_form("<html><form action='/x'></form></html>")


def test_parse_graph_model():
    model = api.parse_graph_model(details_html(MODEL))
    assert model == MODEL


def test_parse_graph_model_missing():
    with pytest.raises(api.EwebtelConnectionError):
        api.parse_graph_model("<html></html>")


def test_parse_nodes():
    payload = {
        "sEcho": "1",
        "iTotalRecords": 1,
        "aaData": [
            {"Id": 42, "Name": "12345", "MeterSN": "ABC123 ", "Type": 1, "Adress": "Street 1 "}
        ],
    }
    assert api.parse_nodes(payload) == [
        api.Node(node_id=42, name="12345", meter_sn="ABC123", address="Street 1")
    ]


def test_parse_graph_readings():
    payload = {
        "Success": True,
        "IsApplicationError": False,
        "Data": {
            "GraphSeries": [
                {
                    "Data": [
                        {"D": "2026-11-24T00:00:00", "V": ["486.742"], "S": 8},
                        {"D": "2026-11-24T01:00:00", "V": [None], "S": 8},
                        {"D": "bad", "V": ["1"], "S": 8},
                        {"D": "2026-11-24T02:00:00", "V": ["486.748"], "S": 168},
                    ]
                }
            ]
        },
    }
    readings = api.parse_graph_readings(payload)
    assert [(r.local_time, r.value, r.status) for r in readings] == [
        (datetime(2026, 11, 24, 0), 486.742, 8),
        (datetime(2026, 11, 24, 2), 486.748, 168),
    ]


def test_parse_graph_readings_error():
    with pytest.raises(api.EwebtelConnectionError):
        api.parse_graph_readings({"Success": False, "IsApplicationError": True, "Data": "boom"})


@pytest.mark.parametrize("row", [{"Name": "x"}, {"Id": None}, {"Id": "abc"}, "row"])
def test_parse_nodes_bad_row(row):
    with pytest.raises(api.EwebtelConnectionError):
        api.parse_nodes({"aaData": [row]})
