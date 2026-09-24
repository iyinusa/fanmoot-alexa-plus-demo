# Fanmoot on Alexa+

**Where fans debate football — now by voice.**

A web simulation of Fanmoot running as an Alexa+ experience, live at
**`https://alexaplus.fanmoot.com`**. You ask about a match, and an agent answers
by calling the real [Fanmoot MCP server](https://mcp.fanmoot.com/mcp). A trace
beside the conversation shows every MCP frame, OAuth request and model call as
it happens.

> **Status: polished (W5), deployment (W6) next.** A real ADK agent, Bedrock
> first, drives a real MCP session against `mcp.fanmoot.com` and signs fans in
> through a real OAuth 2.1 flow. The browser shows it as a smart display: voice
> in and out, captions, the server's own cards, and live scores in the corner.
> Beside it, the trace inspector shows every frame of that session. Guided
> scenarios, written each day from what is actually on, walk a visitor through
> it. Lighthouse scores 95 or more in every category on a throttled phone
> (measured against a local production build; to re-check once deployed).

## Why a simulation

The Alexa+ Add-on toolkit is partner-gated, and the hackathon organisers have
confirmed it is not available to participants. They asked for a simulated
experience that looks and feels functional. This is one. **The simulation is
only the surface.** The MCP server it calls is real, deployed, and connectable
from Claude, ChatGPT or any other MCP client today. The agent's answers are
real model output, not a script.

This project is not affiliated with, endorsed by or certified by Amazon.

## How it is built

```text
browser ──/ws──▶ FastAPI (this repo) ──Streamable HTTP──▶ mcp.fanmoot.com/mcp ──▶ fanmoot.com/v1
                 ├ ADK agent, per turn         ▲
                 │  Bedrock → Bedrock → Gemini │ OAuth 2.1 + PKCE S256
                 ├ one MCP session per visitor ┘ (the sign-in popup)
                 └ the trace: every frame, redacted
```

- **The tools come from the server.** Each turn, the agent is given exactly
  the tools `tools/list` returned, with the server's own descriptions and JSON
  Schemas. Nothing about a tool is written in this repository, so the demo
  cannot drift from the server.
- **One real MCP session per visitor**, with the official `mcp` client SDK,
  negotiating protocol `2025-11-25`. When the server refuses a call with a 401
  challenge, the visitor gets a "Sign in to Fanmoot" button.
- **A switchable model chain.** `FMD_MODEL_CHAIN` lists models in the order
  they are tried, and no provider is preferred over another: put Gemini first
  and Gemini answers. The default, when it is unset, is Amazon Nova Pro on
  Bedrock, then Claude on Bedrock, then Gemini. A provider that errors,
  throttles or times out falls through to the next one for that model call. The chain is read every turn and can be reordered on a running
  demo (see [Operator API](#operator-api)). Every call's tokens and estimated
  cost are counted per provider, with optional daily ceilings.
- **A real sign-in.** "Sign in to Fanmoot" opens the MCP server's own
  `/authorize` in a popup. The demo discovers the authorization server
  (RFC 9728, RFC 8414), sends PKCE S256, `state` and a `resource` indicator,
  checks `iss` on the way back, and redeems the code on the server. The browser
  never sees a token.
- **The browser holds nothing sensitive.** Model credentials, the MCP session
  and any grant stay in this process. The browser gets turns and trace frames
  over `/ws`, with tokens and email addresses struck out before they are sent.
- **The cards are the server's own.** When a tool has a view, the demo reads
  the `ui://` resource over MCP and runs it as an MCP Apps host would
  (`2026-01-26`). The view runs in `/sandbox`, a proxy framed without
  `allow-same-origin`, so it gets an opaque origin of its own. Inside it, a CSP
  built from the resource's declared domains applies. The thread card's reply
  box calls `reply_to_moot` through the host bridge, on the visitor's own MCP
  session and grant.
- **Voice, from the browser.** Speech in uses the Web Speech API, with a typed
  field for browsers without it. Answers are spoken sentence by sentence as
  they stream, with a captions track that follows the synthesiser's word
  boundaries. The waveform shows the microphone's real spectrum while
  listening.
- **The trace inspector.** The column beside the device (a sheet on a phone)
  opens with five facts, each read from the live session: the protocol
  version `initialize` negotiated, the transport and its `Mcp-Session-Id`, the
  PKCE method the authorization server advertises (or the grant the session
  holds), the server's `serverInfo`, and the model that answered last. Below
  them, each request and its response are one row, with direction, status and
  round trip. Tool calls are graded against a 400 ms budget. Open a row for the
  pretty-printed JSON-RPC payload. **Copy as cURL** gives a script that opens
  its own session on `mcp.fanmoot.com` and sends the same request, so anything
  on the trace can be reproduced from a terminal.
- **Live scores before anyone speaks.** While a visitor is connected, their
  session calls `live_scores` on a timer. With nothing in play it calls
  `upcoming_matches` instead. These calls appear on the trace marked
  `ambient`, and calls made by a card are marked `view`.
- **A first screen that explains itself.** A first visit opens on the
  device's own welcome screen: what the demo is, that everything but the
  device is real (with the live connection to `mcp.fanmoot.com` shown under
  it), and where the trace is. "Take the tour" starts the first guided
  scenario as a real turn. The ⓘ button in the header brings the screen back.
- **Failure in the device's own voice.** A turn that fails is answered with an
  apology Alexa says out loud, and the error code sits in small print under
  it. Offline, disconnected, out of date, timed out or full, the device shows
  a screen of its own, with the light pulsing amber, a countdown and a way on.
  A render error shows a restart screen, never a blank page.
- **Keyboard and screen reader.** Everything is reachable by Tab, with a skip
  link first and `/` to jump to the question box. Tabs take arrow keys, the
  trace sheet keeps focus inside it and returns it on close, and each finished
  answer is announced once rather than word by word. Captions are on by
  default. Reduced motion stops every animation, including the waveform and
  the light.
- **Fast on a phone, and kept that way.** The bundle is gzipped on the way
  out. The trace inspector, the cards and Framer Motion's layout and drag
  features load after the first paint. A Vite plugin (`web/budget.ts`) fails
  any build whose critical-path JavaScript goes over 100 KB gzipped, or any
  chunk over 60 KB: on a laptop, in CI and in the Docker image alike.
- **A link that looks deliberate.** Open Graph and X card tags, with the
  image's absolute URL written from `FMD_PUBLIC_BASE_URL` as the page is
  served, so staging previews point at staging. `make share-image` redraws
  the 1200 × 630 image from an HTML template in the brand's own colours and
  fonts, using headless Chrome.
- **One image, two stages.** A Node stage builds the React app, and a Python
  stage serves it with the agent from one FastAPI process. One origin, one
  route, no CORS.
- **Brand from the source.** The Floodlit design tokens and the Lato font files
  are copied unchanged from `fanmoot/web`, and `make brand-check` fails if they
  drift.

## Running it

```bash
make install && cp .env.example .env
# Add model credentials to .env: FMD_AWS_* for Bedrock, FMD_GEMINI_API_KEY for Gemini.
make build-web          # the SPA → app/static/web
make dev                # http://localhost:8090
```

Without model credentials the demo still starts and connects to the MCP
server. Each turn then ends with a clear "no model could answer" message, and
the trace shows which models were skipped and why.

For frontend work, run `make dev` and `make dev-web` side by side. Vite serves
on `:5174` with hot reload and proxies `/ws`, `/oauth` and `/healthz` to `:8090`.

In Docker, the same stack Dokploy runs:

```bash
make up                 # http://localhost:8090
```

| Command | What it does |
|---|---|
| `make check` | Everything CI runs: ruff, `mypy --strict`, `tsc -b`, pytest |
| `make test-live` | The multi-turn scenario against the deployed MCP server. Add `FMD_LIVE_MODEL=1` for a real-model run |
| `make evidence` | A Bedrock-only run of that scenario, saved to `evidence/` |
| `make build-web` | Build the SPA into `app/static/web`. Fails if the JavaScript is over budget |
| `make bundle-budget` | Build, and print the gzipped JavaScript against its budget |
| `make share-image` | Redraw the link-preview image, `web/public/share/og.png`, with headless Chrome |
| `make brand-check` | Diff the Floodlit tokens and fonts against a sibling `fanmoot` checkout |
| `make help` | Every target |

### Tests

`make test` runs offline. It starts a real Streamable HTTP MCP server on a
loopback port (built with the SDK's own FastMCP, with a 401/403 gate like the
real server's), fakes the authorization server at the HTTP layer, and scripts
the model. The agent, the failover chain, the MCP client, the OAuth client and
ADK all run for real.

`make test-live` drives five turns against `https://mcp.fanmoot.com/mcp` and
checks the tool calls in order, the `2025-11-25` handshake, and that an
unauthenticated post is refused with a 401 challenge. Nothing is posted.

## Configuration

Every setting is an environment variable with the `FMD_` prefix. See
[`.env.example`](.env.example) for all of them. The ones you are most likely to
set:

| Variable | Default | Meaning |
|---|---|---|
| `FMD_PUBLIC_BASE_URL` | `http://localhost:8090` | This demo's origin. `/ws` accepts it, and the OAuth redirect URI is built from it |
| `FMD_MCP_URL` | `https://mcp.fanmoot.com/mcp` | The MCP server the demo drives |
| `FMD_MODEL_CHAIN` | Nova Pro, Claude, Gemini | LiteLLM model ids, tried in the order given. `bedrock/…` and `gemini/…` prefixes are added to bare Bedrock and Gemini ids (`gemini-3.1-flash-lite` → `gemini/gemini-3.1-flash-lite`) |
| `FMD_AWS_REGION` | `us-east-1` | Bedrock region. Enable each model in the Bedrock console for it |
| `FMD_AWS_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` | *(empty)* | Bedrock keys. Empty means the standard AWS credential chain |
| `FMD_GEMINI_API_KEY` | *(empty)* | Google AI Studio key. Empty skips the Gemini rung |
| `FMD_PROVIDER_DAILY_CAP_USD` | *(empty)* | Daily spend ceiling per provider, e.g. `{"bedrock": 25, "gemini": 5}` |
| `FMD_OAUTH_CLIENT_MODE` | `auto` | How the demo identifies itself to the authorization server. See below |
| `FMD_DEMO_FAN_EMAIL` / `_PASSWORD` | *(empty)* | A real Fanmoot account visitors may post as. Empty hides the option |
| `FMD_ADMIN_TOKEN` | *(empty)* | Bearer token for `/api/admin/models`. Empty disables it |
| `FMD_AMBIENT_INTERVAL_S` | `30` | Seconds between the ticker's `live_scores` calls. `0` turns the ticker off |
| `FMD_GUIDE_TTL_S` | `300` | Seconds the guided scenarios' reading of what is on is kept. `0` uses fixed phrases |
| `FMD_GUIDE_COMPETITIONS` | Premier League, … | Competitions a scenario prefers, most preferred first |

### How the demo signs in as a client

With `FMD_OAUTH_CLIENT_MODE=auto`:

- On an `https` deployment, the demo uses a **client ID metadata document**
  (SEP-991, recommended by the `2025-11-25` spec). Its `client_id` is the URL
  of `/oauth/client-metadata.json`, which it serves itself. Nothing needs
  registering and there is no secret.
- On `http://localhost`, the authorization server cannot fetch that document,
  so the demo registers itself with **Dynamic Client Registration** (RFC 7591),
  once per process.
- With `FMD_OAUTH_CLIENT_ID` set, it uses that **static client**.

### The demo fan account

For visitors who do not want to sign up, the demo can act as one shared, real
Fanmoot account. It is labelled as the demo account on screen and to the model.
The demo links it through the same OAuth flow a visitor uses, at first use, and
re-links it if the grant is lost. Its writes are limited per session
(`FMD_DEMO_FAN_WRITES_PER_SESSION`) and per hour across all visitors
(`FMD_DEMO_FAN_WRITES_PER_HOUR`).

## Endpoints

| Path | |
|---|---|
| `/` | The demo SPA, with client-side routing fallback |
| `/ws` | The conversation and trace channel ([protocol](app/ws.py)) |
| `/oauth/start` | Opened by the sign-in popup. Starts a PKCE authorization and redirects to the MCP server's `/authorize` |
| `/oauth/callback` | Where the fan returns. The code is redeemed here, server-side |
| `/oauth/client-metadata.json` | This demo's OAuth client metadata (SEP-991) |
| `/linked` | The popup's last page. Tells the opener and closes |
| `/sandbox` | The MCP Apps sandbox proxy the server's `ui://` views run in. Only this origin may frame it |
| `/api/admin/models` | The model chain and today's spend. Needs `FMD_ADMIN_TOKEN` |
| `/healthz` | Always 200. Reports the build, the MCP server's reachability and protocol version, the model chain and whether the SPA is built |

## Operator API

Read the chain and today's usage per provider:

```bash
curl -H "Authorization: Bearer $FMD_ADMIN_TOKEN" https://alexaplus.fanmoot.com/api/admin/models
```

Put Gemini first, from the next turn on, without a redeploy:

```bash
curl -X PUT -H "Authorization: Bearer $FMD_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"chain": ["gemini/gemini-3.1-flash-lite", "bedrock/us.amazon.nova-pro-v1:0"]}' \
  https://alexaplus.fanmoot.com/api/admin/models
```

`DELETE` on the same URL goes back to `FMD_MODEL_CHAIN`. The override is held in
memory, so a restart also goes back to it.

## AWS Builder evidence

`make evidence` runs the standing five-turn scenario against the live MCP server
with the model chain cut to Bedrock only. It writes the full frame transcript
and a readable summary (the Bedrock model that answered each turn, tokens,
estimated cost and tools called) to [`evidence/`](evidence/). It refuses to
write anything if there are no AWS credentials or if any turn was answered by a
model that is not on Bedrock.

## Licence

[MIT](LICENSE). Club names and crests belong to their clubs, and Alexa is a
trademark of Amazon.com, Inc. Neither is used here to imply endorsement.
