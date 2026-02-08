"""Smart Router — 智能LLM路由器，自动发现模型并智能选择最优模型."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import structlog

from moneyclaw.llm.budget_manager import BudgetManager, BudgetStatus
from moneyclaw.llm.cache import ResponseCache
from moneyclaw.llm.cost_tracker import CostTracker
from moneyclaw.llm.model_profile import CostTier, ModelProfile, TaskType
from moneyclaw.llm.model_registry import SmartModelRegistry
from moneyclaw.llm.performance_tracker import PerformanceTracker
from moneyclaw.llm.providers.base import LLMResponse
from moneyclaw.llm.providers.unified_provider import UnifiedProvider
from moneyclaw.llm.task_analyzer import TaskAnalyzer
from moneyclaw.llm.types import TaskRequest

log = structlog.get_logger()


class SmartRouter:
    """智能LLM路由器.

    核心功能：
    1. 自动发现所有配置了API key的Provider的可用模型
    2. 根据任务类型、预算、历史表现智能选择最优模型
    3. 支持预算感知降级和历史性能学习
    4. 统一缓存和成本追踪
    """

    def __init__(
        self,
        registry: SmartModelRegistry,
        cost_tracker: CostTracker,
        budget_manager: BudgetManager,
        performance_tracker: PerformanceTracker,
        cache: ResponseCache | None = None,
    ) -> None:
        self._registry = registry
        self._cost_tracker = cost_tracker
        self._budget_manager = budget_manager
        self._performance = performance_tracker
        self._cache = cache or ResponseCache()
        self._analyzer = TaskAnalyzer()

        # Provider工厂映射
        self._providers: dict[str, UnifiedProvider] = {}
        self._initialized = False

    @property
    def cost_tracker(self) -> CostTracker:
        """Public access to cost tracker for external reporting."""
        return self._cost_tracker

    async def initialize(self) -> None:
        """初始化路由器 — 触发模型发现."""
        if self._initialized:
            return

        log.info("smart_router.initializing")
        await self._registry.discover()
        await self._init_providers()
        self._initialized = True

        stats = self._registry.get_stats()
        log.info(
            "smart_router.initialized",
            total_models=stats["total_models"],
            available=stats["available_models"],
            by_provider=stats["by_provider"],
        )

    async def _init_providers(self) -> None:
        """初始化所有发现的模型的Provider."""
        from moneyclaw.llm.providers.litellm_provider import LiteLLMProvider
        from moneyclaw.llm.providers.ollama import OllamaProvider

        for model in self._registry.get_all():
            try:
                if model.provider == "ollama":
                    base_url = model.metadata.get("base_url", "http://localhost:11434")
                    inner_provider = OllamaProvider(
                        model=model.model_id.split("/")[-1],
                        base_url=base_url,
                    )
                    # 验证 Ollama 是否可用
                    if not await inner_provider.is_available():
                        log.warning("smart_router.ollama_not_available", model=model.model_id)
                        continue
                else:
                    inner_provider = LiteLLMProvider(model=model.model_id)

                unified = UnifiedProvider(model=model, provider=inner_provider)
                self._providers[model.model_id] = unified

            except Exception as e:
                log.warning(
                    "smart_router.provider_init_failed",
                    model=model.model_id,
                    error=str(e),
                )

    async def route(self, request: TaskRequest) -> LLMResponse:
        """智能路由 — 选择最优模型并执行.

        这是SmartRouter的核心方法，完整的决策流程：
        1. 检查缓存
        2. 分析任务类型
        3. 检查预算状态
        4. 选择最优模型
        5. 执行调用
        6. 记录性能和成本
        """
        if not self._initialized:
            await self.initialize()

        # 1. 检查缓存
        if request.cache_ttl > 0:
            cache_key = self._cache_key(request)
            cached = self._cache.get(cache_key)
            if cached is not None:
                log.debug("smart_router.cache_hit", key=cache_key[:12])
                return cached

        # 2. 分析任务
        task_type = request.task_type
        complexity = request.complexity

        if task_type is None or complexity == 0.0:
            analysis = self._analyzer.analyze(request.prompt, request.system)
            if task_type is None:
                task_type = analysis.primary_type
            if complexity == 0.0:
                complexity = analysis.complexity_hint

        # 3. 选择模型
        provider = await self._select_model(request, task_type, complexity)
        if provider is None:
            raise RuntimeError("No suitable model available for this request")

        # 4. 执行调用
        log.info(
            "smart_router.routing",
            model=provider.model_id,
            task=task_type.name if task_type else "unknown",
            complexity=round(complexity, 2),
            budget_status=self._budget_manager.get_status().name,
        )

        start = time.monotonic()
        success = True
        error_type = None

        try:
            response = await provider.complete(
                prompt=request.prompt,
                system=request.system,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )

        except Exception as e:
            success = False
            error_type = type(e).__name__
            elapsed = time.monotonic() - start

            # 记录失败
            self._performance.record(
                model=provider.model_profile,
                task_type=task_type or TaskType.CONVERSATION,
                success=False,
                latency_ms=elapsed * 1000,
                input_tokens=0,
                output_tokens=0,
                actual_cost=0.0,
                error_type=error_type,
            )

            # 标记模型不可用
            self._registry.update_health_status(provider.model_id, False)

            # 尝试降级到其他模型
            fallback = await self._try_fallback(request, task_type, provider.model_id)
            if fallback:
                log.info("smart_router.fallback", from_model=provider.model_id, to_model=fallback.model_id)
                return await self._execute_with_provider(fallback, request, task_type)

            raise

        elapsed = time.monotonic() - start

        # 5. 记录成本和性能
        self._cost_tracker.record(
            layer=0,  # 不再使用固定层级
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost=response.cost,
            latency=elapsed,
        )

        self._performance.record(
            model=provider.model_profile,
            task_type=task_type or TaskType.CONVERSATION,
            success=True,
            latency_ms=elapsed * 1000,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            actual_cost=response.cost,
        )

        # 6. 缓存结果
        if request.cache_ttl > 0:
            self._cache.set(cache_key, response, ttl=request.cache_ttl)

        return response

    async def _select_model(
        self,
        request: TaskRequest,
        task_type: TaskType,
        complexity: float,
    ) -> UnifiedProvider | None:
        """选择最优模型."""
        # 用户指定了模型
        if request.preferred_model:
            provider = self._providers.get(request.preferred_model)
            if provider and provider.model_profile.is_available:
                return provider
            log.warning("smart_router.preferred_model_unavailable", model=request.preferred_model)

        # 获取预算策略
        budget_strategy = self._budget_manager.get_routing_strategy()
        max_cost = budget_strategy["max_cost_per_call"]

        # 筛选候选模型
        candidates = self._filter_candidates(
            task_type=task_type,
            complexity=complexity,
            require_tools=request.require_tools,
            require_vision=request.require_vision,
            require_json_mode=request.require_json_mode,
            max_cost=max_cost,
        )

        if not candidates:
            log.error("smart_router.no_candidates")
            return None

        # 评分排序
        scored = [
            (provider, self._score_model(provider, task_type, complexity, budget_strategy))
            for provider in candidates
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_provider = scored[0][0]
        log.debug(
            "smart_router.model_selected",
            model=best_provider.model_id,
            score=round(scored[0][1], 3),
            candidates=len(candidates),
        )

        return best_provider

    def _filter_candidates(
        self,
        task_type: TaskType,
        complexity: float,
        require_tools: bool,
        require_vision: bool,
        require_json_mode: bool,
        max_cost: float,
    ) -> list[UnifiedProvider]:
        """筛选候选模型."""
        candidates = []

        for provider in self._providers.values():
            model = provider.model_profile
            
            # Debug log
            log.info("smart_router.check_candidate", model=model.model_id)

            # 基本检查
            if not model.is_available:
                log.info("smart_router.skip", reason="unavailable", model=model.model_id)
                continue

            # 功能要求检查
            if require_tools and not model.supports_tools:
                log.info("smart_router.skip", reason="no_tools", model=model.model_id)
                continue
            if require_vision and not model.supports_vision:
                log.info("smart_router.skip", reason="no_vision", model=model.model_id)
                continue
            if require_json_mode and not model.supports_json_mode:
                log.info("smart_router.skip", reason="no_json", model=model.model_id)
                continue

            # 成本检查
            if max_cost > 0 and model.estimated_cost_per_call > max_cost:
                log.info("smart_router.skip", reason="too_expensive", model=model.model_id, cost=model.estimated_cost_per_call, max=max_cost)
                continue

            # 能力检查 — 高复杂度任务需要高能力模型
            min_capability = complexity * 0.8
            if model.capability_score < min_capability:
                log.info("smart_router.skip", reason="low_capability", model=model.model_id, score=model.capability_score, min=min_capability)
                continue

            # 任务匹配检查
            if not model.matches_task(task_type, min_score=0.5):
                log.info("smart_router.skip", reason="task_mismatch", model=model.model_id, task=task_type, score=model.task_strengths.get(task_type, 0))
                continue

            candidates.append(provider)

        return candidates

    def _score_model(
        self,
        provider: UnifiedProvider,
        task_type: TaskType,
        complexity: float,
        budget_strategy: dict[str, Any],
    ) -> float:
        """计算模型评分.

        评分维度：
        - 任务匹配度 (30%)
        - 能力适配度 (25%)
        - 成本效率 (20%)
        - 历史表现 (15%)
        - 响应速度 (10%)
        """
        model = provider.model_profile

        # 1. 任务匹配度
        task_score = model.task_strengths.get(task_type, model.capability_score * 0.7)

        # 2. 能力适配度
        capability_score = model.capability_score
        # 对于高复杂度任务，偏好高能力模型
        if complexity > 0.7:
            capability_score = capability_score ** 0.5  # 放大差异

        # 3. 成本效率
        cost = max(model.estimated_cost_per_call, 0.000001)
        cost_efficiency = 1.0 / (1.0 + cost * 100)
        if budget_strategy["force_cheapest"]:
            cost_efficiency = 1.0 / cost  # 强制省钱时更重视成本

        # 4. 历史表现
        perf_stats = self._performance.get_stats(model.model_id)
        if perf_stats:
            history_score = perf_stats.success_rate * 0.7 + perf_stats.avg_quality_score * 0.3
        else:
            history_score = 0.8  # 无历史数据时给中等评分

        # 5. 响应速度
        speed_score = 1.0 / (1.0 + model.avg_latency_ms / 1000)

        # 加权总分
        weights = (0.30, 0.25, 0.20, 0.15, 0.10)
        total_score = (
            task_score * weights[0] +
            capability_score * weights[1] +
            cost_efficiency * weights[2] +
            history_score * weights[3] +
            speed_score * weights[4]
        )

        return total_score

    async def _try_fallback(
        self,
        request: TaskRequest,
        task_type: TaskType,
        failed_model_id: str,
    ) -> UnifiedProvider | None:
        """尝试降级到其他模型."""
        log.info("smart_router.trying_fallback", failed_model=failed_model_id)

        # 获取其他可用模型
        other_models = [
            p for p in self._providers.values()
            if p.model_id != failed_model_id and p.model_profile.is_available
        ]

        if not other_models:
            return None

        # 按成本排序，选最便宜的可用模型
        return min(other_models, key=lambda p: p.model_profile.estimated_cost_per_call)

    async def _execute_with_provider(
        self,
        provider: UnifiedProvider,
        request: TaskRequest,
        task_type: TaskType,
    ) -> LLMResponse:
        """使用指定Provider执行调用."""
        start = time.monotonic()

        response = await provider.complete(
            prompt=request.prompt,
            system=request.system,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

        elapsed = time.monotonic() - start

        # 记录
        self._cost_tracker.record(
            layer=0,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost=response.cost,
            latency=elapsed,
        )

        self._performance.record(
            model=provider.model_profile,
            task_type=task_type,
            success=True,
            latency_ms=elapsed * 1000,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            actual_cost=response.cost,
        )

        return response

    @staticmethod
    def _cache_key(request: TaskRequest) -> str:
        """生成缓存键."""
        raw = f"{request.system}|{request.prompt}|{request.temperature}|{request.max_tokens}"
        return hashlib.sha256(raw.encode()).hexdigest()

    # 便捷方法
    async def complete(self, prompt: str | TaskRequest, **kwargs) -> LLMResponse:
        """简化的完成接口.

        Args:
            prompt: 提示词字符串或 TaskRequest 对象
            **kwargs: 其他参数（当 prompt 为字符串时）
        """
        if isinstance(prompt, TaskRequest):
            request = prompt
        else:
            request = TaskRequest(prompt=prompt, **kwargs)
        return await self.route(request)

    def get_available_models(self) -> list[ModelProfile]:
        """获取所有可用模型."""
        return [p.model_profile for p in self._providers.values() if p.model_profile.is_available]

    def get_status(self) -> dict[str, Any]:
        """获取路由器状态."""
        return {
            "initialized": self._initialized,
            "total_models": len(self._providers),
            "available_models": len(self.get_available_models()),
            "budget": self._budget_manager.get_status_report(),
            "performance": self._performance.get_summary(),
        }
