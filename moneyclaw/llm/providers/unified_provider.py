"""Unified provider — 统一的Provider包装层，支持所有模型."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from moneyclaw.llm.providers.base import LLMProvider, LLMResponse

if TYPE_CHECKING:
    from moneyclaw.llm.model_profile import ModelProfile

log = structlog.get_logger()


class UnifiedProvider:
    """统一Provider包装层.

    封装实际的LLMProvider，提供标准化的接口和统一的元数据管理。
    支持通过model_id动态路由到正确的底层Provider。
    """

    def __init__(
        self,
        model: ModelProfile,
        provider: LLMProvider,
    ) -> None:
        self._model = model
        self._provider = provider
        self._call_count = 0
        self._total_latency = 0.0

    @property
    def model_id(self) -> str:
        """模型唯一标识."""
        return self._model.model_id

    @property
    def model_profile(self) -> ModelProfile:
        """模型画像."""
        return self._model

    @property
    def display_name(self) -> str:
        """友好显示名称."""
        return self._model.display_name

    @property
    def avg_latency_ms(self) -> float:
        """平均延迟."""
        if self._call_count == 0:
            return self._model.avg_latency_ms
        return self._total_latency / self._call_count

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        **kwargs,
    ) -> LLMResponse:
        """执行模型调用.

        包装底层Provider的调用，统一处理超时和错误。
        """
        start_time = time.monotonic()

        try:
            response = await self._provider.complete(
                prompt=prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._call_count += 1
            self._total_latency += elapsed_ms

            log.debug(
                "unified_provider.complete_success",
                model=self.model_id,
                latency_ms=round(elapsed_ms, 2),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )

            return response

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            log.error(
                "unified_provider.complete_error",
                model=self.model_id,
                latency_ms=round(elapsed_ms, 2),
                error=str(e),
            )
            raise

    async def is_available(self) -> bool:
        """检查模型是否可用."""
        return await self._provider.is_available()

    def estimate_cost(self, input_tokens: int = 500, output_tokens: int = 300) -> float:
        """估算指定token数的成本."""
        input_cost = (input_tokens / 1000) * self._model.cost_per_1k_input
        output_cost = (output_tokens / 1000) * self._model.cost_per_1k_output
        return input_cost + output_cost

    def __str__(self) -> str:
        return f"UnifiedProvider({self.model_id})"

    def __repr__(self) -> str:
        return (
            f"UnifiedProvider("
            f"model_id='{self.model_id}', "
            f"display_name='{self.display_name}', "
            f"calls={self._call_count}"
            f")"
        )
