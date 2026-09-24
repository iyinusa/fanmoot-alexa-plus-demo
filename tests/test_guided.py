"""W4's server side: the scenario lines, the read-back of a post, sign-up, and reset.

The lines are tested against the days a judge is likeliest to arrive on — the
international break with nothing live, a Saturday with a match in play, a
server with nothing at all — because "What's happening with Arsenal today?" is
the right line on exactly one of them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult, TextContent

from app.agent.oauth import OAuthError, ServerAuth
from app.guided import GuideBoard, Scenario, build, fallback, matches_in
from app.main import create_app
from app.settings import Settings
from tests import fake_mcp
from tests.fake_mcp import RunningServer, match_card
from tests.scripted import ScriptedLlm, call, say
from tests.test_runtime import _session, _signed_in, _turn
from tests.test_ws import until

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
PREFER = ["Premier League", "LaLiga"]


def _card(home: str, away: str, *, hours: float, **extra: Any) -> dict[str, Any]:
    card = match_card(home, away, hours=hours, **extra)
    # Relative to the test's fixed clock, not the machine's.
    card["kickoff_at"] = (NOW + timedelta(hours=hours)).isoformat()
    return card


def _board(
    live: list[dict[str, Any]] | None = None,
    upcoming: list[dict[str, Any]] | None = None,
    recent: list[dict[str, Any]] | None = None,
) -> dict[str, Scenario]:
    scenarios = build(
        live=matches_in({"matches": live or []}),
        upcoming=matches_in({"days": [{"matches": upcoming or []}]}),
        recent=matches_in({"matches": recent or []}),
        now=NOW,
        competitions=PREFER,
    )
    return {scenario.id: scenario for scenario in scenarios}


# ── The lines (W4-1 … W4-6) ──────────────────────────────────────────────────
def test_an_international_break_is_written_from_what_is_actually_on() -> None:
    """The day this was built: nothing live, internationals tonight, and the
    only thread with takes on it a Premier League result from last week."""
    board = _board(
        upcoming=[
            _card("Andorra", "Malta", hours=4, status="scheduled", competition="National Teams"),
            _card(
                "Norway", "Denmark", hours=6.75, status="scheduled", competition="National Teams"
            ),
        ],
        recent=[
            _card("Brighton", "Arsenal", hours=-120, status="finished", score=(3, 0)),
            _card("Brentford", "Chelsea", hours=-137, status="finished", takes=3, score=(3, 0)),
        ],
    )

    assert board["matchday"].steps[0] == "What's happening with Andorra today?"
    # The mood goes where the takes are, not to the newest result.
    assert board["mood"].steps == (
        "What are fans saying about Brentford against Chelsea?",
        "What's the mood overall?",
    )
    assert board["mood"].basis.startswith("Brentford v Chelsea")
    assert board["mood"].basis.endswith("3 takes")
    # Posts on that same thread, and two different takes, not one twice.
    assert board["say"].steps[0] == (
        "Tell the fans on Brentford against Chelsea that Brentford fully deserved that win."
    )
    assert board["new"].steps[0] == (
        "I'm new to Fanmoot. Tell the fans on Brentford against Chelsea that "
        "Chelsea have a lot to fix after that."
    )
    assert board["call"].steps[0] == "Predict Andorra two, Malta one."


def test_a_match_in_play_is_the_matchday() -> None:
    board = _board(
        live=[
            _card("Arsenal", "Spurs", hours=-1, status="live", takes=12, score=(2, 1)),
            _card("Getafe", "Elche", hours=-1, status="live", competition="LaLiga"),
        ]
    )

    assert board["matchday"].steps == (
        "What's the score in Arsenal against Spurs?",
        "What else is on today?",
    )
    assert board["matchday"].basis == "Arsenal v Spurs · Live now · 12 takes"
    assert board["say"].steps[0].endswith("that this one is still wide open.")


def test_a_line_names_the_club_so_it_cannot_be_two() -> None:
    """Found in a real run: "What's happening with Andorra today?" made the
    agent ask whether that meant the national team or FC Andorra. The line
    says the full name; the caption keeps the one a fan says."""
    card = _card("Andorra", "Malta", hours=4, status="scheduled", competition="National Teams")
    card["home"]["name"] = "FC Andorra"
    board = _board(upcoming=[card])

    assert board["matchday"].steps[0] == "What's happening with FC Andorra today?"
    assert board["call"].steps[0] == "Predict FC Andorra two, Malta one."
    assert board["matchday"].basis.startswith("Andorra v Malta")


def test_the_preferred_competition_wins_a_tie() -> None:
    board = _board(
        upcoming=[
            _card("Andorra", "Malta", hours=3, status="scheduled", competition="National Teams"),
            _card("Arsenal", "Chelsea", hours=30, status="scheduled"),
        ]
    )

    # A judge is likelier to know Arsenal than Andorra, even two days out.
    assert board["call"].steps[0] == "Predict Arsenal two, Chelsea one."


def test_a_fixture_about_to_kick_off_is_not_offered_for_a_prediction() -> None:
    """Predictions close at kick-off: one ten minutes away would close mid-tour."""
    board = _board(
        upcoming=[
            _card("Arsenal", "Chelsea", hours=0.15, status="scheduled"),
            _card("Leeds", "Hull", hours=26, status="scheduled"),
        ]
    )

    assert board["call"].steps[0] == "Predict Leeds two, Hull one."


def test_stale_and_unconfirmed_results_are_not_the_argument() -> None:
    board = _board(
        recent=[
            _card("Old", "News", hours=-24 * 30, status="finished", takes=50, score=(1, 0)),
            _card("Nobody", "Watched", hours=-5, status="finished", takes=9),
        ]
    )

    # Neither is a thread to send a judge to: nothing else has takes.
    assert board["mood"].steps == ("What are fans arguing about right now?",)


def test_a_draw_is_taken_as_a_draw() -> None:
    board = _board(
        recent=[_card("Osasuna", "Rayo", hours=-100, status="finished", takes=2, score=(1, 1))]
    )

    assert board["say"].steps[0].endswith("that a draw was about right.")
    assert board["new"].steps[0].endswith("that neither side did enough to win it.")


def test_with_nothing_at_all_the_tour_still_works() -> None:
    board = _board()

    assert board["matchday"].steps == ("What's on this week?",)
    assert board["mood"].steps == ("What are fans arguing about right now?",)
    # The posting scenarios fall back to the add-on's phrase; there is no
    # fixture to predict, so there is no "Call it" rather than a broken one.
    assert board["say"].steps[0] == "I want to tell them Saka was offside"
    assert "call" not in board


def test_the_scenarios_that_need_an_account_say_what_finishes_them() -> None:
    board = {scenario.id: scenario for scenario in fallback()}

    assert board["say"].goal == board["new"].goal == "post_moot"
    assert board["call"].goal == "predict_match"
    assert board["say"].retry and board["call"].retry
    # Only "New here" opens the sign-in on the sign-up form, and only it has
    # an email address to confirm before its first post.
    assert [s.id for s in board.values() if s.signup] == ["new"]
    assert board["new"].retry_refused == "I've confirmed my email. Post it now."
    # And the fixed phrases are the add-on's, word for word (S8-1).
    assert board["matchday"].steps[0] == "What's happening with Arsenal today?"
    assert board["call"].steps[0] == "Predict Arsenal two, Chelsea one"


# ── One reading, shared ──────────────────────────────────────────────────────
class _Connection:
    """Enough of an MCP connection to answer the board's three reads."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str | None]] = []

    async def connect(self) -> None:
        return None

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, origin: str | None = None
    ) -> CallToolResult:
        self.calls.append((name, origin))
        await asyncio.sleep(0.01)
        if self.fail:
            raise RuntimeError("unreachable")
        structured: dict[str, Any] = {"matches": []}
        if name == "list_matches":
            structured = {
                "matches": [
                    match_card(
                        "Brentford", "Chelsea", status="finished", hours=-20, takes=3, score=(3, 0)
                    )
                ]
            }
        return CallToolResult(
            content=[TextContent(type="text", text="ok")], structuredContent=structured
        )


async def test_one_reading_serves_every_visitor_until_it_is_stale() -> None:
    board = GuideBoard(Settings(guide_ttl_s=300))
    first, second = _Connection(), _Connection()

    (a, live_a), (b, live_b) = await asyncio.gather(board.scenarios(first), board.scenarios(second))

    assert live_a and live_b
    assert a == b
    # Two visitors at once, three reads between them, not six — and tagged, so
    # the visitor whose session made them can see why on their trace.
    assert len(first.calls) + len(second.calls) == 3
    assert {origin for _, origin in first.calls + second.calls} == {"guide"}


async def test_a_failed_reading_falls_back_to_the_fixed_phrases() -> None:
    board = GuideBoard(Settings(guide_ttl_s=300))

    scenarios, live = await board.scenarios(_Connection(fail=True))

    assert live is False
    assert [s.steps for s in scenarios] == [s.steps for s in fallback()]


async def test_a_failed_reading_keeps_the_last_good_one(monkeypatch: pytest.MonkeyPatch) -> None:
    board = GuideBoard(Settings(guide_ttl_s=1))
    good, _ = await board.scenarios(_Connection())
    monkeypatch.setattr(board, "_fresh", lambda _now: False)

    kept, live = await board.scenarios(_Connection(fail=True))

    assert live is True
    assert kept == good


async def test_switched_off_it_reads_nothing() -> None:
    connection = _Connection()
    scenarios, live = await GuideBoard(Settings(guide_ttl_s=0)).scenarios(connection)

    assert live is False and connection.calls == []
    assert scenarios == fallback()


# ── Over /ws ─────────────────────────────────────────────────────────────────
@pytest.fixture
def guided_client(configure: Callable[..., Settings], mcp_server: RunningServer) -> TestClient:
    configure(mcp_url=mcp_server.url, guide_ttl_s=300)
    return TestClient(create_app())


def test_a_visitor_is_sent_the_scenarios_once_connected(guided_client: TestClient) -> None:
    with guided_client, guided_client.websocket_connect("/ws") as socket:
        frame = until(socket, "scenarios")

    assert frame["live_data"] is True
    ids = [scenario["id"] for scenario in frame["scenarios"]]
    assert ids == ["matchday", "mood", "say", "new", "call"]
    mood = frame["scenarios"][1]
    # Written from the fake server's one thread with takes on it.
    assert mood["steps"][0] == "What are fans saying about Brentford against Chelsea?"
    assert frame["scenarios"][3]["signup"] is True


def test_the_reading_is_on_the_trace_as_the_guides(guided_client: TestClient) -> None:
    with guided_client, guided_client.websocket_connect("/ws") as socket:
        frames = []
        while True:
            frame = socket.receive_json()
            frames.append(frame)
            if frame["type"] == "scenarios":
                break

    tagged = {
        f["frame"]["tool"]
        for f in frames
        if f["type"] == "trace" and f["frame"].get("origin") == "guide"
    }
    assert tagged == {"live_scores", "upcoming_matches", "list_matches"}


def test_without_live_data_the_fixed_phrases_are_sent(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        frame = until(socket, "scenarios")

    assert frame["live_data"] is False
    assert frame["scenarios"][0]["steps"] == ["What's happening with Arsenal today?"]


# ── W4-4: the post, read back ────────────────────────────────────────────────
async def _drain_until(
    session: Any, predicate: Callable[[dict[str, Any]], bool]
) -> list[dict[str, Any]]:
    queue = session.outbox._queue  # noqa: SLF001
    frames: list[dict[str, Any]] = []
    for _ in range(200):
        try:
            frame = await asyncio.wait_for(queue.get(), timeout=2)
        except TimeoutError:
            break
        frames.append(frame)
        if predicate(frame):
            break
    return frames


async def test_a_live_post_is_read_back_from_fanmoot_beside_the_answer(
    mcp_server: RunningServer,
) -> None:
    llm = ScriptedLlm.of(
        call("post_moot", body="Brentford fully deserved that win."), say("Posted.")
    )
    session = _session(mcp_server, llm)
    _signed_in(session)
    try:
        frames = await _turn(session, "Yes, post it.")
        frames += await _drain_until(session, lambda f: f.get("label") is not None)
    finally:
        await session.close()

    card = next(f for f in frames if f["type"] == "view" and f.get("label"))
    assert card["label"] == "Live on fanmoot.com"
    assert card["link"] == fake_mcp.POSTED_URL
    assert card["uri"] == "ui://fanmoot/thread"
    assert card["tool"] == "read_thread"
    # A second, separate question to Fanmoot, by the take's own address.
    assert card["input"] == {"take": fake_mcp.POSTED_URL, "limit": 3}
    verify = [
        f["frame"] for f in frames if f["type"] == "trace" and f["frame"].get("origin") == "verify"
    ]
    assert {frame["tool"] for frame in verify} == {"read_thread"}


async def test_a_held_post_is_not_shown_as_live(mcp_server: RunningServer) -> None:
    """HAD-7: a take Fanmoot is holding for review is not live, so nothing
    reads it back as though it were."""
    llm = ScriptedLlm.of(call("post_moot", body="Held: my lunch was great."), say("It's held."))
    session = _session(mcp_server, llm)
    _signed_in(session)
    try:
        frames = await _turn(session, "Post about lunch.")
        frames += await _drain_until(session, lambda f: f.get("label") is not None)
    finally:
        await session.close()

    assert not [f for f in frames if f["type"] == "view" and f.get("label")]
    assert "read_thread" not in {f["frame"].get("tool") for f in frames if f["type"] == "trace"}


# ── W4-7: reset ──────────────────────────────────────────────────────────────
async def test_reset_stops_a_read_back_before_it_can_land(mcp_server: RunningServer) -> None:
    """A judge who posts and immediately starts over must not get the old
    conversation's card on the fresh screen."""
    session = _session(mcp_server, ScriptedLlm.of())
    _signed_in(session)
    queue = session.outbox.attach()
    try:
        await session.connection.connect()
        session.show_live({"id": "p1", "url": fake_mcp.POSTED_URL}, turn_id="t1", call_id="t1-0")
        await session.reset()
        await asyncio.sleep(0.3)
    finally:
        await session.close()

    frames = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    types = [f["type"] for f in frames if f["type"] != "trace"]
    assert "session.reset" in types
    after = types[types.index("session.reset") :]
    assert "view" not in after
    # The trace says what happened, so the panel does not look reset by accident.
    resets = [
        f["frame"] for f in frames if f["type"] == "trace" and f["frame"]["method"] == "reset"
    ]
    assert len(resets) == 1
    assert "sign-in is kept" in resets[0]["summary"]


def test_reset_mid_answer_leaves_a_clean_screen_and_fresh_chips(
    configure: Callable[..., Settings], mcp_server: RunningServer
) -> None:
    from tests.test_runtime import _Hanging

    configure(mcp_url=mcp_server.url)
    with TestClient(create_app()) as test_client:
        test_client.app.state.registry.llm_factory = lambda _r, _s: _Hanging()  # type: ignore[attr-defined]
        with test_client.websocket_connect("/ws") as socket:
            until(socket, "scenarios")
            socket.send_json({"type": "turn", "text": "What's the score?"})
            until(socket, "turn.start")
            socket.send_json({"type": "reset"})

            ended = until(socket, "turn.end")
            until(socket, "session.reset")
            state = until(socket, "session")
            chips = until(socket, "scenarios")

    assert ended["status"] == "cancelled"
    assert state["busy"] is False
    # The allowance is not refunded by starting over: it is the cost guard.
    assert state["turns_left"] == Settings().session_turn_cap - 1
    assert chips["scenarios"]


# ── W4-5: sign-up first ──────────────────────────────────────────────────────
def _server_auth(prompts: list[str]) -> ServerAuth:
    return ServerAuth(
        resource="https://mcp.fanmoot.test/mcp",
        issuer="https://mcp.fanmoot.test",
        authorization_endpoint="https://mcp.fanmoot.test/authorize",
        token_endpoint="https://mcp.fanmoot.test/token",
        revocation_endpoint=None,
        registration_endpoint=None,
        scopes=["fan.read", "moot.write"],
        code_challenge_methods=["S256"],
        iss_parameter_supported=True,
        metadata_documents_supported=False,
        prompt_values=prompts,
    )


@pytest.mark.parametrize(
    ("advertised", "signup", "expected"),
    [
        (["create"], True, "prompt=create"),
        (["create"], False, None),
        # A server that never said it honours prompt=create is not sent one.
        ([], True, None),
    ],
)
async def test_signup_asks_for_the_signup_form_only_where_it_is_honoured(
    mcp_server: RunningServer,
    monkeypatch: pytest.MonkeyPatch,
    advertised: list[str],
    signup: bool,
    expected: str | None,
) -> None:
    session = _session(mcp_server, ScriptedLlm.of())
    auth = _server_auth(advertised)

    async def discovered(_tracer: Any) -> ServerAuth:
        return auth

    async def client(_tracer: Any, _auth: ServerAuth) -> Any:
        from app.agent.oauth import RegisteredClient

        return RegisteredClient("demo", "static")

    monkeypatch.setattr(session.oauth, "server_auth", discovered)
    monkeypatch.setattr(session.oauth, "client", client)
    try:
        url = await session.begin_sign_in(signup=signup)
    finally:
        await session.close()

    if expected:
        assert expected in url
    else:
        assert "prompt=" not in url


def test_the_popup_route_passes_signup_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []

    async def begin(self: Any, *, signup: bool = False) -> str:
        seen.append(signup)
        raise OAuthError("unreachable")

    from app.session import VisitorSession

    monkeypatch.setattr(VisitorSession, "begin_sign_in", begin)
    with client.websocket_connect("/ws") as socket:
        ticket_url = until(socket, "session")["sign_in_url"]
        client.get(f"{ticket_url}&signup=1", follow_redirects=False)

    assert seen == [True]
