"""The standing scenario: one multi-turn matchday conversation (W1-9, W1-10).

The same five turns serve two purposes:

* ``tests/test_scenario_live.py`` drives them against the **deployed** server
  with a scripted model, asserting the tool calls in order (W1-9) — so a change
  to ``fanmoot-mcp`` that breaks the demo fails a test, not a judge's session.
* ``scripts/capture_evidence.py`` drives them with a **real Bedrock model**
  and keeps the transcript, model ids and MCP frames as standing AWS Builder
  evidence (W1-10).

It follows the story in §1 of the plan: what's on, a nickname, the argument,
then a take the fan wants to post. Nothing is written to Fanmoot: the session
is not signed in, so the post ends at the server's 401 and a sign-in prompt —
which is itself the most interesting frame in the trace. The visitor-facing
guided scenarios are W4's, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScenarioTurn:
    text: str
    #: What a scripted model calls for this turn, in order: (tool, arguments).
    #: A real model chooses for itself; this is the path a correct one takes.
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: What a scripted model then says.
    reply: str = ""


MATCHDAY: list[ScenarioTurn] = [
    ScenarioTurn(
        "Who's playing in the next few days?",
        [("upcoming_matches", {"days": 3, "limit": 3})],
        "Here's what's coming up. Want to hear about any of them?",
    ),
    ScenarioTurn(
        "Who are the gunners?",
        [("find_club", {"name": "the gunners"})],
        "That's Arsenal. Want their next fixture?",
    ),
    ScenarioTurn(
        "What are fans arguing about right now?",
        [("trending_takes", {"limit": 2})],
        "A couple of takes are doing the rounds. Want to weigh in?",
    ),
    ScenarioTurn(
        "Tell them the ref had a shocker.",
        [],
        'I\'d post: "The ref had a shocker." Shall I post that?',
    ),
    ScenarioTurn(
        "Yes, post it.",
        [("post_moot", {"body": "The ref had a shocker."})],
        "You'll need to connect your Fanmoot account first. The sign-in button is on screen.",
    ),
]
