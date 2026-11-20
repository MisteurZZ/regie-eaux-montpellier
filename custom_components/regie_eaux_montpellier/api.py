"""Client for normal subscriber access to the Montpellier water portal."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from json import JSONDecodeError, loads
from typing import TYPE_CHECKING, Mapping, Protocol
from urllib.parse import quote
from uuid import uuid4

from .const import BASE_URL
from .history import InvalidReadings, Reading, parse_readings

if TYPE_CHECKING:
    from typing import TypeGuard

PORTAL_APP = "/app/app.js"
GENERATE_TOKEN = "/webapi/Acces/generateToken"
LOGIN = "/webapi/Utilisateur/authentification"
CONSUMPTION = "/webapi/Facturation/listeConsommationsFacturees/"


class PortalError(Exception):
    """A portal request failed. Messages are static and contain no secrets."""


class InvalidAuth(PortalError):
    """The login endpoint rejected the credentials."""


class ContractError(PortalError):
    """The authenticated session cannot read this contract."""


class ProtocolError(PortalError):
    """The response did not match the supported portal protocol."""


class LoginProtocolError(ProtocolError):
    """The portal sign-in exchange returned an unsupported response."""


class MFARequired(PortalError):
    """The portal requires a second factor before issuing a session."""


class TemporaryError(PortalError):
    """The portal is temporarily unreachable or rate limiting requests."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True, repr=False)
class Response:
    """The minimal response information required by the protocol."""

    status: int
    headers: Mapping[str, str]
    body: object


class Transport(Protocol):
    """HTTP transport with cookies scoped to this portal session."""

    def clear_cookies(self) -> None: ...

    def set_token_cookie(self, token: str) -> None: ...

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        read_text: bool = False,
    ) -> Response: ...


def _retry_after(header: str | None) -> float | None:
    if not header:
        return None
    if header.isascii() and header.isdecimal() and len(header) <= 15:
        return float(header)
    try:
        deadline = parsedate_to_datetime(header)
        if deadline.tzinfo is not None:
            return max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
    except (OverflowError, TypeError, ValueError):
        pass
    return None


def _check_status(response: Response, *, step: str) -> None:
    status = response.status
    if 200 <= status < 300:
        return
    if status in (408, 429) or 500 <= status < 600:
        headers = {k.lower(): v for k, v in response.headers.items()}
        raise TemporaryError(
            f"{step} temporarily unavailable (HTTP {status})",
            _retry_after(headers.get("retry-after")),
        )
    raise ProtocolError(f"{step} returned HTTP {status}")


def _valid_header(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip()) and value.isascii() and value.isprintable()


def _application_fields(script: object) -> tuple[str, str]:
    """Extract the web app's two published string literals without running JS."""
    if not isinstance(script, str):
        raise ProtocolError("Public portal script is missing")

    fields: list[str] = []
    for name in ("WS_APPLICATION_LOGIN", "WS_APPLICATION_PWD"):
        pattern = r'\b{name}\s*:\s*("(?:[^"\\]|\\.)*")'.format(name=name)
        match = re.search(pattern, script)
        if match is None:
            raise ProtocolError("Public portal script does not contain the app configuration")
        try:
            value = loads(match[1])
        except JSONDecodeError:
            raise ProtocolError("Public portal app configuration is invalid") from None
        if not _valid_header(value):
            raise ProtocolError("Public portal app configuration is invalid")
        fields.append(value)

    return fields[0], fields[1]


def _session_token(response: Response) -> str:
    """Read the session token returned by the portal."""
    token = response.body.get("tokenAuthentique") if isinstance(response.body, dict) else None
    if not _valid_header(token):
        raise LoginProtocolError(f"Login response has no usable session token (HTTP {response.status})")

    return token


def _conversation_id() -> str:
    """Build the per-request identifier used by the subscriber portal."""
    return f"JS-WEB-Netscape-{uuid4()}"


class MontpellierClient:
    """Authenticate when required and renew an expired session once."""

    def __init__(self, transport: Transport, username: str, password: str) -> None:
        self._transport = transport
        self._username = username
        self._password = password
        self._token: str | None = None
        self._application: tuple[str, str] | None = None

    async def _request_open_token(self) -> str:
        """Acquire an anonymous token using the same public app configuration."""
        try:
            landing = await self._transport.request(
                "GET", "/", headers={"Accept": "text/html, */*;q=0.8"}
            )
            _check_status(landing, step="Portal landing page")

            if self._application is None:
                script = await self._transport.request(
                    "GET",
                    PORTAL_APP,
                    headers={"Accept": "*/*", "Cache-Control": "no-cache"},
                    read_text=True,
                )
                _check_status(script, step="Public portal script")
                self._application = _application_fields(script.body)

            client_id, access_key = self._application
            response = await self._transport.request(
                "POST",
                GENERATE_TOKEN,
                headers={
                    "ConversationId": _conversation_id(),
                    "Origin": BASE_URL,
                    "Referer": f"{BASE_URL}/",
                    "Token": access_key,
                },
                json={
                    "ConversationId": _conversation_id(),
                    "ClientId": client_id,
                    "AccessKey": access_key,
                },
            )
            if response.status in (401, 403):
                self._application = None
            _check_status(response, step="Anonymous token request")
            token = response.body.get("token") if isinstance(response.body, dict) else None
            if not _valid_header(token):
                raise ProtocolError("Anonymous token response has no usable token")
            return token
        except ProtocolError as err:
            raise LoginProtocolError(f"Portal session initialisation failed: {err}") from None

    async def _request_login(self) -> Response:
        self._transport.clear_cookies()
        open_token = await self._request_open_token()
        return await self._transport.request(
            "POST",
            LOGIN,
            headers={
                "ConversationId": _conversation_id(),
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/",
                "Token": open_token,
            },
            json={"identifiant": self._username, "motDePasse": self._password},
        )

    async def _login(self) -> None:
        self._token = None
        response = await self._request_login()
        if response.status == 410:
            response = await self._request_login()
        if response.status in (401, 403):
            raise InvalidAuth("Portal login rejected")
        try:
            _check_status(response, step="Subscriber login")
        except ProtocolError as err:
            raise LoginProtocolError(str(err)) from None
        if isinstance(response.body, dict) and response.body.get("isMFA") is True:
            raise MFARequired("Portal login requires a second authentication factor")
        token = _session_token(response)
        self._transport.set_token_cookie(token)
        self._token = token

    async def _read(self, path: str) -> Response:
        assert self._token is not None
        return await self._transport.request(
            "GET", path, headers={"Token": self._token, "ConversationId": _conversation_id()}
        )

    async def fetch_readings(self, contract: str) -> tuple[Reading, ...]:
        """Read the supplied subscription under the subscriber's session."""
        if self._token is None:
            await self._login()

        path = CONSUMPTION + quote(contract, safe="")
        response = await self._read(path)
        if response.status in (401, 403, 410):
            await self._login()
            response = await self._read(path)
        if response.status in (400, 403, 404):
            raise ContractError("Portal did not return this subscription")
        if response.status in (401, 410):
            self._token = None
            raise ProtocolError("Portal rejected the renewed session")

        _check_status(response, step="Meter readings request")
        try:
            return parse_readings(response.body, contract)
        except InvalidReadings as err:
            raise ProtocolError(str(err)) from None
