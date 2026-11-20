"""Home Assistant HTTP transport with a private cookie jar."""

from __future__ import annotations

from http.cookies import SimpleCookie
from json import JSONDecodeError

from aiohttp import ClientError, ClientSession, ClientTimeout, CookieJar
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import Response, TemporaryError
from .const import BASE_URL


class PortalTransport:
    """Keep credentials and session cookies on the official HTTPS origin."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._session: ClientSession = async_create_clientsession(
            hass,
            auto_cleanup=False,
            cookie_jar=CookieJar(),
            timeout=ClientTimeout(total=20),
            headers={"Accept": "application/json, text/plain, */*"},
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
        try:
            async with self._session.request(
                method, BASE_URL + path, headers=headers, json=json, allow_redirects=False
            ) as response:
                body = None
                if 200 <= response.status < 300:
                    try:
                        body = (
                            await response.text(encoding="utf-8")
                            if read_text
                            else await response.json(content_type=None)
                        )
                    except (JSONDecodeError, UnicodeError):
                        pass
                return Response(response.status, dict(response.headers), body)
        except (ClientError, TimeoutError):
            raise TemporaryError("Portal connection failed") from None
