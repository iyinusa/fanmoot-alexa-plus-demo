"""Configuration for the Alexa+ demo.

Every value is read from the environment with the ``FMD_`` prefix (so
``mcp_url`` comes from ``FMD_MCP_URL``) and falls back to the default declared
here. A bare ``docker run`` with nothing set boots into a *development* posture;
production is configured through the Dokploy UI, which passes environment
variables rather than writing a file.

The prefix is its own for the same reason ``fanmoot-mcp`` has ``FMCP_``: the
three services are deployed side by side on one host, and a shared prefix would
let one service's variable silently configure another.

**Nothing here is a model credential the browser could see.** The AWS and
Gemini keys are read by the agent runtime on this server and passed to each
model instance directly (``app.agent.models``). They never go into the process
environment, and no frame on ``/ws`` carries them.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Where `make dev-web` serves the SPA. Its WebSocket reaches `/ws` through the
#: Vite proxy carrying *this* origin, so development allows it by default.
VITE_DEV_ORIGINS = ("http://localhost:5174", "http://127.0.0.1:5174")

#: The chain HAD-9 settles on: Amazon's own model first (AWS Builder), Claude on
#: Bedrock second because it handles multi-turn tool use more reliably, and
#: Gemini as a genuine third provider so a Bedrock outage is survivable. The
#: `us.` ids are cross-region inference profiles, which is how Bedrock serves
#: on-demand calls for these models.
DEFAULT_MODEL_CHAIN = (
    "bedrock/us.amazon.nova-pro-v1:0,bedrock/us.anthropic.claude-sonnet-4-6,gemini/gemini-2.5-flash"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FMD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Environment & identity ───────────────────────────────────────────────
    env: str = "development"
    service_name: str = "fanmoot-alexa-plus-demo"

    # Where *this* demo lives. Its origin is the one browser origin `/ws`
    # always accepts, and the OAuth redirect URI is built from it. No trailing
    # slash.
    public_base_url: str = "http://localhost:8090"

    # ── The MCP server this demo drives (HAD-8, HAD-12) ──────────────────────
    # The **deployed** server, by default: the demo exists to show the real
    # thing answering, so a local server is something a developer opts into.
    mcp_url: str = "https://mcp.fanmoot.com/mcp"

    # How long one MCP request may take before the turn gives up on it. The
    # server's own budget is 400 ms p95 (HAD-6); this is the ceiling for a
    # stall, not the expectation.
    mcp_timeout_s: float = 15.0

    # ── /ws ──────────────────────────────────────────────────────────────────
    # Further browser origins allowed to open `/ws`, comma-separated. The
    # demo's own origin is always allowed and a client that sends no `Origin`
    # (a test, a script) is unaffected; this is for a staging host or a
    # preview deploy. A WebSocket is not covered by CORS, so without this check
    # any page on the internet could open a session here and spend this demo's
    # model budget through a visitor's browser.
    ws_allowed_origins: str = ""

    # The largest frame a browser may send. A turn is a sentence a fan said
    # aloud; anything near this size is not one.
    ws_max_frame_bytes: int = 16_384

    # ── Visitor sessions (W1-1) ──────────────────────────────────────────────
    # How many visitors may hold a session at once. Each one holds an MCP
    # session on the server and an agent's history in memory, so this is the
    # memory ceiling rather than a politeness.
    max_sessions: int = 200

    # A dropped socket keeps its session this long, so a phone that loses
    # signal for a moment resumes the conversation — and a fan's sign-in —
    # instead of starting again.
    session_grace_s: int = 300

    # A session nobody has spoken in for this long is closed even while its
    # socket is open: a tab left on a table during judging is not a visitor.
    session_idle_s: int = 3600

    # How many turns one session may take. HAD-9's per-session ceiling: the
    # demo runs on a public URL, and a script looping on `/ws` should run out
    # of turns long before it runs up a bill.
    session_turn_cap: int = 40

    # The largest MCP payload the trace panel receives in full. A `ui://` view
    # is tens of kilobytes of HTML; past this the panel gets a preview and
    # the size.
    trace_max_payload_bytes: int = 24_000

    # ── The Alexa+ shell (W2) ────────────────────────────────────────────────
    # The corner ticker (W2-7): how often a connected visitor's session asks
    # `live_scores` while something is in play. 0 turns the ticker off. The
    # server caches `live_scores` for 10 s, so faster than that buys nothing.
    ambient_interval_s: float = 30.0
    # With nothing in play the ticker shows the next fixtures instead, from
    # `upcoming_matches`, and asks again this often.
    ambient_idle_interval_s: float = 300.0

    # `tools/call`s a card may make through the host in one session (W2-6) —
    # the thread view's reply composer. A write still spends the demo
    # account's allowance as well; this bounds a card that loops.
    view_calls_per_session: int = 30

    # ── The model chain (HAD-9, W1-3) ────────────────────────────────────────
    # Comma-separated LiteLLM model ids, tried in order. The chain is read at
    # the start of every turn, and `PUT /api/admin/models` replaces it in
    # memory, so it can be reordered on a running demo without a redeploy.
    model_chain: str = DEFAULT_MODEL_CHAIN

    # Bedrock. Credentials come from the standard AWS chain — an instance
    # role, `AWS_PROFILE`, or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` —
    # unless the explicit pair below is set, which is then passed to each
    # model instance directly.
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""

    # Gemini, through Google AI Studio (an API key, not Vertex).
    gemini_api_key: str = ""

    # One model call's time limit. A provider that has not answered by then
    # is treated as down and the chain moves on (W1-3b).
    model_timeout_s: float = 20.0
    model_temperature: float = 0.4
    model_max_output_tokens: int = 600

    # Model calls one turn may make. A turn is a question, a few tool calls
    # and an answer; eight is generous, and it stops a model that loops on a
    # tool from looping for ever.
    max_llm_calls_per_turn: int = 8

    # Prices LiteLLM does not know, as JSON: {"model id": [input, output]} in
    # US dollars per million tokens. LiteLLM's own table covers the default
    # chain; this is for a model added later.
    model_prices: str = ""

    # A daily spend ceiling per provider, as JSON in US dollars:
    # {"bedrock": 25, "gemini": 5}. A provider over its ceiling is skipped for
    # the rest of the UTC day, and the chain falls through to the next one.
    # Empty means no ceiling.
    provider_daily_cap_usd: str = ""

    # ── OAuth: how this demo signs a fan in (W1-7) ───────────────────────────
    # How the demo identifies itself to the MCP server's authorization server:
    #   auto               static if FMD_OAUTH_CLIENT_ID is set; otherwise a
    #                      client ID metadata document on an https deployment,
    #                      and Dynamic Client Registration anywhere else.
    #   static             FMD_OAUTH_CLIENT_ID (and secret, if confidential).
    #   metadata_document  SEP-991: this demo serves its own metadata at
    #                      /oauth/client-metadata.json and uses that URL as its
    #                      client id. Needs a public https origin.
    #   dcr                RFC 7591, once per process.
    oauth_client_mode: str = "auto"
    oauth_client_id: str = ""
    oauth_client_secret: str = ""

    # ── The demo fan (W1-8) ──────────────────────────────────────────────────
    # A real Fanmoot account for visitors who will not sign up. The demo links
    # it through the same OAuth flow a visitor goes through, headlessly, at
    # first use. Unset, and the option is simply not offered.
    demo_fan_email: str = ""
    demo_fan_password: str = ""
    # How the account is described on screen and to the model.
    demo_fan_label: str = "Demo fan account"

    # Posts, replies, reactions, predictions and follows the shared account may
    # make: per visitor session, and across all of them per hour. A public
    # account on a public URL needs both.
    demo_fan_writes_per_session: int = 5
    demo_fan_writes_per_hour: int = 30

    # ── Operator ─────────────────────────────────────────────────────────────
    # Bearer token for `/api/admin/*` — the model chain and the spend figures.
    # Unset, and those routes answer 404.
    admin_token: str = ""

    @field_validator("public_base_url", "mcp_url")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("model_prices", "provider_daily_cap_usd")
    @classmethod
    def _json_object_or_empty(cls, value: str) -> str:
        """Refuse malformed JSON at boot, not at the first turn that reads it."""
        if not value.strip():
            return value
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON ({exc.msg} at position {exc.pos})") from None
        if not isinstance(parsed, dict):
            raise ValueError("must be a JSON object")
        return value

    @field_validator("oauth_client_mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        mode = value.strip().lower() or "auto"
        if mode not in {"auto", "static", "metadata_document", "dcr"}:
            raise ValueError("must be one of auto, static, metadata_document, dcr")
        return mode

    @property
    def is_production(self) -> bool:
        return self.env.strip().lower() == "production"

    @property
    def ws_origins(self) -> frozenset[str]:
        """Every browser origin allowed to open ``/ws``."""
        origins = {_origin(self.public_base_url)}
        origins.update(_origin(item) for item in self.ws_allowed_origins.split(",") if item.strip())
        if self.env.strip().lower() == "development":
            origins.update(VITE_DEV_ORIGINS)
        return frozenset(origins)

    @property
    def mcp_health_url(self) -> str:
        """The MCP server's own ``/healthz``, beside its ``/mcp`` endpoint."""
        parts = urlsplit(self.mcp_url)
        return f"{parts.scheme}://{parts.netloc}/healthz"

    @property
    def oauth_redirect_uri(self) -> str:
        """Where the authorization server sends a fan back to (W1-7)."""
        return f"{self.public_base_url}/oauth/callback"

    @property
    def oauth_metadata_document_url(self) -> str:
        """This demo's SEP-991 client id: the URL of its own metadata."""
        return f"{self.public_base_url}/oauth/client-metadata.json"

    @property
    def demo_fan_configured(self) -> bool:
        return bool(self.demo_fan_email.strip() and self.demo_fan_password)

    @property
    def model_price_overrides(self) -> dict[str, tuple[float, float]]:
        """``FMD_MODEL_PRICES`` as {model: (input, output)} per million tokens."""
        out: dict[str, tuple[float, float]] = {}
        for model, pair in _json_object(self.model_prices).items():
            if isinstance(pair, list | tuple) and len(pair) == 2:
                out[str(model)] = (float(pair[0]), float(pair[1]))
        return out

    @property
    def provider_caps(self) -> dict[str, float]:
        """``FMD_PROVIDER_DAILY_CAP_USD`` as {provider: dollars}."""
        return {
            str(provider).strip().lower(): float(cap)
            for provider, cap in _json_object(self.provider_daily_cap_usd).items()
            if isinstance(cap, int | float)
        }


def _json_object(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _origin(url: str) -> str:
    """``scheme://host[:port]`` — the form a browser sends in ``Origin``."""
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
