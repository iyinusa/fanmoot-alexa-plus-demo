# Fanmoot on Alexa+

**Where fans debate football — now by voice.**

A web simulation of Fanmoot running as an Alexa+ experience, live at
**`https://alexaplus.fanmoot.com`**. You ask about a match out loud, and an
agent answers by calling the real [Fanmoot MCP server](https://mcp.fanmoot.com/mcp).
A trace panel beside the conversation shows every MCP frame as it happens.

> **Status: skeleton (W0).** The container, the FastAPI shell, `/ws` and the
> Floodlit-branded SPA are in place. The agent, the Alexa+ shell and the trace
> inspector come next. The sections marked *W1+* below describe them before
> they exist.

## Why a simulation

The Alexa+ Add-on toolkit is partner-gated, and the hackathon organisers have
confirmed it is not available to participants. They asked for a simulated
experience that looks and feels functional. This is one. **The simulation is
only the surface.** The MCP server it calls is real, deployed, and connectable
from Claude, ChatGPT or any other MCP client today. The views it renders are the
server's own `ui://` resources.

This project is not affiliated with, endorsed by or certified by Amazon.

## How it is built

```
browser ──/ws──▶ FastAPI (this repo) ──Streamable HTTP──▶ mcp.fanmoot.com/mcp ──▶ fanmoot.com/v1
                 └ ADK agent (W1)
                   Bedrock first, Gemini fallback
```

- **One image, two stages.** A Node stage builds the React app, and a Python
  stage serves it together with the agent API from one FastAPI process. One
  origin and one route, so there is no CORS.
- **The browser holds nothing sensitive.** Model credentials, the MCP session
  and any OAuth grant stay on the server. The browser gets turns and trace
  frames over `/ws`.
- **Brand from the source.** The Floodlit design tokens and the Lato font files
  are copied unchanged from `fanmoot/web`, and `make brand-check` fails if they
  drift.

## Running it

```bash
make install && cp .env.example .env
make build-web          # the SPA → app/static/web
make dev                # http://localhost:8090
```

For frontend work, run `make dev` and `make dev-web` side by side. Vite serves
on `:5174` with hot reload and proxies `/ws` and `/healthz` to `:8090`.

In Docker, the same stack Dokploy runs:

```bash
make up                 # http://localhost:8090
```

| Command | What it does |
|---|---|
| `make check` | Everything CI runs: ruff, `mypy --strict`, `tsc -b`, pytest |
| `make build-web` | Build the SPA into `app/static/web` |
| `make brand-check` | Diff the Floodlit tokens and fonts against a sibling `fanmoot` checkout |
| `make help` | Every target |

## Configuration

Every setting is an environment variable with the `FMD_` prefix. See
[`.env.example`](.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `FMD_ENV` | `development` | `production` switches to JSON logs |
| `FMD_PUBLIC_BASE_URL` | `http://localhost:8090` | This demo's origin, and the one `/ws` always accepts |
| `FMD_MCP_URL` | `https://mcp.fanmoot.com/mcp` | The MCP server the demo drives |
| `FMD_WS_ALLOWED_ORIGINS` | *(empty)* | More browser origins allowed to open `/ws` |

## Endpoints

| Path | |
|---|---|
| `/` | The demo SPA, with client-side routing fallback |
| `/ws` | The conversation and trace channel. W0 greets with `hello` and answers `ping` |
| `/healthz` | Always 200. Reports the build, the MCP server's reachability and protocol version, and whether the SPA is built |

## Licence

[MIT](LICENSE). Club names and crests belong to their clubs, and Alexa is a
trademark of Amazon.com, Inc. Neither is used here to imply endorsement.
