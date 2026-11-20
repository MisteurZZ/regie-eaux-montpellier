"""Verify the portal login request emitted by the Home Assistant transport."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.regie_eaux_montpellier.api import LOGIN
from custom_components.regie_eaux_montpellier.const import BASE_URL
from custom_components.regie_eaux_montpellier.http import PortalTransport


async def test_subscriber_login_sends_utf8_json_and_retains_the_rejection_details():
    request_context = AsyncMock()
    request_context.__aenter__.return_value = SimpleNamespace(
        status=400,
        headers={"Content-Type": "application/json"},
        read=AsyncMock(return_value=b'{"aelCode":"U12"}'),
    )
    session = MagicMock()
    session.request.return_value = request_context
    with patch(
        "custom_components.regie_eaux_montpellier.http.async_create_clientsession",
        return_value=session,
    ):
        transport = PortalTransport(object())

    response = await transport.request(
        "POST",
        LOGIN,
        headers={"Token": "openToken-fixture"},
        json={"identifiant": "test", "motDePasse": "été-pass"},
    )

    session.request.assert_called_once_with(
        "POST",
        BASE_URL + LOGIN,
        headers={
            "Token": "openToken-fixture",
            "Content-Type": "application/json;charset=utf-8",
        },
        data='{"identifiant":"test","motDePasse":"été-pass"}'.encode("utf-8"),
        allow_redirects=False,
    )
    assert response.status == 400
    assert response.body == {"aelCode": "U12"}
