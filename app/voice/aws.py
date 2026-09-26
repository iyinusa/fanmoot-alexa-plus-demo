"""What Polly and Transcribe share: credentials, and knowing when AWS said no.

The credentials are Bedrock's (``FMD_AWS_*``, or the standard AWS chain), so a
deployment that answers with Nova Pro can speak without a second secret. They
never leave this process: Polly is called with boto3, and Transcribe's stream is
opened by this server with a URL it signs itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.logging_config import get_logger
from app.settings import Settings

log = get_logger("voice")

#: How long a service that refused us for good reason — no permission, no
#: credentials — is left alone before it is tried again. Long enough not to ask
#: on every sentence, short enough that fixing the IAM policy is noticed within
#: a few minutes, without a redeploy.
OUTAGE_S = 600.0


class VoiceUnavailable(Exception):
    """Polly or Transcribe cannot be used just now. The browser falls back."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AwsKeys:
    access_key: str
    secret_key: str
    session_token: str | None


_chain: Any = None


def aws_keys(settings: Settings) -> AwsKeys | None:
    """The explicit ``FMD_AWS_*`` pair, or whatever boto3's chain finds."""
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        return AwsKeys(
            settings.aws_access_key_id,
            settings.aws_secret_access_key,
            settings.aws_session_token or None,
        )
    global _chain
    try:
        if _chain is None:
            import boto3  # noqa: PLC0415 - only on a deployment that uses the chain

            _chain = boto3.Session().get_credentials() or False
        if not _chain:
            return None
        # Frozen per call: an instance role's credentials rotate underneath.
        frozen = _chain.get_frozen_credentials()
    except Exception as exc:
        log.warning("voice_credentials_unreadable", error=str(exc)[:200])
        return None
    return AwsKeys(frozen.access_key, frozen.secret_key, frozen.token or None)


def boto_kwargs(settings: Settings) -> dict[str, Any]:
    """What ``boto3.client`` is given, as ``app.agent.models`` gives Bedrock."""
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_access_key_id:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            kwargs["aws_session_token"] = settings.aws_session_token
    return kwargs


class Outage:
    """A service that refused this deployment, and until when to believe it."""

    def __init__(self, service: str, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.service = service
        self._clock = clock
        self._until = 0.0
        self.reason: str | None = None

    @property
    def down(self) -> bool:
        return self._clock() < self._until

    def trip(self, reason: str) -> None:
        if not self.down:
            # Loud once, because the fix is the operator's: an IAM action, a
            # region, credentials. Then quiet until it is retried.
            log.warning("voice_service_refused", service=self.service, reason=reason)
        self._until = self._clock() + OUTAGE_S
        self.reason = reason

    def clear(self) -> None:
        if self._until:
            log.info("voice_service_back", service=self.service)
        self._until = 0.0
        self.reason = None
