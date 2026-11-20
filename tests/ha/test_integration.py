"""Run the config flow, entities and statistics against Home Assistant Core."""

from datetime import datetime, timezone
from json import loads
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_homeassistant.components.recorder.common import async_recorder_block_till_done

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry

from custom_components.regie_eaux_montpellier.api import GENERATE_TOKEN, LOGIN, PORTAL_APP, Response
from custom_components.regie_eaux_montpellier.const import CONF_CONTRACT, DOMAIN
from custom_components.regie_eaux_montpellier.history import PARIS
from custom_components.regie_eaux_montpellier.http import PortalTransport

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INPUT = {"username": "test-login", "password": "test-password", CONF_CONTRACT: "TEST"}


@pytest.fixture
def portal():
    """Control the portal transport without contacting a network."""
    state = {
        "status": 200,
        "payload": loads((FIXTURES / "readings.json").read_text(encoding="utf-8")),
    }
    script = (FIXTURES / "portal_app.js").read_text(encoding="utf-8")

    async def request(self, method, path, *, headers=None, json=None, read_text=False):
        if (method, path) == ("GET", "/"):
            return Response(200, {}, None)
        if (method, path) == ("GET", PORTAL_APP):
            assert read_text
            return Response(200, {}, script)
        if (method, path) == ("POST", GENERATE_TOKEN):
            assert headers["Token"] == json["AccessKey"] == "test-app-key"
            assert json["ClientId"] == "test-client"
            return Response(200, {}, {"token": "openToken-fixture"})
        if (method, path) == ("POST", LOGIN):
            assert headers["Token"] == "openToken-fixture"
            if state["status"] == 401:
                return Response(401, {}, None)
            return Response(200, {}, {"tokenAuthentique": "fixture"})
        assert method == "GET" and path.endswith("/TEST")
        assert headers["Token"] == "fixture"
        if state["status"] != 200:
            return Response(state["status"], {"Retry-After": "1800"}, None)
        return Response(200, {}, state["payload"])

    with patch.object(PortalTransport, "request", request):
        yield state


async def add_contract(hass):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert flow["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


def entity_state(hass, entry, platform, key):
    registry = entity_registry.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{entry.unique_id}_{key}")
    return hass.states.get(entity_id)


async def recorded_hours(hass, statistic_id):
    await async_recorder_block_till_done(hass)
    series = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 11, 16, tzinfo=timezone.utc),
        datetime(2026, 11, 20, tzinfo=timezone.utc),
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    return series[statistic_id]


async def test_config_flow_entities_and_external_statistics(hass, recorder_mock, portal):
    entry = await add_contract(hass)
    index = entity_state(hass, entry, "sensor", "meter_index")
    assert float(index.state) == 484.838
    assert "state_class" not in index.attributes
    assert float(entity_state(hass, entry, "sensor", "last_volume").state) == 533
    status = entity_state(hass, entry, "binary_sensor", "portal_status")
    assert status.state == "on"
    statistic_id = status.attributes["energy_statistic_id"]
    rows = await recorded_hours(hass, statistic_id)
    assert [row["sum"] for row in rows] == pytest.approx([0, 0.429, 0.429, 0.839, 0.839, 1.372])
    assert [datetime.fromtimestamp(row["start"], PARIS).day for row in rows] == [17, 17, 18, 18, 19, 19]

    # Restart the integration with a rolling response and a correction. Its
    # cached history must remain usable and all existing hours must be upserted.
    first, second, *_ = portal["payload"]["resultats"]
    first["volumeConsoEnLitres"] = 500
    first["volumeConsoEnM3"] = 0.500
    portal["payload"] = {"nbTotalResultats": 2, "resultats": [first, second]}
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    corrected = await recorded_hours(hass, statistic_id)
    assert [row["start"] for row in corrected] == [row["start"] for row in rows]
    assert [row["sum"] for row in corrected] == pytest.approx([0, 0.429, 0.429, 0.839, 0.839, 1.339])

    assert await hass.config_entries.async_reload(entry.entry_id)
    assert await recorded_hours(hass, statistic_id) == corrected


async def test_reauth_preserves_contract_and_entry(hass, recorder_mock, portal):
    entry = await add_contract(hass)
    portal["status"] = 401
    with patch("custom_components.regie_eaux_montpellier.coordinator.monotonic", return_value=10**12):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flow = next(
        result for result in hass.config_entries.flow.async_progress()
        if result["handler"] == DOMAIN and result["context"]["source"] == SOURCE_REAUTH
    )
    response = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"username": "test-login", "password": "bad"}
    )
    assert response["errors"] == {"base": "invalid_auth"}
    portal["status"] = 200
    response = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"username": "test-login", "password": "new-test-password"}
    )
    assert response["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_CONTRACT] == "TEST"
    assert entry.data["password"] == "new-test-password"
    assert entity_state(hass, entry, "sensor", "meter_index") is not None
