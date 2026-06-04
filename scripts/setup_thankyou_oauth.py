#!/usr/bin/env python3
"""
One-time OAuth setup for the Sun & Soil thank you email script.

Requests gmail.send + spreadsheets scopes and saves tokens to:
    ~/.openjarvis/connectors/lawncare.json

Run once:
    uv run python scripts/setup_thankyou_oauth.py
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from openjarvis.core import open_browser

CONFIG_DIR = Path.home() / ".openjarvis" / "connectors"
OUTPUT_FILE = CONFIG_DIR / "lawncare.json"
CALLBACK_PORT = 8789
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
]


def load_client_creds():
    path = CONFIG_DIR / "gmail.json"
    data = json.loads(path.read_text())
    return data["client_id"], data["client_secret"]


def wait_for_code():
    auth_code = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            if "code" in params:
                auth_code.append(params["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Auth complete!</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
    server.timeout = 120
    while not auth_code:
        server.handle_request()
    server.server_close()
    return auth_code[0]


def main():
    client_id, client_secret = load_client_creds()

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })

    print("Opening browser for Google OAuth...")
    print(f"Scopes: {', '.join(SCOPES)}")
    open_browser(url)

    print("Waiting for callback on http://localhost:8789 ...")
    code = wait_for_code()

    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    tokens = resp.json()

    payload = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_in": tokens.get("expires_in", 3600),
        "client_id": client_id,
        "client_secret": client_secret,
    }

    OUTPUT_FILE.write_text(json.dumps(payload, indent=2))
    OUTPUT_FILE.chmod(0o600)
    print(f"\nTokens saved to {OUTPUT_FILE}")
    print("You can now run: uv run python scripts/send_thankyou.py --dry-run")


if __name__ == "__main__":
    main()
