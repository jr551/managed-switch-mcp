"""HTTP client for the GT-ST018M managed switch.

The web UI uses a cookie-based session with a quirky challenge:

  cookie `admin` = md5(username + password)

That same hash is also POSTed to /login.cgi as the `Response` field. The
server reads the cookie for subsequent requests. There is no logout endpoint
we need to worry about; sessions appear to be long-lived as long as the
cookie keeps being sent.

This client is **read-only by design** — it only ever issues GETs to the CGI
endpoints that render data, plus a single benign POST for MAC table
pagination (`cmd=perpage` / `cmd=nextpage`) which only affects the current
session view, not the device configuration.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)


@dataclass
class SwitchConfig:
    host: str
    username: str
    password: str
    timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "SwitchConfig":
        host = os.environ.get("SWITCH_HOST", "192.168.16.3").strip()
        if host.startswith("http://") or host.startswith("https://"):
            host = host.split("://", 1)[1]
        host = host.rstrip("/")
        return cls(
            host=host,
            username=os.environ.get("SWITCH_USERNAME", "admin"),
            password=os.environ.get("SWITCH_PASSWORD", "admin"),
            timeout=float(os.environ.get("SWITCH_TIMEOUT", "10")),
        )


class SwitchClient:
    """Tiny async HTTP client that knows how to log in and stay logged in."""

    def __init__(self, cfg: SwitchConfig):
        self.cfg = cfg
        self._client: Optional[httpx.AsyncClient] = None
        self._auth_cookie: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return f"http://{self.cfg.host}"

    def _challenge(self) -> str:
        raw = (self.cfg.username + self.cfg.password).encode("ascii")
        return hashlib.md5(raw).hexdigest()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.cfg.timeout,
                follow_redirects=False,
                headers={"User-Agent": "managed-switch-mcp/0.1"},
            )
        return self._client

    async def login(self) -> None:
        client = await self._ensure_client()
        token = self._challenge()
        # The UI sets the cookie *before* posting; the CGI checks both.
        client.cookies.set("admin", token, domain=self.cfg.host, path="/")
        data = {
            "username": self.cfg.username,
            "password": self.cfg.password,
            "Response": token,
            "language": "EN",
        }
        r = await client.post("/login.cgi", data=data)
        r.raise_for_status()
        # On success the server replies with a tiny HTML redirect to "/".
        # Wrong credentials leave us on a login page including the word "error".
        if "login.cgi" in r.text and "Response" in r.text:
            raise RuntimeError("Login rejected — check SWITCH_USERNAME/SWITCH_PASSWORD.")
        self._auth_cookie = token
        log.info("Logged in to %s as %s", self.cfg.host, self.cfg.username)

    async def _ensure_authed(self) -> None:
        if self._auth_cookie is None:
            async with self._lock:
                if self._auth_cookie is None:
                    await self.login()

    def _is_login_page(self, html: str) -> bool:
        # An unauthenticated request gets redirected via inline JS to /login.cgi.
        return 'location.replace("/login.cgi")' in html or "<title>GT-ST018M</title>" in html and "login.cgi" in html

    async def fetch(self, path: str) -> str:
        """GET a CGI page and return its HTML, retrying login once if needed."""
        await self._ensure_authed()
        client = await self._ensure_client()
        url = path if path.startswith("/") else f"/{path}"
        headers = {"Referer": f"{self.base_url}/"}
        r = await client.get(url, headers=headers)
        if r.status_code == 200 and self._is_login_page(r.text):
            log.info("Session looks stale — re-logging in")
            self._auth_cookie = None
            await self._ensure_authed()
            r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.text

    async def fetch_bytes(self, path: str) -> tuple[bytes, str]:
        """GET a binary file (used for config backup). Returns (body, suggested_filename)."""
        await self._ensure_authed()
        client = await self._ensure_client()
        url = path if path.startswith("/") else f"/{path}"
        headers = {"Referer": f"{self.base_url}/"}
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        cd = r.headers.get("content-disposition", "")
        fname = "switch_cfg.bin"
        if "filename=" in cd:
            fname = cd.split("filename=", 1)[1].strip().strip('"')
        return r.content, fname

    async def post(self, path: str, data: dict[str, str]) -> str:
        """POST form data. Only used for harmless session-scope ops (MAC pagination)."""
        await self._ensure_authed()
        client = await self._ensure_client()
        url = path if path.startswith("/") else f"/{path}"
        headers = {"Referer": f"{self.base_url}{url}"}
        r = await client.post(url, data=data, headers=headers)
        if r.status_code == 200 and self._is_login_page(r.text):
            self._auth_cookie = None
            await self._ensure_authed()
            r = await client.post(url, data=data, headers=headers)
        r.raise_for_status()
        return r.text

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
