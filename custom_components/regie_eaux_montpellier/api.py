"""Client for normal subscriber access to the Montpellier water portal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping, Protocol
from urllib.parse import quote
from uuid import uuid4

from .const import BASE_URL
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


class LoginProtocolError(ProtocolError):
    """The login endpoint returned an unsupported response."""


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
    raise ProtocolError(f"Unexpected portal HTTP status {status}")


def _valid_header(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.isascii() and value.isprintable()


def _session_token(response: Response) -> str:
    """Read the session token returned by the portal."""
    fields = {k.lower(): v for k, v in response.headers.items()}
    if isinstance(response.body, dict):
        for key, value in response.body.items():
            fields.setdefault(key.lower(), value)

    token = fields.get("tokenauthentique") or fields.get("token")
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

    async def _login(self) -> None:
        self._token = None
        self._transport.clear_cookies()
        response = await self._transport.request(
            "POST",
            LOGIN,
            headers={
                "ConversationId": _conversation_id(),
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/",
            },
            json={"identifiant": self._username, "motDePasse": self._password},
        )
        if response.status in (401, 403):
            raise InvalidAuth("Portal login rejected")
        try:
            _check_status(response)
        except ProtocolError as err:
            raise LoginProtocolError(str(err)) from None
        self._token = _session_token(response)

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
        if response.status in (401, 403):
            await self._login()
            response = await self._read(path)
        if response.status in (400, 403, 404):
            raise ContractError("Portal did not return this subscription")
        if response.status == 401:
            self._token = None
            raise ProtocolError("Portal rejected the renewed session")

        _check_status(response)
        try:
            return parse_readings(response.body, contract)
        except InvalidReadings as err:
            raise ProtocolError(str(err)) from None
