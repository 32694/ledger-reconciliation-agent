"""Chat-model construction and bounded retry policy."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import httpx
import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_chain, wait_fixed

from reconciliation_agent.settings import Settings


class AsyncModelInvoker(Protocol):
    """Minimal asynchronous model contract used by the workflow."""

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        """Return one chat-model message."""


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Create an OpenAI-compatible model without SDK-level retries."""
    return ChatOpenAI(
        model=settings.model_name,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        timeout=settings.model_timeout_seconds,
        max_completion_tokens=settings.model_max_output_tokens,
        max_retries=0,
    )


def _is_retryable_model_error(error: BaseException) -> bool:
    return isinstance(
        error,
        TimeoutError
        | httpx.TransportError
        | openai.APIConnectionError
        | openai.APITimeoutError
        | openai.RateLimitError,
    )


async def invoke_model_with_retry(
    model: AsyncModelInvoker,
    messages: Sequence[BaseMessage],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AIMessage:
    """Invoke a model at most three times for transient transport failures."""
    retrying = AsyncRetrying(
        sleep=sleep,
        stop=stop_after_attempt(3),
        wait=wait_chain(wait_fixed(1), wait_fixed(2)),
        retry=retry_if_exception(_is_retryable_model_error),
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            response = await model.ainvoke(messages)
            if not isinstance(response, AIMessage):
                raise TypeError("chat model must return an AIMessage")
            return response
    raise RuntimeError("model retry loop ended without a result")
