"""Home Assistant HTTP transport with a private cookie jar."""

from __future__ import annotations

from http.cookies import SimpleCookie

from aiohttp import ClientError, ClientSession, ClientTimeout, CookieJar
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    API_ACCEPT,
    JSON_CONTENT_TYPE,
    LOGIN,
    Response,
    TemporaryError,
    decode_body,
    encode_json,
)
from .const import BASE_URL


class PortalTransport:
    """Keep credentials and session cookies on the official HTTPS origin."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._session: ClientSession = async_create_clientsession(
            hass,
            auto_cleanup=False,
            # The portal sends the load balancer cookie as an unquoted value.
            cookie_jar=CookieJar(quote_cookie=False),
            timeout=ClientTimeout(total=20),
            headers={"Accept": API_ACCEPT},
        )

    def clear_cookies(self) -> None:
        """Discard expired or partial login cookies."""
        self._session.cookie_jar.clear()

    def set_token_cookie(self, token: str) -> None:
        """Mirror the portal's cookie for the authenticated session."""
        cookies = SimpleCookie()
        cookies["aelToken"] = token
        cookies["aelToken"]["secure"] = True
        cookies["aelToken"]["path"] = "/"
        self._session.cookie_jar.update_cookies(cookies, response_url=URL(BASE_URL))

    def close(self) -> None:
        """Detach from the HA-managed connector."""
        self._session.detach()

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        read_text: bool = False,
    ) -> Response:
        """Send a bounded request without following redirects."""
        data = None
        if json is not None:
            headers = {**(headers or {}), "Content-Type": JSON_CONTENT_TYPE}
            data = encode_json(json)
        try:
            async with self._session.request(
                method, BASE_URL + path, headers=headers, data=data, allow_redirects=False
            ) as response:
                body = None
                if 200 <= response.status < 300 or (path == LOGIN and response.status == 400):
                    body = decode_body(await response.read(), read_text=read_text)
                return Response(response.status, dict(response.headers), body)
        except (ClientError, TimeoutError):
            raise TemporaryError("Portal connection failed") from None
