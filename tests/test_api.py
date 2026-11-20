"""Exercise the subscriber session using an in-memory HTTP transport."""

from collections import deque
from json import loads
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from uuid import UUID

from custom_components.regie_eaux_montpellier.api import (
    CONSUMPTION,
    GENERATE_TOKEN,
    LOGIN,
    PORTAL_APP,
    ContractError,
    InvalidAuth,
    LoginProtocolError,
    MFARequired,
    MontpellierClient,
    ProtocolError,
    Response,
    TemporaryError,
    decode_body,
    encode_json,
)

FIXTURES = Path(__file__).parent / "fixtures"
CONVERSATION_PREFIX = "JS-WEB-Netscape-"


class FakeTransport:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []
        self.cleared = 0
        self.cookie_tokens = []

    def clear_cookies(self):
        self.cleared += 1

    def set_token_cookie(self, token):
        self.cookie_tokens.append(token)

    async def request(self, method, path, *, headers=None, json=None, read_text=False):
        self.calls.append((method, path, headers, json, read_text))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def login(token="session-one"):
    return Response(200, {}, {"tokenAuthentique": token})


def open_token(token="openToken-fixture"):
    return Response(200, {}, {"token": token, "expirationDate": "2026-11-20T14:30:56+01:00"})


def bootstrap(*, cached=False, token="openToken-fixture"):
    landing = Response(200, {}, None)
    if cached:
        return landing, open_token(token)
    script = (FIXTURES / "portal_app.js").read_text(encoding="utf-8")
    return landing, Response(200, {}, script), open_token(token)


def readings():
    return Response(200, {}, loads((FIXTURES / "readings.json").read_text(encoding="utf-8")))


class PortalClientTest(IsolatedAsyncioTestCase):
    async def test_login_request_and_session_reuse(self):
        transport = FakeTransport(*bootstrap(), login(), readings(), readings())
        client = MontpellierClient(transport, "subscriber", "secret-example")
        readings_one = await client.fetch_readings("TEST")
        readings_two = await client.fetch_readings("TEST")
        self.assertEqual(readings_one, readings_two)
        self.assertEqual(transport.cleared, 1)
        self.assertEqual(transport.cookie_tokens, ["session-one"])
        self.assertEqual(transport.calls[:2], [
            ("GET", "/", {"Accept": "text/html, */*;q=0.8"}, None, False),
            ("GET", PORTAL_APP, {"Accept": "*/*", "Cache-Control": "no-cache"}, None, True),
        ])
        method, path, headers, body, _ = transport.calls[2]
        self.assertEqual((method, path), ("POST", GENERATE_TOKEN))
        self.assertEqual(headers["Token"], "test-app-key")
        self.assertEqual(body["AccessKey"], "test-app-key")
        self.assertEqual(body["ClientId"], "test-client")
        conversations = [headers["ConversationId"], body["ConversationId"]]
        method, path, headers, body, _ = transport.calls[3]
        self.assertEqual((method, path), ("POST", LOGIN))
        self.assertEqual(body, {"identifiant": "subscriber", "motDePasse": "secret-example"})
        self.assertEqual(headers["Token"], "openToken-fixture")
        self.assertEqual(headers["Origin"], "https://ael.regiedeseaux3m.fr")
        self.assertEqual(headers["Referer"], "https://ael.regiedeseaux3m.fr/")
        conversations.append(headers["ConversationId"])
        for method, path, headers, body, _ in transport.calls[4:]:
            self.assertEqual((method, path, body), ("GET", CONSUMPTION + "TEST", None))
            self.assertEqual(headers["Token"], "session-one")
            conversations.append(headers["ConversationId"])
        self.assertEqual(len(set(conversations)), 5)
        for cid in conversations:
            self.assertTrue(cid.startswith(CONVERSATION_PREFIX))
            self.assertEqual(UUID(cid.removeprefix(CONVERSATION_PREFIX)).version, 4)

    async def test_prefers_the_subscriber_token_to_an_echoed_header(self):
        transport = FakeTransport(
            *bootstrap(),
            Response(200, {"Token": "temporary"}, {"tokenAuthentique": "session-json"}),
            readings(),
        )
        await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(transport.calls[-1][2]["Token"], "session-json")
        self.assertEqual(transport.cookie_tokens, ["session-json"])

    async def test_expired_session_is_renewed_once(self):
        for status in (401, 403, 410):
            with self.subTest(status=status):
                transport = FakeTransport(
                    *bootstrap(), login(), Response(status, {}, None),
                    *bootstrap(cached=True, token="openToken-new"), login("new"), readings(),
                )
                await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
                self.assertEqual(transport.cleared, 2)
                self.assertEqual(transport.cookie_tokens, ["session-one", "new"])
                login_attempts = [call for call in transport.calls if call[1] == LOGIN]
                self.assertEqual([call[2]["Token"] for call in login_attempts], [
                    "openToken-fixture", "openToken-new",
                ])
                self.assertEqual(sum(call[1] == PORTAL_APP for call in transport.calls), 1)
                self.assertEqual(transport.calls[-1][2]["Token"], "new")

    async def test_expired_open_token_is_renewed_once_during_login(self):
        transport = FakeTransport(
            *bootstrap(), Response(410, {}, None),
            *bootstrap(cached=True), Response(410, {}, None),
        )
        with self.assertRaises(LoginProtocolError):
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(sum(call[1] == LOGIN for call in transport.calls), 2)
        self.assertEqual(transport.cookie_tokens, [])

    async def test_rejected_credentials_trigger_reauthentication(self):
        transport = FakeTransport(
            *bootstrap(), login(), Response(401, {}, None),
            *bootstrap(cached=True), Response(401, {}, None),
        )
        with self.assertRaises(InvalidAuth):
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(transport.calls[-1][1], LOGIN)

    async def test_persistent_session_rejection_is_classified(self):
        for status, expected in ((401, ProtocolError), (403, ContractError), (410, ProtocolError)):
            with self.subTest(status=status):
                denied = Response(status, {}, None)
                transport = FakeTransport(
                    *bootstrap(), login(), denied, *bootstrap(cached=True), login(), denied
                )
                with self.assertRaises(expected):
                    await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
                self.assertEqual(sum(call[1] == CONSUMPTION + "TEST" for call in transport.calls), 2)

    async def test_rate_limit_does_not_trigger_login_again(self):
        transport = FakeTransport(
            *bootstrap(), login(), Response(429, {"Retry-After": "1800"}, None)
        )
        with self.assertRaises(TemporaryError) as raised:
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(raised.exception.retry_after, 1800)
        self.assertEqual(len(transport.calls), 5)

    async def test_bootstrap_406_identifies_the_rejected_step(self):
        stages = ("Portal landing page", "Public portal script", "Anonymous token request")
        for index, stage in enumerate(stages):
            with self.subTest(stage=stage):
                transport = FakeTransport(
                    *bootstrap()[:index],
                    Response(406, {}, {"detail": "private portal response"}),
                )
                with self.assertRaises(LoginProtocolError) as raised:
                    await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
                self.assertIn(f"{stage} returned HTTP 406", str(raised.exception))
                self.assertNotIn("private portal response", str(raised.exception))
                self.assertNotIn(LOGIN, [call[1] for call in transport.calls])

    async def test_http_date_retry_after(self):
        transport = FakeTransport(
            Response(503, {"retry-after": "Wed, 01 Jan 2070 00:00:00 GMT"}, None)
        )
        with self.assertRaises(TemporaryError) as raised:
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertGreater(raised.exception.retry_after, 0)

    async def test_bad_login_response_logs_only_validation_metadata(self):
        body = {
            "aelCode": "U12",
            "message": "private portal response",
            "errors": {"$.identifiant": ["secret"], "private-field": ["other secret"]},
        }
        transport = FakeTransport(*bootstrap(), Response(400, {}, body))
        with self.assertRaisesRegex(
            LoginProtocolError, r"^Subscriber login returned HTTP 400 \(portal code U12; fields identifiant\)$"
        ):
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")

    async def test_login_payload_is_compact_utf8_json(self):
        payload = {"identifiant": "test", "motDePasse": "été-pass"}
        encoded = encode_json(payload)
        self.assertEqual(encoded.decode("utf-8"), '{"identifiant":"test","motDePasse":"été-pass"}')
        self.assertEqual(decode_body(encoded, read_text=False), payload)
        self.assertIsNone(decode_body(b"<html>Bad request</html>", read_text=False))

    async def test_unknown_200_login_response_is_a_protocol_error(self):
        transport = FakeTransport(
            *bootstrap(),
            Response(200, {"Token": "openToken-fixture"}, {"debug": "sensitive payload"}),
        )
        with self.assertRaises(LoginProtocolError) as raised:
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertIn("HTTP 200", str(raised.exception))
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(transport.calls[-1][1], LOGIN)

    async def test_unsupported_public_configuration_never_sends_credentials(self):
        transport = FakeTransport(Response(200, {}, None), Response(200, {}, "unrelated JavaScript"))
        with self.assertRaises(LoginProtocolError):
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual([call[0] for call in transport.calls], ["GET", "GET"])

    async def test_rejected_app_configuration_is_reloaded_on_the_next_attempt(self):
        landing, script, _ = bootstrap()
        transport = FakeTransport(
            landing, script, Response(403, {}, None), *bootstrap(), login(), readings()
        )
        client = MontpellierClient(transport, "a", "b")
        with self.assertRaises(LoginProtocolError):
            await client.fetch_readings("TEST")
        await client.fetch_readings("TEST")
        self.assertEqual(sum(call[1] == PORTAL_APP for call in transport.calls), 2)
        self.assertEqual(sum(call[1] == LOGIN for call in transport.calls), 1)

    async def test_second_factor_challenge_is_not_used_as_a_session(self):
        transport = FakeTransport(
            *bootstrap(), Response(200, {}, {"isMFA": True, "tokenAuthentique": "challenge"})
        )
        with self.assertRaises(MFARequired):
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(transport.cookie_tokens, [])
        self.assertEqual(transport.calls[-1][1], LOGIN)

    async def test_redirect_is_refused(self):
        transport = FakeTransport(Response(302, {"location": "https://example.org"}, None))
        with self.assertRaises(LoginProtocolError):
            await MontpellierClient(transport, "a", "b").fetch_readings("TEST")
        self.assertEqual(len(transport.calls), 1)

    async def test_valid_empty_account_has_no_readings(self):
        transport = FakeTransport(
            *bootstrap(), login(), Response(200, {}, {"nbTotalResultats": 0, "resultats": []})
        )
        self.assertEqual(await MontpellierClient(transport, "a", "b").fetch_readings("TEST"), ())
