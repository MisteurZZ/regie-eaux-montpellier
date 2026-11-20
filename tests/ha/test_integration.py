"""Run the config flow, entities and statistics against Home Assistant Core."""

from datetime import date, datetime, timedelta, timezone
from functools import partial
from json import loads
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant.components.recorder.common import async_recorder_block_till_done

from homeassistant.components.energy import validate as energy_validation
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry
from homeassistant.util import dt as dt_util

from custom_components.regie_eaux_montpellier.api import GENERATE_TOKEN, LOGIN, PORTAL_APP, Response
from custom_components.regie_eaux_montpellier.const import (
    CONF_ALERT_THRESHOLD,
    CONF_CONTRACT,
    CONF_COST_ENABLED,
    CONF_TARIFFS,
    DOMAIN,
)
from custom_components.regie_eaux_montpellier.history import PARIS
from custom_components.regie_eaux_montpellier.http import PortalTransport

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INPUT = {"username": "test-login", "password": "test-password", CONF_CONTRACT: "TEST"}


@pytest.fixture
def portal():
    """Control the portal transport without contacting a network."""
    state = {
        "status": 200,
        "request_count": 0,
        "payload": loads((FIXTURES / "readings.json").read_text(encoding="utf-8")),
    }
    script = (FIXTURES / "portal_app.js").read_text(encoding="utf-8")

    async def request(self, method, path, *, headers=None, json=None, read_text=False):
        state["request_count"] += 1
        if (method, path) == ("GET", "/"):
            assert headers["Accept"].startswith("text/html")
            return Response(200, {}, None)
        if (method, path) == ("GET", PORTAL_APP):
            assert headers["Accept"] == "*/*"
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


async def test_estimated_cost_options_publish_energy_statistics(hass, recorder_mock, portal):
    entry = await add_contract(hass)
    status = entity_state(hass, entry, "binary_sensor", "portal_status")
    water_id = status.attributes["energy_statistic_id"]
    cost_id = f"{DOMAIN}:cout_{entry.unique_id}"
    water_rows = await recorded_hours(hass, water_id)
    get_cost_metadata = partial(get_metadata, hass, statistic_ids={cost_id}, statistic_type="sum")
    assert not await get_instance(hass).async_add_executor_job(get_cost_metadata)

    options = {
        CONF_ALERT_THRESHOLD: 600,
        CONF_COST_ENABLED: True,
        "price_per_m3": 3.6078,
        "tariff_effective_from": "2026-11-17",
    }
    request_count = portal["request_count"]
    coordinator = entry.runtime_data
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM
    defaults = {str(key.schema): key.default() for key in flow["data_schema"].schema}
    assert defaults[CONF_COST_ENABLED] is False
    assert defaults["price_per_m3"] == 3.6078
    assert defaults["tariff_effective_from"] == "2026-11-17"
    result = await hass.config_entries.options.async_configure(flow["flow_id"], options)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data is coordinator
    assert entry.options[CONF_ALERT_THRESHOLD] == 600
    assert portal["request_count"] == request_count

    cost_rows = await recorded_hours(hass, cost_id)
    assert [row["start"] for row in cost_rows] == [row["start"] for row in water_rows]
    assert [row["sum"] for row in cost_rows] == pytest.approx(
        [0, 1.5477462, 1.5477462, 3.0269442, 3.0269442, 4.9499016]
    )
    assert await recorded_hours(hass, water_id) == water_rows

    metadata = await get_instance(hass).async_add_executor_job(get_cost_metadata)
    assert metadata[cost_id][1]["unit_of_measurement"] == "EUR"
    assert metadata[cost_id][1]["source"] == DOMAIN
    assert metadata[cost_id][1]["has_sum"] is True

    manager = SimpleNamespace(
        data={
            "energy_sources": [
                {"type": "water", "stat_energy_from": water_id, "stat_cost": cost_id}
            ],
            "device_consumption": [],
        }
    )
    with patch.object(energy_validation.data, "async_get_manager", AsyncMock(return_value=manager)):
        validation = await energy_validation.async_validate(hass)
    assert validation.as_dict()["energy_sources"] == [[]]

    today = dt_util.utcnow().astimezone(PARIS).date()
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    rejected = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {**options, "price_per_m3": 4.1234, "tariff_effective_from": today.isoformat()},
    )
    assert rejected["errors"] == {"tariff_effective_from": "retroactive_tariff"}
    next_rate_day = max(today + timedelta(days=1), date(2026, 11, 20))
    options.update(price_per_m3=4.1234, tariff_effective_from=next_rate_day.isoformat())
    result = await hass.config_entries.options.async_configure(flow["flow_id"], options)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert await recorded_hours(hass, cost_id) == cost_rows

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {**options, CONF_COST_ENABLED: False}
    )
    await hass.async_block_till_done()
    first = portal["payload"]["resultats"][0]
    first["volumeConsoEnLitres"] = 500
    first["volumeConsoEnM3"] = 0.500
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert await recorded_hours(hass, cost_id) == cost_rows
    assert (await recorded_hours(hass, water_id))[-1]["sum"] == pytest.approx(1.339)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(flow["flow_id"], options)
    await hass.async_block_till_done()
    assert [row["price_per_m3"] for row in entry.options[CONF_TARIFFS]] == ["3.6078", "4.1234"]
    corrected = await recorded_hours(hass, cost_id)
    assert [row["start"] for row in corrected] == [row["start"] for row in cost_rows]
    assert [row["sum"] for row in corrected] == pytest.approx(
        [0, 1.5477462, 1.5477462, 3.0269442, 3.0269442, 4.8308442]
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    assert await recorded_hours(hass, cost_id) == corrected
