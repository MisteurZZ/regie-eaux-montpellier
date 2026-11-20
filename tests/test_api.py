"""Exercise the subscriber session using an in-memory HTTP transport."""

from collections import deque
from json import loads
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from custom_components.regie_eaux_montpellier.api import (
    CONSUMPTION,
    LOGIN,
    ContractError,
    InvalidAuth,
    MontpellierClient,
    ProtocolError,
    Response,
    TemporaryError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "readings.json"


class FakeTransport:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []
        self.cleared = 0

    def clear_cookies(self):
        self.cleared += 1

    async def request(self, method, path, *, headers=None, json=None):
        self.calls.append((method, path, headers, json))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def login(token="session-one"):
    return Response(200, {"ToKen": token}, {"conversationId": "conversation-test"})


def readings():
    return Response(200, {}, loads(FIXTURE.read_text(encoding="utf-8")))


class PortalClientTest(IsolatedAsyncioTestCase):
    async def test_login_request_and_session_reuse(self):
        transport = FakeTransport(login(), readings(), readings())
        client = MontpellierClient(transport, "subscriber", "secret-example")
        readings_one = await client.fetch_readings("TEST")
        readings_two = await client.fetch_readings("TEST")
        self.assertEqual(readings_one, readings_two)
        self.assertEqual(transport.cleared, 1)
        self.assertEqual(transport.calls[0], (
            "POST", LOGIN, None, {"identifiant": "subscriber", "motDePasse": "secret-example"}
        ))
        for method, path, headers, body in transport.calls[1:]:
            self.assertEqual((method, path, body), ("GET", CONSUMPTION + "TEST", None))
            self.assertEqual(headers, {"Token": "session-one", "ConversationId": "conversation-test"})

    async def test_expired_session_is_renewed_once(self):
        for status in (401, 403):
            with self.subTest(status=status):
                transport = FakeTransport(login(), Response(status, {}, None), login("new"), readings())
                await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
                self.assertEqual(transport.cleared, 2)
                self.assertEqual([call[0] for call in transport.calls], ["POST", "GET", "POST", "GET"])
                self.assertEqual(transport.calls[-1][2]["Token"], "new")

    async def test_rejected_credentials_trigger_reauthentication(self):
        transport = FakeTransport(login(), Response(401, {}, None), Response(401, {}, None))
        with self.assertRaises(InvalidAuth):
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(len(transport.calls), 3)

    async def test_persistent_401_and_403_are_classified(self):
        for status, expected in ((401, ProtocolError), (403, ContractError)):
            with self.subTest(status=status):
                denied = Response(status, {}, None)
                transport = FakeTransport(login(), denied, login(), denied)
                with self.assertRaises(expected):
                    await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
                self.assertEqual(len(transport.calls), 4)

    async def test_rate_limit_does_not_trigger_login_again(self):
        transport = FakeTransport(login(), Response(429, {"Retry-After": "1800"}, None))
        with self.assertRaises(TemporaryError) as raised:
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(raised.exception.retry_after, 1800)
        self.assertEqual(len(transport.calls), 2)

    async def test_http_date_retry_after(self):
        transport = FakeTransport(Response(503, {"retry-after": "Wed, 01 Jan 2070 00:00:00 GMT"}, None))
        with self.assertRaises(TemporaryError) as raised:
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertGreater(raised.exception.retry_after, 0)

    async def test_unknown_200_login_response_is_a_protocol_error(self):
        transport = FakeTransport(Response(200, {}, {"debug": "sensitive payload"}))
        with self.assertRaises(ProtocolError) as raised:
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(len(transport.calls), 1)

    async def test_redirect_is_refused(self):
        transport = FakeTransport(Response(302, {"location": "https://example.org"}, None))
        with self.assertRaises(ProtocolError):
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(len(transport.calls), 1)

    async def test_valid_empty_account_has_no_readings(self):
        transport = FakeTransport(login(), Response(200, {}, {"nbTotalResultats": 0, "resultats": []}))
        self.assertEqual(await MontpellierClient(transport, "a", "b").fetch_readings("TEST"), ())
