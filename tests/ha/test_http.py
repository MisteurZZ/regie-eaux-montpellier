"""Verify the portal login request emitted by the Home Assistant transport."""

from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from yarl import URL

from custom_components.regie_eaux_montpellier.api import CONSUMPTION, LOGIN
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


async def test_readings_cookie_keeps_the_portal_load_balancer_format():
    with patch(
        "custom_components.regie_eaux_montpellier.http.async_create_clientsession"
    ) as create_session:
        PortalTransport(object())

    jar = create_session.call_args.kwargs["cookie_jar"]
    cookies = SimpleCookie()
    cookies.load("BIGipServerFixture=!dummy/W+YT==; Path=/; Secure")
    jar.update_cookies(cookies, response_url=URL(BASE_URL))

    outgoing = jar.filter_cookies(URL(BASE_URL + CONSUMPTION + "TEST"))
    assert outgoing["BIGipServerFixture"].coded_value == "!dummy/W+YT=="
