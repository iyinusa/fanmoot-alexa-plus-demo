"""A model that follows a script, so a turn is deterministic.

Each step is one model call: a tool call, some words (streamed as partials,
then the whole), an exception, or responses given verbatim. It records every
request it was sent, so a test can assert what the agent was told and which tools it was offered.

It stands in for a *provider*, not for the agent: it is handed to
``FailoverLlm`` through the same factory ``build_llm`` fills in production,
so the chain, the failover, the accounting and ADK itself all run for real.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr


def call(tool: str, /, **args: Any) -> dict[str, Any]:
    return {"call": tool, "args": args}


def say(text: str) -> dict[str, Any]:
    return {"say": text}


def fail(error: BaseException, *, after_words: str | None = None) -> dict[str, Any]:
    return {"raise": error, "after": after_words}


def respond(*responses: LlmResponse) -> dict[str, Any]:
    """Exactly these responses, in order: an error response, an empty stream."""
    return {"respond": list(responses)}


def _usage(tokens_in: int, tokens_out: int) -> types.GenerateContentResponseUsageMetadata:
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=tokens_in, candidates_token_count=tokens_out
    )


class ScriptedLlm(BaseLlm):
    model: str = "scripted"
    _steps: list[dict[str, Any]] = PrivateAttr(default_factory=list)
    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    @classmethod
    def of(cls, *steps: dict[str, Any], model: str = "scripted") -> ScriptedLlm:
        llm = cls(model=model)
        llm._steps = list(steps)
        return llm

    @property
    def requests(self) -> list[LlmRequest]:
        return self._requests

    @property
    def remaining(self) -> int:
        return len(self._steps)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request)
        if not self._steps:
            raise AssertionError("the script ran out of steps")
        step = self._steps.pop(0)

        if "respond" in step:
            for response in step["respond"]:
                yield response
            return

        if "raise" in step:
            if step["after"]:
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text=step["after"])]),
                    partial=True,
                )
            raise step["raise"]

        if "call" in step:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(name=step["call"], args=step["args"])
                        )
                    ],
                ),
                usage_metadata=_usage(120, 12),
                model_version=f"{self.model}-v1",
            )
            return

        words = step["say"].split(" ")
        if stream:
            for index, word in enumerate(words):
                chunk = word if index == len(words) - 1 else word + " "
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text=chunk)]),
                    partial=True,
                )
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=step["say"])]),
            partial=False,
            usage_metadata=_usage(200, 20),
            model_version=f"{self.model}-v1",
        )
