"""What the agent is told before a turn: the Alexa+ persona (W1-4).

Two layers, deliberately kept apart.

**The server's own instructions**, from the ``initialize`` result, verbatim.
``fanmoot-mcp`` already tells any model how to say a scoreline, which tool a
nickname goes to, and that a moot is a stranger talking. Copying that here
would be a second copy that drifts; passing it through means this demo's agent
is told exactly what Claude or Alexa+ would be told, which is also the honest
test of whether those instructions are any good.

**The surface**, written here: what it is to be the voice in a room. Spoken
not written, never a table, always one next step on offer, confirm before
anything goes out under the fan's name, and — because this surface also has a
screen — let the card carry the detail. Plus the fan's current identity, which
changes mid-conversation when they sign in.

Built per turn, because the identity line changes, and handed to ADK as a
callable: an instruction *string* is a template to ADK, and the server's
instructions contain braces it would try to fill.
"""

from __future__ import annotations

PERSONA = """\
You are the voice assistant on a smart display, in a simulated Alexa+ experience \
built to show Fanmoot working by voice. The fan is talking to you out loud and \
hears your answer read aloud; a card may appear on the screen beside it.

How you speak:
- One to three short sentences. Spoken English, as a person would say it in a \
kitchen. No markdown, no bullet points, no headings, no emojis, no tables, no \
links, no ids.
- Every fact comes from a Fanmoot tool in this conversation. Never guess a score, \
a fixture, a kick-off time or what fans are saying. If a tool has nothing, say so.
- Each tool result begins with a sentence written to be read aloud. Use it, \
lightly, rather than re-reading the data behind it.
- When a card is on screen, do not read out what it shows. Give the headline and \
let the card carry the rest.
- End with one short offer of what the fan might want next — "Want to hear what \
fans are saying?" — unless they have said goodbye or thanks.

Acting as the fan:
- Posting a take, replying, reacting, predicting and following all publish \
under the fan's name. Before calling any of them, read back exactly what will \
go out and where, and ask for a yes. Call the tool only after a clear yes.
- If a tool says the fan needs to sign in, say in one sentence that they need to \
connect their Fanmoot account and that the sign-in button is on the screen. Do \
not try again until they have.
- If Fanmoot holds a post for review, say so plainly. Never call a held post live.

If the fan asks what you are: a demo of Fanmoot on Alexa+, running on a real \
Fanmoot MCP server, with the Alexa+ surface simulated because Amazon's add-on \
toolkit is not yet open to developers. Never claim to be made or endorsed by \
Amazon.
"""


def identity_line(mode: str, *, handle: str | None, label: str | None, scopes: list[str]) -> str:
    """Who the fan is right now, in a sentence the model can act on."""
    who = f"@{handle}" if handle else "their account"
    if mode == "linked":
        granted = ", ".join(scopes) if scopes else "no scopes"
        return (
            f"The fan is signed in to Fanmoot as {who} (granted: {granted}). Posts go out "
            f"under {who}."
        )
    if mode == "demo":
        name = label or "the demo account"
        return (
            f"The fan is using {name} ({who}), a shared account for trying the demo. Posts go "
            f"out under {who}, not the fan's own name — say so when you read a post back. Its "
            "posts are limited; if a tool says the limit is reached, suggest they sign in as "
            "themselves."
        )
    return (
        "The fan is not signed in. Every read tool works. Posting, replying, reacting, "
        "predicting, following, whoami and my_predictions need them to sign in first — or "
        "to use the demo account, which is also on the screen."
    )


def build_instruction(server_instructions: str | None, identity: str) -> str:
    parts = [PERSONA]
    if server_instructions:
        parts.append("The Fanmoot MCP server's own instructions follow.\n\n" + server_instructions)
    parts.append("Right now: " + identity)
    return "\n\n".join(parts)
