"""Check the normal portal login on a Mac without installing Home Assistant."""

import asyncio
from getpass import getpass
from http.cookiejar import Cookie, CookieJar
from sys import stdin
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from custom_components.regie_eaux_montpellier.api import (
    API_ACCEPT,
    JSON_CONTENT_TYPE,
    LOGIN,
    MontpellierClient,
    PortalError,
    Response,
    TemporaryError,
    decode_body,
    encode_json,
)
from custom_components.regie_eaux_montpellier.const import BASE_URL


class NoRedirects(HTTPRedirectHandler):
    """Keep all requests on the configured HTTPS origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TerminalTransport:
    """Use the standard HTTPS client for a one-off connection check."""

    def __init__(self):
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), NoRedirects())

    def clear_cookies(self):
        self.cookies.clear()

    def set_token_cookie(self, token):
        self.cookies.set_cookie(Cookie(
            version=0, name="aelToken", value=token,
            port=None, port_specified=False,
            domain="ael.regiedeseaux3m.fr", domain_specified=False, domain_initial_dot=False,
            path="/", path_specified=True, secure=True, expires=None,
            discard=True, comment=None, comment_url=None, rest={},
        ))

    async def request(self, method, path, *, headers=None, json=None, read_text=False):
        return await asyncio.to_thread(self._request, method, path, headers, json, read_text)

    def _request(self, method, path, headers, payload, read_text):
        headers = {"Accept": API_ACCEPT, **(headers or {})}
        data = None
        if payload is not None:
            headers["Content-Type"] = JSON_CONTENT_TYPE
            data = encode_json(payload)
        request = Request(BASE_URL + path, method=method, headers=headers, data=data)
        try:
            try:
                response = self.opener.open(request, timeout=20)
            except HTTPError as err:
                response = err
            with response:
                body = None
                if 200 <= response.status < 300 or (path == LOGIN and response.status == 400):
                    body = decode_body(response.read(), read_text=read_text)
                return Response(response.status, dict(response.headers), body)
        except (URLError, OSError):
            raise TemporaryError("Portal connection failed") from None


def main() -> int:
    if not stdin.isatty():
        print("Lance le test dans une fenêtre Terminal pour saisir les identifiants.")
        return 1
    username = getpass("Identifiant du portail (masqué) : ").strip()
    password = getpass("Mot de passe du portail (masqué) : ")
    contract = getpass("Numéro de contrat (masqué) : ").strip()
    if not all((username, password, contract)):
        print("Les trois champs sont nécessaires.")
        return 1
    try:
        readings = asyncio.run(
            MontpellierClient(TerminalTransport(), username, password).fetch_readings(contract)
        )
    except PortalError as err:
        print(f"Échec du test. {err}")
        return 1
    print(f"Connexion réussie. {len(readings)} relevé(s) reçu(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
