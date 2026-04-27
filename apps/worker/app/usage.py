"""LLM usage capture.

Every agent call goes through ``with_structured_output``, which returns
the parsed Pydantic model — but the *raw* LangChain response (with token
counts in ``usage_metadata`` / ``llm_output``) is hidden inside that
wrapper.

The cleanest place to grab token counts is a callback handler.
LangChain calls ``on_llm_end`` with an ``LLMResult`` whose
``llm_output`` (or per-generation ``message.usage_metadata``) carries
the provider's usage block. We accumulate into a ``UsageCapture``
instance and the agent reads totals after the call returns.

Falls open: if the provider doesn't surface usage we leave the counters
at zero (the cost ledger handles unknown-cost rows).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler

logger = logging.getLogger(__name__)


class UsageCapture(AsyncCallbackHandler):
    """Async callback that accumulates token usage across one call.

    Anthropic surfaces usage on the AIMessage's ``usage_metadata``
    (``input_tokens`` / ``output_tokens``). OpenAI-compatible providers
    use ``llm_output["token_usage"]`` with prompt/completion fields.
    Both paths are handled here so the agents stay provider-agnostic.
    """

    def __init__(self) -> None:
        super().__init__()
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_creation_input_tokens: int = 0
        self.cache_read_input_tokens: int = 0
        self.model: str = ""

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            generations = getattr(response, "generations", None) or []
            if generations and generations[0]:
                msg = getattr(generations[0][0], "message", None)
                if msg is not None:
                    usage = getattr(msg, "usage_metadata", None) or {}
                    inp = int(usage.get("input_tokens", 0) or 0)
                    out = int(usage.get("output_tokens", 0) or 0)
                    if inp or out:
                        self.input_tokens += inp
                        self.output_tokens += out
                        details = usage.get("input_token_details") or {}
                        self.cache_creation_input_tokens += int(
                            details.get("cache_creation", 0) or 0
                        )
                        self.cache_read_input_tokens += int(
                            details.get("cache_read", 0) or 0
                        )
                        if not self.model:
                            meta = getattr(msg, "response_metadata", {}) or {}
                            self.model = (
                                meta.get("model")
                                or meta.get("model_name")
                                or ""
                            )
                        return

            llm_output = getattr(response, "llm_output", None) or {}
            usage = (
                llm_output.get("usage")
                or llm_output.get("token_usage")
                or {}
            )
            inp = int(
                usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
            )
            out = int(
                usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
            )
            self.input_tokens += inp
            self.output_tokens += out
            self.cache_creation_input_tokens += int(
                usage.get("cache_creation_input_tokens", 0) or 0
            )
            self.cache_read_input_tokens += int(
                usage.get("cache_read_input_tokens", 0) or 0
            )
            if not self.model:
                self.model = (
                    llm_output.get("model_name")
                    or llm_output.get("model")
                    or ""
                )
        except Exception as exc:
            # Telemetry must never break the call.
            logger.debug("UsageCapture failed to parse response: %s", exc)


__all__ = ["UsageCapture"]
