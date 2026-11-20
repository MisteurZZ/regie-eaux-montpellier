"""Client for normal subscriber access to the Montpellier water portal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping, Protocol
from urllib.parse import quote

from .history import InvalidReadings, Reading, parse_readings

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

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
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


def _check_status(response: Response) -> None:
    status = response.status
    if 200 <= status < 300:
        return
    if status in (408, 429) or 500 <= status < 600:
        headers = {k.lower(): v for k, v in response.headers.items()}
        raise TemporaryError("Portal temporarily unavailable", _retry_after(headers.get("retry-after")))
    raise ProtocolError("Unexpected portal HTTP status")


def _session_headers(response: Response) -> dict[str, str]:
    """Read server-issued fields from headers or the root JSON object."""
    fields = {k.lower(): v for k, v in response.headers.items()}
    if isinstance(response.body, dict):
        for key, value in response.body.items():
            fields.setdefault(key.lower(), value)

    result = {}
    for name in ("Token", "ConversationId"):
        value = fields.get(name.lower())
        if not isinstance(value, str) or not value.strip() or not value.isprintable():
            raise ProtocolError("Missing or invalid login session fields")
        result[name] = value
    return result


class MontpellierClient:
    """Authenticate when required and renew an expired session once."""

    def __init__(self, transport: Transport, username: str, password: str) -> None:
        self._transport = transport
        self._username = username
        self._password = password
        self._session_headers: dict[str, str] | None = None

    async def _login(self) -> None:
        self._session_headers = None
        self._transport.clear_cookies()
        response = await self._transport.request(
            "POST",
            LOGIN,
            json={"identifiant": self._username, "motDePasse": self._password},
        )
        if response.status in (401, 403):
            raise InvalidAuth("Portal login rejected")
        _check_status(response)
        self._session_headers = _session_headers(response)

    async def fetch_readings(self, contract: str) -> tuple[Reading, ...]:
        """Read the supplied subscription under the subscriber's session."""
        if self._session_headers is None:
            await self._login()

        path = CONSUMPTION + quote(contract, safe="")
        response = await self._transport.request("GET", path, headers=self._session_headers)
        if response.status in (401, 403):
            await self._login()
            response = await self._transport.request("GET", path, headers=self._session_headers)
        if response.status in (400, 403, 404):
            raise ContractError("Portal did not return this subscription")
        if response.status == 401:
            self._session_headers = None
            raise ProtocolError("Portal rejected the renewed session")

        _check_status(response)
        try:
            return parse_readings(response.body, contract)
        except InvalidReadings as err:
            raise ProtocolError(str(err)) from None
