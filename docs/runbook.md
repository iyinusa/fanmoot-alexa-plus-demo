# fanmoot-alexa-plus-demo runbook

For whoever operates `https://alexaplus.fanmoot.com`. Each section is a task,
in the order you do it. Everything named here is in this repository.

| Task | Section |
|---|---|
| First deploy | [Deploy](#deploy) |
| Is it healthy? | [Check it from outside](#4-check-it-from-outside) |
| Will it hold on judging day? | [Load](#load) |
| What is it costing? | [The cost guard](#the-cost-guard) |
| Bedrock is failing or too dear | [Switch models without a redeploy](#switch-models-without-a-redeploy) |
| Something broke after a deploy | [Roll back](#roll-back) |

---

## What is running

- **One container, one process, one worker.** A visitor's session (their
  WebSocket, their MCP session on `mcp.fanmoot.com`, their conversation and
  any grant) lives in this process's memory. A second worker or replica would
  be a second, disjoint memory, and a socket that reconnects to it would find
  no session. Keep **replicas at 1**. [Load](#load) shows one worker holds
  twenty visitors with room to spare.
- **No database, no volume.** A restart ends every session and revokes every
  visitor's own grant on the way out, so it leaves nothing linked on the MCP
  server. It also resets today's spend counters (see
  [The cost guard](#the-cost-guard)).
- **Traefik in front**, from Dokploy, terminating TLS and routing
  `alexaplus.fanmoot.com` to the `demo` service on port 8090 over
  `dokploy-network`. There is no `ports:` mapping, and there must not be one.
- **Upstream:** `https://mcp.fanmoot.com/mcp` (the MCP server, on the same
  Dokploy host), Amazon Bedrock in `us-east-1`, and Gemini through Google AI
  Studio.

Logs are JSON, one line per request, per trace frame and per turn. Every
`turn_complete` line names the model that answered, its tokens and its
estimated cost. Tokens and email addresses are redacted before a line is
written.

---

## Deploy

### 0. Before you start

- **`mcp.fanmoot.com` is live.** `make smoke` in `fanmoot-mcp` passes. The
  demo is a client of the deployed server and has nothing to show without it.
- **DNS.** `alexaplus.fanmoot.com` has an `A` record pointing at the Dokploy
  host. *Checked 2026-09-24: it resolves to the same address as
  `mcp.fanmoot.com`, and Traefik answers it with its default certificate and a
  404, meaning the record is in place and no router exists yet.*
- **Bedrock model access** in `us-east-1`, in the AWS console under *Bedrock →
  Model access*, for **Amazon Nova Pro** and **Claude Sonnet 4.6**. Anthropic
  models ask for a one-time use-case form first; allow a day for it.
- **An IAM user** for the demo with only this policy. The chain uses
  cross-region inference profiles (`us.…`), which need permission on the
  profile *and* on the foundation model in every region it routes to. The
  second statement is Alexa's voice (Amazon Polly) and ears (Amazon
  Transcribe streaming); neither action takes a resource ARN, hence `*`:

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        "Resource": [
          "arn:aws:bedrock:us-east-1:<ACCOUNT_ID>:inference-profile/us.amazon.nova-pro-v1:0",
          "arn:aws:bedrock:us-east-1:<ACCOUNT_ID>:inference-profile/us.anthropic.claude-sonnet-4-6",
          "arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1*",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6*"
        ]
      },
      {
        "Effect": "Allow",
        "Action": ["polly:SynthesizeSpeech", "transcribe:StartStreamTranscriptionWebSocket"],
        "Resource": "*"
      }
    ]
  }
  ```

  Without the second statement the demo still works: it logs
  `voice_service_refused` once per ten minutes and each visitor's browser
  speaks and listens with its own Web Speech API instead. Fixing the policy
  is picked up within ten minutes, with no redeploy.

- **An AWS Budget** on the account: a monthly cost budget with email alerts at
  50%, 80% and 100%. The demo's own ceilings live in process memory and reset
  on restart. The Budget is the backstop that does not.

### 1. Create the service

In Dokploy, create a **Compose** application from this repository, using
`docker-compose.yml` (never `docker-compose.dev.yml`). Set replicas to 1 and a
**memory limit of 1 GB** ([Load](#load) has the arithmetic).

### 2. Set the environment

In Dokploy's environment tab. Every variable is documented in `.env.example`,
and `docker-compose.yml` already defaults the rest to production values.

| Variable | Value |
|---|---|
| `FMD_ENV` | `production` (the compose default) |
| `FMD_PUBLIC_BASE_URL` | `https://alexaplus.fanmoot.com`. No trailing slash. The WebSocket origin check, the OAuth redirect URI, the OAuth client id and the share tags are all built from it. |
| `FMD_AWS_ACCESS_KEY_ID`, `FMD_AWS_SECRET_ACCESS_KEY` | the IAM user above. Without them the Bedrock rungs are skipped and `make smoke` fails. |
| `FMD_AWS_REGION` | `us-east-1` (the compose default) |
| `FMD_GEMINI_API_KEY` | a Google AI Studio key: the rung that answers when Bedrock cannot. |
| `FMD_VOICE_ID`, `FMD_VOICE_ENGINE` | leave unset: Polly's **Amy** (British English) on the **generative** engine, falling back to neural by itself. `FMD_VOICE_ENABLED=false` hands voice back to each visitor's browser. |
| `FMD_ADMIN_TOKEN` | `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Keep a copy: the operator API and `make smoke`'s cost-guard check need it. |
| `FMD_MODEL_CHAIN` | leave unset. The default is Nova Pro, then Claude on Bedrock, then Gemini. |
| `FMD_OAUTH_CLIENT_MODE` | leave unset (`auto`). On https the demo identifies itself with a client ID metadata document it serves itself, so there is nothing to register on the MCP server. |

The cost guard is on by default. See [The cost guard](#the-cost-guard) before
changing any of `FMD_DAILY_CAP_USD`, `FMD_DAILY_TURN_CAP`,
`FMD_ADDRESS_TURNS_PER_HOUR`, `FMD_PROVIDER_DAILY_CAP_USD` or
`FMD_SESSION_TURN_CAP`.

### 3. Route the domain

In Dokploy's Domains tab: `alexaplus.fanmoot.com` → service `demo`, port
`8090`, HTTPS on, Let's Encrypt certificate.

On the MCP server, `FMCP_MCP_ALLOWED_ORIGINS=https://alexaplus.fanmoot.com`
is in `fanmoot-mcp`'s runbook. The demo holds its MCP sessions server side and
sends no browser `Origin`, so this is belt and braces rather than required.

### 4. Check it from outside

```bash
FMD_ADMIN_TOKEN=… make smoke          # https://alexaplus.fanmoot.com
```

TLS, HSTS and the HTTP redirect; `/healthz` in production with the MCP server
reachable at `2025-11-25` and **a Bedrock model ready**; the share tags
pointing at this origin; the bundle gzipped; the OAuth client metadata; the
sandbox's framing; a foreign `Origin` refused on `/ws`; a real visitor session
opening a real MCP session; and, with the token, the cost guard's ceilings in
force. It asks no question and signs nobody in. Every line must pass;
`WARN` lines are worth reading but do not fail it.

| Failure | Meaning |
|---|---|
| `TLS handshake — self-signed certificate` | Traefik has no router for the domain yet, or Let's Encrypt has not issued. Step 3. |
| `FMD_ENV is production` | The variable is missing. Without it the Vite dev origins are allowed on `/ws`. |
| `the MCP server is reachable…` | `mcp.fanmoot.com` is down, or unreachable from the container. Check its own `make smoke`. |
| `a Bedrock model is ready` | No AWS credentials reached the container. Step 2. |
| `og:url is this origin` | `FMD_PUBLIC_BASE_URL` is wrong. Link previews would point elsewhere. |
| `a visitor's session holds a live MCP session` | The page works and the demo cannot reach the server. The container's log has `mcp_connect_failed` with the reason. |

Then, by hand, the three checks a script cannot make:

1. **Sign in** with a throwaway Fanmoot account through the popup, and post a
   take. It should appear on fanmoot.com under that handle.
2. **Share the link** in Slack, or LinkedIn's post inspector, and check the
   preview card (W5-6).
3. **Lighthouse** on `https://alexaplus.fanmoot.com` in Chrome, mobile and
   desktop (W5-3 measured a local build at 95 or above).

And one for the submission: **`make evidence`** (W1-10), a Bedrock-only run
kept as AWS Builder evidence. Run it from a checkout with the same
`FMD_AWS_*` values in `.env`, so the files land in `evidence/` to be
committed. The container's code tree is read-only to its user, so running it
there needs `--out /tmp/evidence` and a `docker cp` afterwards.

---

## Load

`make load-check URL=https://alexaplus.fanmoot.com` opens twenty visitors at
once, each a `/ws` socket with its own MCP session on `mcp.fanmoot.com`, holds
them for a minute with pings and the ambient ticker running, and reports what a
visitor would feel and, with `FMD_ADMIN_TOKEN` set, the server's session count
and memory. `ARGS="--turns 1"` also has each visitor ask a question. That
spends about a cent a turn, and every visitor shares your address, so keep
visitors × turns under `FMD_ADDRESS_TURNS_PER_HOUR` (60).

**Measured 2026-09-24**, the production command (gunicorn, one worker,
`FMD_ENV=production`) on a laptop, against production `mcp.fanmoot.com`, with
Gemini answering because the laptop has no Bedrock keys:

| | 20 visitors, one turn each |
|---|---|
| Connected, with an MCP session at `2025-11-25` | 20 / 20, none dropped, no error frames |
| First frame after connecting | p50 4 ms · p95 15 ms |
| MCP session ready | p50 2.3 s · p95 2.8 s |
| Ping round trip, all twenty connected | p50 1 ms · p95 3 ms |
| First word of the answer | p50 4.1 s · p95 4.9 s |
| Answer complete | p50 4.3 s · p95 5.0 s, 20 / 20 ok |
| Memory | 262 → 315 MB, about 2.6 MB a visitor |

What the numbers say:

- **One worker is enough.** Twenty turns at once finished within a second of
  one turn alone (4.1 s p50 against about 4 s), and pings stayed at single
  milliseconds, so nothing in this process blocks the event loop. No scale-out.
- **The MCP handshake time is the laptop's, not the demo's.** From that
  laptop a bare TCP connect to the Dokploy host took 0.3–0.9 s and
  `tools/list` (72 KB) up to 2.8 s. In production the two containers share a
  host. Re-measure there; expect it to fall well under a second.
- **Memory:** about 100 MB at boot, about 260 MB once the model libraries are
  loaded, and about 2.6 MB a visitor after that. `FMD_MAX_SESSIONS=200`
  therefore peaks near 800 MB, hence the 1 GB limit. A session outlives its
  socket by `FMD_SESSION_GRACE_S` (5 minutes), so a load check's sessions
  still show in the count just after it ends. That is expected.
- **The first turn after a start was the slow one, and is fixed.** It took
  8–15 s because the model adapters and LiteLLM's price table loaded on first
  use. They now load in a background thread at startup
  (`models.preload`, production only), and a cold first turn measured 4.5 s,
  most of it the provider's own first connection.

**To do after the deploy:** run `make load-check URL=https://alexaplus.fanmoot.com ARGS="--turns 1"`
and put the production numbers beside these.

---

## The cost guard

Five ceilings, each for a way round the one before (W6-3). The defaults come
from `make turn-cost`, which runs the five-turn standing scenario with a real
model and prices the tokens for every model in the chain.

**Measured 2026-09-24:** a turn is 11–17k tokens in and under 100 out. Input
grows about 535 tokens a turn as the conversation lengthens. So a turn costs
about **$0.011 on Nova Pro, $0.044 on Claude Sonnet 4.6 and $0.003 on Gemini
Flash-Lite**, and a session that uses all 40 turns costs about $0.72, $3.00
and $0.23. Tokenizers differ between providers, so read these to ±30%.

| Ceiling | Default | Bounds | When it is reached |
|---|---|---|---|
| `FMD_SESSION_TURN_CAP` | 40 | one tab | "That's all the questions I can take in one visit." |
| `FMD_ADDRESS_TURNS_PER_HOUR` | 60 | one address across reloads, over any 60 minutes | "Give me a little while, then ask again." |
| `FMD_PROVIDER_DAILY_CAP_USD` | `{"bedrock": 30, "gemini": 10}` | one provider, per UTC day | that provider is skipped and the chain moves on |
| `FMD_DAILY_CAP_USD` | 40 | everything, per UTC day | turns are refused until 00:00 UTC |
| `FMD_DAILY_TURN_CAP` | 2,000 | everything, per UTC day, whatever it costs | turns are refused until 00:00 UTC |

$30 of Bedrock is about 2,700 Nova Pro turns or 680 Claude turns. A busy
judging day is a few hundred. So the ceilings only bite on abuse, and the most
a bad day costs is **$40**, plus at most one model call per turn already in
flight when the ceiling is crossed. The dollar ceiling is also checked before
every model call, not only at the start of a turn.

The per-address ceiling keys on the first `X-Forwarded-For` entry, which is
Traefik's record of the client. **Do not add the internet to Traefik's
`forwardedHeaders.trustedIPs`**, or a caller could name their own address.

Read today's position:

```bash
curl -s -H "Authorization: Bearer $FMD_ADMIN_TOKEN" https://alexaplus.fanmoot.com/api/admin/models | jq .usage
```

`usage.total` has today's turns and spend against the demo-wide ceilings, and
`usage.providers` has each provider's calls, tokens, spend and ceiling.
`process.rss_mb` is the container's memory.

**To change a ceiling**, change its variable and redeploy. A redeploy also
resets today's counters and ends every session, so do it when nobody is
mid-conversation. If legitimate traffic is being refused, raise
`FMD_DAILY_CAP_USD` rather than switching the guard off. If Bedrock alone is
the problem, [switch models](#switch-models-without-a-redeploy) instead. That
needs no restart.

Re-measure after any change to the persona instructions, the MCP server's tool
descriptions or the model chain. Each changes the tokens a turn carries:

```bash
make turn-cost                                       # the first rung with credentials
make turn-cost CHAIN=bedrock/us.amazon.nova-pro-v1:0
```

---

## Switch models without a redeploy

```bash
# Gemini first, from the next turn on:
curl -X PUT -H "Authorization: Bearer $FMD_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"chain": ["gemini/gemini-3.1-flash-lite", "bedrock/us.amazon.nova-pro-v1:0"]}' \
  https://alexaplus.fanmoot.com/api/admin/models

# Back to FMD_MODEL_CHAIN:
curl -X DELETE -H "Authorization: Bearer $FMD_ADMIN_TOKEN" https://alexaplus.fanmoot.com/api/admin/models
```

The override is in memory; a restart also goes back to `FMD_MODEL_CHAIN`.
Record the video from a **Bedrock** run (W1-10): check the trace's model fact
says `bedrock/…` before recording.

---

## Roll back

1. In Dokploy, redeploy the previous commit. The image rebuilds from that ref.
2. `make smoke`.

There is no state to migrate. Rolling back ends every visitor's session (they
reconnect to a fresh one) and revokes their own grants. It signs nobody out of
Fanmoot itself.

---

## Traefik notes

Dokploy's defaults are right. The things not to change:

- **WebSockets need nothing extra.** Traefik upgrades them by default. The
  page pings every 15 s, well inside any idle timeout.
- **No buffering or compression middleware** on this router. The app gzips its
  own bundle (W5-3), and a buffering proxy would hold streamed answers.
- **Do not trust `X-Forwarded-For` from the internet** (see the cost guard).
- **HTTP → HTTPS redirect on.** HSTS is sent by the application whenever
  `FMD_PUBLIC_BASE_URL` is https.
- **One replica, no sticky sessions needed.** If this ever scales out, `/ws`
  needs affinity, and the ceilings need a shared store. Neither is worth doing
  for a hackathon.
