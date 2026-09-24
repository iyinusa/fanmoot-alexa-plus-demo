"""The demo fan: a real, pre-linkable Fanmoot account for visitors who will not sign up (W1-8).

A judge should be able to hear the whole loop — a take dictated, posted, live on
fanmoot.com — without creating an account first. So the demo holds one real
Fanmoot account (``FMD_DEMO_FAN_EMAIL``), clearly labelled on screen and to the
model as the demo account, and any visitor can choose to act as it.

**It is linked through the same OAuth flow a visitor goes through**, not given
a token some other way. At first use this module walks ``/authorize`` →
``/oauth/login`` → ``/oauth/consent`` → the redirect → ``/token`` as a
browser would, reading the signed ``flow`` field out of each page and posting it
back. The alternative was an operator linking the account once and pasting a
refresh token into the environment — but the server rotates refresh tokens and
treats a reused one as a replay, so the first restart after the first refresh
would present a spent token and revoke the whole grant. Linking afresh when
needed has no such cliff: a lost grant is re-linked on the next call.

Everything it does is traced to whichever visitor triggered it, like any other
OAuth request, with the address and password struck out.

**Writes are rationed.** The account is shared and public, so a visitor gets
``FMD_DEMO_FAN_WRITES_PER_SESSION`` writes and all visitors together get
``FMD_DEMO_FAN_WRITES_PER_HOUR``. A visitor who wants more signs in as
themselves.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from collections import deque
from urllib.parse import parse_qs, urljoin, urlsplit

from app.agent.oauth import DemoOAuthClient, Grant, GrantHolder, OAuthError
from app.agent.trace import Tracer
from app.logging_config import get_logger
from app.settings import Settings

log = get_logger("agent.demo_fan")

_HIDDEN_FLOW = re.compile(r'<input[^>]*name="flow"[^>]*value="([^"]*)"')
_FORM_ACTION = re.compile(r'<form[^>]*method="post"[^>]*action="([^"]*)"', re.IGNORECASE)
_SCOPE_BOX = re.compile(r'<input[^>]*name="scope"[^>]*value="([^"]*)"')


class DemoFanUnavailable(Exception):
    """The demo account is not configured, or could not be linked."""


class WriteBudget:
    """A sliding one-hour window of writes, shared by every visitor."""

    def __init__(self, per_hour: int) -> None:
        self.per_hour = per_hour
        self._stamps: deque[float] = deque()

    def remaining(self) -> int:
        cutoff = time.time() - 3600
        while self._stamps and self._stamps[0] < cutoff:
            self._stamps.popleft()
        return max(0, self.per_hour - len(self._stamps))

    def spend(self) -> bool:
        if self.remaining() <= 0:
            return False
        self._stamps.append(time.time())
        return True


class DemoFan:
    """The shared account: linked on first use, kept linked, rationed."""

    def __init__(self, settings: Settings, oauth: DemoOAuthClient) -> None:
        self.settings = settings
        self.oauth = oauth
        self.label = settings.demo_fan_label
        self.handle: str | None = None
        self.budget = WriteBudget(settings.demo_fan_writes_per_hour)
        self._holder: GrantHolder | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return self.settings.demo_fan_configured

    async def holder(self, tracer: Tracer) -> GrantHolder:
        if not self.configured:
            raise DemoFanUnavailable("No demo account is configured on this deployment.")
        async with self._lock:
            if self._holder is None or self._holder.grant is None:
                grant = await self._link(tracer)
                self._holder = GrantHolder(self.oauth, grant, relink=self._link)
            return self._holder

    async def _link(self, tracer: Tracer) -> Grant:
        """The authorization-code flow, walked by this server on the account's behalf."""
        url, pending = await self.oauth.begin(tracer)
        try:
            async with self.oauth.http(follow_redirects=False) as http:
                page = await http.get(url)
                self._trace(tracer, "GET /authorize", page.status_code, "the sign-in page")
                login = await http.post(
                    _action(page.text, str(page.url), "/oauth/login"),
                    data={
                        "flow": _flow(page.text),
                        "email": self.settings.demo_fan_email.strip(),
                        "password": self.settings.demo_fan_password,
                    },
                )
                self._trace(
                    tracer, "POST /oauth/login", login.status_code, "signing in the demo fan"
                )
                if login.status_code != 200 or "oauth/consent" not in login.text:
                    raise DemoFanUnavailable("The demo account's sign-in was refused.")

                scopes = [html.unescape(s) for s in _SCOPE_BOX.findall(login.text)]
                consent = await http.post(
                    _action(login.text, str(login.url), "/oauth/consent"),
                    # A list value is sent as the field repeated — one `scope`
                    # per ticked box, as the consent form posts them.
                    data={"flow": _flow(login.text), "decision": "allow", "scope": scopes},
                )
                self._trace(
                    tracer,
                    "POST /oauth/consent",
                    consent.status_code,
                    f"allowing {' '.join(scopes)}",
                )
        except OAuthError:
            raise
        except DemoFanUnavailable:
            raise
        except Exception as exc:
            log.warning("demo_fan_link_failed", error=str(exc)[:200])
            raise DemoFanUnavailable("The demo account could not be linked just now.") from exc

        location = consent.headers.get("location", "")
        query = parse_qs(urlsplit(location).query)
        code = (query.get("code") or [""])[0]
        if consent.status_code not in (302, 303) or not code:
            raise DemoFanUnavailable("The authorization server did not return a code.")
        if (query.get("state") or [""])[0] != pending.state:
            raise DemoFanUnavailable("The authorization came back with the wrong state.")
        grant = await self.oauth.exchange(
            tracer, pending, code=code, iss=(query.get("iss") or [None])[0]
        )
        log.info("demo_fan_linked", scopes=grant.scope, client_mode=grant.client.mode)
        return grant

    @staticmethod
    def _trace(tracer: Tracer, method: str, status: int, what: str) -> None:
        tracer.emit(
            "oauth",
            direction="in",
            method=method,
            status="ok" if status < 400 else "error",
            summary=f"demo fan: {what} — HTTP {status}",
            http_status=status,
        )


def _flow(page: str) -> str:
    match = _HIDDEN_FLOW.search(page)
    if match is None:
        raise DemoFanUnavailable("The authorization server's page had no flow token.")
    return html.unescape(match.group(1))


def _action(page: str, page_url: str, default: str) -> str:
    match = _FORM_ACTION.search(page)
    return urljoin(page_url, html.unescape(match.group(1)) if match else default)
