from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from reconciliation_agent.model import invoke_model_with_retry


class ScriptedInvoker:
    def __init__(self, failures: Sequence[Exception]) -> None:
        self._failures = list(failures)
        self.attempts = 0

    async def ainvoke(
        self,
        input: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        del input, config, kwargs
        self.attempts += 1
        if self._failures:
            raise self._failures.pop(0)
        return AIMessage(content="ok")


@pytest.mark.anyio
async def test_timeout_is_retried_three_total_attempts() -> None:
    model = ScriptedInvoker([TimeoutError("first"), TimeoutError("second")])
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    result = await invoke_model_with_retry(model, [], sleep=record_sleep)

    assert result.content == "ok"
    assert model.attempts == 3
    assert delays == [1.0, 2.0]


@pytest.mark.anyio
async def test_validation_error_is_not_retried() -> None:
    model = ScriptedInvoker([ValueError("invalid request")])

    with pytest.raises(ValueError, match="invalid request"):
        await invoke_model_with_retry(model, [], sleep=_unexpected_sleep)

    assert model.attempts == 1


async def _unexpected_sleep(_delay: float) -> Any:
    raise AssertionError("non-retryable errors must not sleep")
