"""The guided scenarios (W4): five short conversations a visitor starts with one tap.

A scenario is a script for the **fan**, never for Alexa. Each step is a line a
fan would say, and tapping it sends that line as an ordinary ``turn`` frame, so
the agent, its MCP calls and the answer are exactly as real as anything typed
into the box. Nothing in this module answers anything.

What this module decides is **which lines**. The add-on's example phrases
(S8-1) name Arsenal, the derby, Saka and Arsenal v Chelsea, and on most days a
judge visits none of those is on: the international break has no derby, and
"predict Arsenal two, Chelsea one" needs an open Arsenal v Chelsea fixture. A
tour that answered "I can't find that match" would be real, and useless. So the
lines are written from what Fanmoot actually has right now, read over MCP:

* **Matchday** (W4-2) — the match in play, or today's, or the latest result.
* **The mood** (W4-3) — the match with the most takes on it, so there is a
  room to read.
* **Have your say** (W4-4) — a take on that same match, posted for real.
* **New here** (W4-5) — the same, as a brand new fan, from the sign-up form.
* **Call it** (W4-6) — a scoreline for the next fixture that has not kicked off.

Every take the tour offers is plainly about football, because Fanmoot's gate
holds anything it cannot tell is (R8), and a held post is the wrong ending for
the scenario the video is built around.

One reading serves every visitor on the process for ``FMD_GUIDE_TTL_S``: the
data is public, the same for everyone, and three calls per visitor per page
load would be three calls too many. The visitor whose session happens to make
the reading sees it on their trace, tagged ``guide``, as the first visitor in
an hour sees OAuth discovery. When the reading fails, or is switched off, the
add-on's fixed phrases are used, and the frame says so.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent.mcp_client import McpConnection
from app.logging_config import get_logger
from app.settings import Settings

log = get_logger("guided")

#: What the board reads, all public and unauthenticated. `list_matches` has no
#: date filter, so recent results are asked for in bulk and the old ones cut.
_READ_LIVE = ("live_scores", {"limit": 10})
_READ_UPCOMING = ("upcoming_matches", {"days": 14, "limit": 20})
_READ_RECENT = ("list_matches", {"status": "finished", "limit": 20})

#: A result older than this is not "the one fans are still arguing about".
_RECENT = timedelta(days=14)
#: "Today" without knowing the visitor's time zone: the next twelve hours.
_TODAY = timedelta(hours=12)
#: Predictions close at kick-off; one this close would close mid-tour.
_CALL_MARGIN = timedelta(minutes=20)
#: After a failed reading, how long before trying again. Not every page load.
_RETRY_AFTER_S = 60.0


@dataclass(frozen=True)
class Scenario:
    """One guided conversation, as the browser's tour runs it."""

    id: str
    title: str
    #: What it shows, in a line — the chip's tooltip and the tour's caption.
    blurb: str
    #: What the fan says, in order. The tour offers the next after each answer.
    steps: tuple[str, ...]
    #: Why these words today: "Live now", "Last Friday · 3 takes". Empty for
    #: the fixed phrases, which are about nothing in particular.
    basis: str = ""
    #: The tool whose success finishes the scenario — the post, the prediction.
    goal: str | None = None
    #: Said once the fan has signed in, when the goal needed an account.
    retry: str | None = None
    #: Said when the goal was refused for a reason the fan can fix and then
    #: ask again — an unconfirmed email address, above all (W4-5).
    retry_refused: str | None = None
    #: The sign-in this scenario leads to opens on the sign-up form (W4-5).
    signup: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "blurb": self.blurb,
            "steps": list(self.steps),
            "basis": self.basis,
            "goal": self.goal,
            "retry": self.retry,
            "retry_refused": self.retry_refused,
            "signup": self.signup,
        }


# ── The fixed phrases ────────────────────────────────────────────────────────
def fallback() -> list[Scenario]:
    """The add-on's example phrases (S8-1), word for word, when there is no
    reading of what is on. Real turns still — they may just find less."""
    return [
        Scenario(
            "matchday",
            "Matchday",
            "A fixture, a live score, and the scoreboard card",
            ("What's happening with Arsenal today?",),
        ),
        Scenario(
            "mood",
            "The mood",
            "What fans are saying, and the mood of the thread",
            ("What are fans saying about the derby?", "What's the mood overall?"),
        ),
        _have_your_say("I want to tell them Saka was offside"),
        _new_here("I'm new to Fanmoot. I want to tell them Saka was offside"),
        _call_it("Predict Arsenal two, Chelsea one"),
    ]


def _have_your_say(line: str, basis: str = "") -> Scenario:
    return Scenario(
        "say",
        "Have your say",
        "Sign in for real, post a take, and see it live on fanmoot.com",
        (line, "Yes, post it."),
        basis=basis,
        goal="post_moot",
        retry="Post it now.",
        retry_refused="Try posting it again.",
    )


def _new_here(line: str, basis: str = "") -> Scenario:
    return Scenario(
        "new",
        "New here",
        "Create a Fanmoot account inside the sign-in, then post",
        (line, "Yes, post it."),
        basis=basis,
        goal="post_moot",
        retry="I've signed up. Post it now.",
        retry_refused="I've confirmed my email. Post it now.",
        signup=True,
    )


def _call_it(line: str, basis: str = "") -> Scenario:
    return Scenario(
        "call",
        "Call it",
        "Predict a scoreline before kick-off",
        (line, "Yes, lock it in."),
        basis=basis,
        goal="predict_match",
        retry="Lock it in now.",
        retry_refused="Try that prediction again.",
    )


# ── Written from what is on ──────────────────────────────────────────────────
@dataclass
class _Match:
    """The few things a scenario line needs from a MatchCard.

    ``home`` and ``away`` are the clubs' full names, which is what every line
    says. A short name is what a fan says, but it is not always one club:
    "Andorra" is a national team *and* FC Andorra, and a line that makes the
    agent ask "which one?" is the right answer to the wrong line. The short
    names are only for the caption a person reads.
    """

    home: str
    away: str
    home_short: str
    away_short: str
    status: str
    kickoff: datetime
    kickoff_spoken: str
    competition: str
    takes: int
    home_score: int | None
    away_score: int | None
    score_confirmed: bool

    @classmethod
    def of(cls, card: Any) -> _Match | None:
        if not isinstance(card, dict):
            return None
        home, away = card.get("home"), card.get("away")
        if not isinstance(home, dict) or not isinstance(away, dict):
            return None
        try:
            kickoff = datetime.fromisoformat(str(card.get("kickoff_at")).replace("Z", "+00:00"))
        except ValueError:
            return None
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=UTC)
        home_name, away_name = _name(home), _name(away)
        if not home_name or not away_name:
            return None
        return cls(
            home=home_name,
            away=away_name,
            home_short=_short(home) or home_name,
            away_short=_short(away) or away_name,
            status=str(card.get("status") or ""),
            kickoff=kickoff,
            kickoff_spoken=str(card.get("kickoff_spoken") or ""),
            competition=str(card.get("competition") or ""),
            takes=int(card.get("take_count") or 0),
            home_score=_score(home.get("score")),
            away_score=_score(away.get("score")),
            score_confirmed=bool(card.get("score_confirmed", True)),
        )

    @property
    def fixture(self) -> str:
        return f"{self.home} against {self.away}"


def _name(side: dict[str, Any]) -> str:
    """The club's name as Fanmoot has it, which resolves to one club."""
    return str(side.get("name") or side.get("short_name") or "").strip()


def _short(side: dict[str, Any]) -> str:
    """What a fan calls the club: "Spurs", not "Tottenham Hotspur"."""
    return str(side.get("short_name") or "").strip()


def _score(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def matches_in(structured: dict[str, Any]) -> list[_Match]:
    """Every match in a tool's answer, flat — `upcoming_matches` groups by day."""
    cards: list[Any] = list(structured.get("matches") or [])
    for day in structured.get("days") or []:
        if isinstance(day, dict):
            cards.extend(day.get("matches") or [])
    return [match for card in cards if (match := _Match.of(card)) is not None]


def _rank(competitions: list[str], match: _Match) -> int:
    """How much a scenario prefers this match's competition; lower is better."""
    name = match.competition.lower()
    for index, preferred in enumerate(competitions):
        if preferred.lower() in name:
            return index
    return len(competitions)


def build(
    *,
    live: list[_Match],
    upcoming: list[_Match],
    recent: list[_Match],
    now: datetime,
    competitions: list[str],
) -> list[Scenario]:
    """The five scenarios, written from one reading of what is on. Pure, so the
    choices can be tested against a Tuesday in the international break as
    easily as a Saturday at quarter to five."""

    def rank(match: _Match) -> int:
        return _rank(competitions, match)

    playing = [m for m in live if m.status == "live"] or live
    scheduled = [m for m in upcoming if m.status == "scheduled" and m.kickoff > now]
    finished = [
        m
        for m in recent
        if m.status == "finished" and m.score_confirmed and now - m.kickoff < _RECENT
    ]

    # The match there is most to say about: takes first, then the competition a
    # judge is likelier to know, then the most recent.
    talked = max(
        (m for m in [*playing, *finished, *scheduled] if m.takes > 0),
        key=lambda m: (m.takes, -rank(m), m.kickoff),
        default=None,
    )

    scenarios = [_matchday(playing, scheduled, finished, now=now, rank=rank)]
    scenarios.append(_mood(talked))

    subject = talked or _first(playing, rank) or _latest(finished, rank) or _first(scheduled, rank)
    if subject is None:
        fixed = {scenario.id: scenario for scenario in fallback()}
        scenarios += [fixed["say"], fixed["new"]]
    else:
        basis = _basis(subject)
        scenarios.append(
            _have_your_say(f"Tell the fans on {subject.fixture} that {_take(subject)}.", basis)
        )
        scenarios.append(
            _new_here(
                f"I'm new to Fanmoot. Tell the fans on {subject.fixture} that "
                f"{_other_take(subject)}.",
                basis,
            )
        )

    callable_ = [m for m in scheduled if m.kickoff - now > _CALL_MARGIN]
    fixture = min(callable_, key=lambda m: (rank(m), m.kickoff), default=None)
    if fixture is not None:
        scenarios.append(
            _call_it(f"Predict {fixture.home} two, {fixture.away} one.", _basis(fixture))
        )
    return scenarios


def _first(matches: list[_Match], rank: Callable[[_Match], int]) -> _Match | None:
    return min(matches, key=lambda m: (rank(m), m.kickoff), default=None)


def _latest(matches: list[_Match], rank: Callable[[_Match], int]) -> _Match | None:
    return min(matches, key=lambda m: (rank(m), -m.kickoff.timestamp()), default=None)


def _matchday(
    playing: list[_Match],
    scheduled: list[_Match],
    finished: list[_Match],
    *,
    now: datetime,
    rank: Callable[[_Match], int],
) -> Scenario:
    """W4-2: a fixture, a score and the scoreboard — whatever the day offers."""
    blurb = "A fixture, a score, and the scoreboard card"
    if (match := _first(playing, rank)) is not None:
        steps = (f"What's the score in {match.fixture}?", "What else is on today?")
    elif (match := _first([m for m in scheduled if m.kickoff - now < _TODAY], rank)) is not None:
        steps = (f"What's happening with {match.home} today?", "Who else is playing today?")
    elif (match := _latest(finished, rank)) is not None:
        steps = (
            f"How did {match.home} get on against {match.away}?",
            f"When do {match.home} play next?",
        )
    elif (match := _first(scheduled, rank)) is not None:
        steps = (f"When do {match.home} play next?", "What else is on this week?")
    else:
        return Scenario("matchday", "Matchday", blurb, ("What's on this week?",))
    return Scenario("matchday", "Matchday", blurb, steps, basis=_basis(match))


def _mood(talked: _Match | None) -> Scenario:
    """W4-3: the room, then its mood, on the match with the most to read."""
    blurb = "What fans are saying, and the mood of the thread"
    if talked is None:
        # Nothing has a thread worth reading: the argument across the site.
        return Scenario("mood", "The mood", blurb, ("What are fans arguing about right now?",))
    return Scenario(
        "mood",
        "The mood",
        blurb,
        (f"What are fans saying about {talked.fixture}?", "What's the mood overall?"),
        basis=_basis(talked),
    )


def _take(match: _Match) -> str:
    """A take on the match, plainly about football so the gate lets it through."""
    if match.status == "finished" and match.home_score is not None and match.away_score is not None:
        if match.home_score == match.away_score:
            return "a draw was about right"
        winner = match.home if match.home_score > match.away_score else match.away
        return f"{winner} fully deserved that win"
    if match.status == "live":
        return "this one is still wide open"
    return f"{match.home} will win this one"


def _other_take(match: _Match) -> str:
    """A different take on the same match, so two scenarios make two posts."""
    if match.status == "finished" and match.home_score is not None and match.away_score is not None:
        if match.home_score == match.away_score:
            return "neither side did enough to win it"
        loser = match.away if match.home_score > match.away_score else match.home
        return f"{loser} have a lot to fix after that"
    if match.status == "live":
        return "whoever scores next wins this"
    return f"{match.away} will make this harder than people think"


def _basis(match: _Match) -> str:
    """Why this match: when it is, and how much is being said about it."""
    when = "Live now" if match.status == "live" else match.kickoff_spoken.strip()
    when = when[:1].upper() + when[1:] if when else ""
    parts = [f"{match.home_short} v {match.away_short}"]
    if when:
        parts.append(when)
    if match.takes:
        parts.append(f"{match.takes} take{'s' if match.takes != 1 else ''}")
    return " · ".join(parts)


# ── One reading, shared ──────────────────────────────────────────────────────
@dataclass
class _Reading:
    at: float
    scenarios: list[Scenario]
    live_data: bool
    failed: bool = False


class GuideBoard:
    """The scenarios for every visitor on this process, read at most once per
    ``FMD_GUIDE_TTL_S`` over whichever visitor's MCP session asks first."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._reading: _Reading | None = None
        self._lock = asyncio.Lock()

    @property
    def competitions(self) -> list[str]:
        names = self.settings.guide_competitions.split(",")
        return [name.strip() for name in names if name.strip()]

    def _fresh(self, now: float) -> bool:
        reading = self._reading
        if reading is None:
            return False
        ttl = _RETRY_AFTER_S if reading.failed else self.settings.guide_ttl_s
        return now - reading.at < ttl

    async def scenarios(self, connection: McpConnection) -> tuple[list[Scenario], bool]:
        """The scenarios, and whether they were written from live data."""
        if self.settings.guide_ttl_s <= 0:
            return fallback(), False
        reading = self._reading
        if reading is None or not self._fresh(time.monotonic()):
            async with self._lock:
                # Another visitor may have read while this one waited.
                reading = self._reading
                if reading is None or not self._fresh(time.monotonic()):
                    reading = self._reading = await self._read(connection)
        return reading.scenarios, reading.live_data

    async def _read(self, connection: McpConnection) -> _Reading:
        now = time.monotonic()
        try:
            await connection.connect()
            answers = await asyncio.gather(
                *(
                    connection.call_tool(name, dict(args), origin="guide")
                    for name, args in (_READ_LIVE, _READ_UPCOMING, _READ_RECENT)
                )
            )
        except Exception as exc:
            log.info("guide_read_failed", error=str(exc)[:200])
            return self._keep_or_fall_back(now)
        if any(answer.isError for answer in answers):
            log.info("guide_read_refused")
            return self._keep_or_fall_back(now)
        live, upcoming, recent = (matches_in(answer.structuredContent or {}) for answer in answers)
        scenarios = build(
            live=live,
            upcoming=upcoming,
            recent=recent,
            now=datetime.now(UTC),
            competitions=self.competitions,
        )
        log.info("guide_read", scenarios=[s.id for s in scenarios], live=len(live))
        return _Reading(at=now, scenarios=scenarios, live_data=True)

    def _keep_or_fall_back(self, now: float) -> _Reading:
        """A failed reading keeps the last good one rather than going back to
        phrases about Arsenal, and is retried in a minute, not per visitor."""
        previous = self._reading
        if previous is not None and previous.live_data:
            return _Reading(at=now, scenarios=previous.scenarios, live_data=True, failed=True)
        return _Reading(at=now, scenarios=fallback(), live_data=False, failed=True)


__all__ = ["GuideBoard", "Scenario", "build", "fallback", "matches_in"]
