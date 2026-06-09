"""Model registry — 智能模型注册表，管理所有发现的模型."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from moneyclaw.llm.model_discovery import ModelDiscoveryService
from moneyclaw.llm.model_profile import CostTier, ModelProfile, TaskType

log = structlog.get_logger()


async def _check_model_health(model: ModelProfile, timeout: float = 5.0) -> bool:
    """检查单个模型的健康状态.

    通过尝试调用模型的 is_available() 方法来验证配置是否有效。
    """
    from moneyclaw.llm.providers.litellm_provider import LiteLLMProvider
    from moneyclaw.llm.providers.ollama import OllamaProvider

    try:
        if model.provider == "ollama":
            base_url = model.metadata.get("base_url", "http://localhost:11434")
            provider = OllamaProvider(
                model=model.model_id.split("/")[-1],
                base_url=base_url,
            )
        else:
            provider = LiteLLMProvider(model=model.model_id)

        # 使用 asyncio.wait_for 添加超时
        is_available = await asyncio.wait_for(
            provider.is_available(),
            timeout=timeout,
        )
        return is_available

    except asyncio.TimeoutError:
        log.debug("registry.health_check_timeout", model=model.model_id)
        return False
    except Exception as e:
        log.debug("registry.health_check_failed", model=model.model_id, error=str(e))
        return False


class SmartModelRegistry:
    """智能模型注册表.

    自动发现并管理所有可用模型，提供按能力、成本、任务类型等维度的查询接口。
    支持模型健康检查和动态可用性更新。
    """

    # 缓存时间（秒）
    CACHE_TTL = 900  # 15分钟

    def __init__(
        self,
        discovery_service: ModelDiscoveryService | None = None,
    ) -> None:
        self._discovery = discovery_service or ModelDiscoveryService()
        self._models: dict[str, ModelProfile] = {}
        self._models_by_provider: dict[str, list[str]] = {}
        self._models_by_tier: dict[CostTier, list[str]] = {}
        self._models_by_task: dict[TaskType, list[str]] = {}
        self._last_discovery: float = 0.0
        self._discovery_lock = asyncio.Lock()
        self._health_status: dict[str, bool] = {}

    async def discover(
        self,
        force: bool = False,
        skip_health_check: bool = False,
    ) -> list[ModelProfile]:
        """触发模型发现.

        Args:
            force: 是否强制重新发现，忽略缓存
            skip_health_check: 是否跳过健康检查

        Returns:
            发现的模型列表（已通过健康检查的可用模型）
        """
        async with self._discovery_lock:
            now = time.time()
            if not force and (now - self._last_discovery) < self.CACHE_TTL:
                log.debug("registry.using_cache", models=len(self._models))
                return list(self._models.values())

            log.info("registry.starting_discovery")
            discovered_models = await self._discovery.discover_all()

            # 健康检查：验证每个发现的模型是否真正可用
            if not skip_health_check:
                log.info("registry.health_check_start", models=len(discovered_models))
                healthy_models = await self._health_check_models(discovered_models)
                log.info(
                    "registry.health_check_complete",
                    total=len(discovered_models),
                    healthy=len(healthy_models),
                    unhealthy=len(discovered_models) - len(healthy_models),
                )
            else:
                healthy_models = discovered_models

            self._update_registry(healthy_models)
            self._last_discovery = now

            log.info(
                "registry.discovery_complete",
                total=len(healthy_models),
                by_provider={
                    provider: len(ids)
                    for provider, ids in self._models_by_provider.items()
                },
            )
            return healthy_models

    async def _health_check_models(self, models: list[ModelProfile]) -> list[ModelProfile]:
        """对模型列表进行健康检查，返回可用的模型."""
        if not models:
            return []

        # 并行检查所有模型的健康状态
        check_tasks = [_check_model_health(m) for m in models]
        results = await asyncio.gather(*check_tasks, return_exceptions=True)

        healthy_models: list[ModelProfile] = []
        unhealthy_details: list[dict[str, str]] = []
        for model, result in zip(models, results):
            if isinstance(result, Exception):
                unhealthy_details.append(
                    {
                        "model": model.model_id,
                        "reason": "health_check_exception",
                        "error": str(result),
                    }
                )
                model = ModelProfile(
                    **{**model.__dict__, "is_available": False}
                )
            elif result is True:
                healthy_models.append(model)
            else:
                unhealthy_details.append(
                    {
                        "model": model.model_id,
                        "reason": "health_check_failed",
                    }
                )
                model = ModelProfile(
                    **{**model.__dict__, "is_available": False}
                )

        if unhealthy_details:
            sample = unhealthy_details[:5]
            log.warning(
                "registry.health_check_unhealthy_summary",
                unhealthy=len(unhealthy_details),
                sample=sample,
            )
            if len(unhealthy_details) > len(sample):
                log.debug(
                    "registry.health_check_unhealthy_full",
                    details=unhealthy_details,
                )

        return healthy_models

    def _update_registry(self, models: list[ModelProfile]) -> None:
        """更新注册表索引."""
        self._models = {m.model_id: m for m in models}
        self._models_by_provider = {}
        self._models_by_tier = {}
        self._models_by_task = {}

        for model in models:
            # 按Provider索引
            self._models_by_provider.setdefault(model.provider, []).append(model.model_id)

            # 按成本层级索引
            self._models_by_tier.setdefault(model.cost_tier, []).append(model.model_id)

            # 按任务专长索引
            for task_type, score in model.task_strengths.items():
                if score >= 0.6:  # 只记录有显著专长的任务
                    self._models_by_task.setdefault(task_type, []).append(model.model_id)

    def get(self, model_id: str) -> ModelProfile | None:
        """获取指定模型."""
        return self._models.get(model_id)

    def get_all(self, include_unavailable: bool = False) -> list[ModelProfile]:
        """获取所有模型.

        Args:
            include_unavailable: 是否包含标记为不可用的模型
        """
        models = self._models.values()
        if not include_unavailable:
            models = [m for m in models if m.is_available]
        return list(models)

    def get_by_provider(self, provider: str) -> list[ModelProfile]:
        """获取指定Provider的所有模型."""
        model_ids = self._models_by_provider.get(provider, [])
        return [self._models[mid] for mid in model_ids if mid in self._models]

    def get_by_tier(self, tier: CostTier) -> list[ModelProfile]:
        """获取指定成本层级的模型."""
        model_ids = self._models_by_tier.get(tier, [])
        return [self._models[mid] for mid in model_ids if mid in self._models]

    def get_by_task(
        self,
        task_type: TaskType,
        min_score: float = 0.5,
    ) -> list[ModelProfile]:
        """获取擅长指定任务类型的模型."""
        candidates = []
        for model in self._models.values():
            if model.matches_task(task_type, min_score):
                candidates.append(model)
        return candidates

    def get_cheapest(
        self,
        min_capability: float = 0.0,
        task_type: TaskType | None = None,
    ) -> ModelProfile | None:
        """获取满足条件的最便宜模型."""
        candidates = self._models.values()

        if min_capability > 0:
            candidates = [m for m in candidates if m.capability_score >= min_capability]

        if task_type:
            candidates = [m for m in candidates if m.matches_task(task_type)]

        candidates = [m for m in candidates if m.is_available]

        if not candidates:
            return None

        return min(candidates, key=lambda m: m.estimated_cost_per_call)

    def get_best_for_task(
        self,
        task_type: TaskType,
        max_cost: float | None = None,
    ) -> ModelProfile | None:
        """获取最适合指定任务的最佳模型.

        综合考虑任务专长、能力评分和成本。
        """
        candidates = []
        for model in self._models.values():
            if not model.is_available:
                continue
            if max_cost and model.estimated_cost_per_call > max_cost:
                continue
            candidates.append(model)

        if not candidates:
            return None

        # 按任务专长和能力的加权评分排序
        def score(model: ModelProfile) -> float:
            task_score = model.task_strengths.get(task_type, model.capability_score * 0.7)
            # 平衡性能和成本：高质量但不太贵的模型优先
            cost_factor = 1.0 / (1.0 + model.estimated_cost_per_call * 100)
            return task_score * 0.7 + model.capability_score * 0.2 + cost_factor * 0.1

        return max(candidates, key=score)

    def select_models_for_budget(
        self,
        budget_remaining: float,
        expected_calls: int = 1,
    ) -> list[ModelProfile]:
        """根据剩余预算选择可用模型.

        返回预算内可以使用的模型列表，按性价比排序。
        """
        max_cost_per_call = budget_remaining / max(expected_calls, 1)

        candidates = [
            m for m in self._models.values()
            if m.is_available and m.estimated_cost_per_call <= max_cost_per_call
        ]

        # 按性价比排序（能力/成本比）
        def cost_efficiency(model: ModelProfile) -> float:
            cost = max(model.estimated_cost_per_call, 0.000001)  # 避免除零
            return model.effective_score / cost

        return sorted(candidates, key=cost_efficiency, reverse=True)

    def update_health_status(self, model_id: str, is_healthy: bool) -> None:
        """更新模型的健康状态."""
        self._health_status[model_id] = is_healthy
        if model_id in self._models:
            # 使用 object.__setattr__ 绕过 frozen dataclass
            model = self._models[model_id]
            # 创建新的profile（dataclass是frozen的）
            new_model = ModelProfile(
                **{**model.__dict__, "is_available": is_healthy}
            )
            self._models[model_id] = new_model

    def get_stats(self) -> dict[str, Any]:
        """获取注册表统计信息."""
        return {
            "total_models": len(self._models),
            "available_models": sum(1 for m in self._models.values() if m.is_available),
            "by_provider": {
                provider: len(ids)
                for provider, ids in self._models_by_provider.items()
            },
            "by_tier": {
                tier.name: len(ids)
                for tier, ids in self._models_by_tier.items()
            },
            "last_discovery": self._last_discovery,
        }

    def __len__(self) -> int:
        return len(self._models)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def __iter__(self):
        return iter(self._models.values())
