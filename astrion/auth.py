"""One-time OAuth 2.0 helper for the Blogger refresh token.

Run locally, not in CI. The resulting refresh token is stored as a GitHub
Secret and is never written to the repository.
"""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .blogger import BLOGGER_SCOPE, TOKEN_URL, USER_AGENT

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def authorize(
    client_id: str,
    client_secret: str,
    *,
    port: int = 8765,
    open_browser: bool = True,
    timeout: int = 300,
) -> dict[str, Any]:
    redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
    state = secrets.token_urlsafe(24)
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            parsed = urlparse(self.path)
            if parsed.path != "/oauth2callback":
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            captured["code"] = (query.get("code") or [""])[0]
            captured["state"] = (query.get("state") or [""])[0]
            captured["error"] = (query.get("error") or [""])[0]
            body = (
                "<html><body style='font-family:sans-serif;padding:2rem'>"
                "<h2>Astrion authorization</h2>"
                "<p>You can close this tab and return to the terminal.</p>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": BLOGGER_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    url = f"{AUTH_URL}?{query}"
    print("Open this URL and approve access to your Blogger account:\n")
    print(url)
    print()
    if open_browser:
        webbrowser.open(url)

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = timeout
    server.handle_request()

    if captured.get("error"):
        raise RuntimeError(f"authorization was denied: {captured['error']}")
    if captured.get("state") != state:
        raise RuntimeError("authorization state did not match; try again")
    code = captured.get("code") or ""
    if not code:
        raise RuntimeError("no authorization code was received")

    data = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"token exchange failed ({exc.code}): {exc.read().decode('utf-8', 'replace')}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"token endpoint is unreachable: {exc}") from exc

    if not payload.get("refresh_token"):
        raise RuntimeError(
            "Google did not return a refresh token. Revoke the app's access in your "
            "Google Account and run the command again (prompt=consent is required)."
        )
    return payload
