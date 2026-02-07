"""Budget manager — 预算管理，支持成本感知和降级策略."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum, auto
from typing import Any

import structlog

from moneyclaw.llm.cost_tracker import CostTracker

log = structlog.get_logger()


class BudgetStatus(Enum):
    """预算状态."""

    HEALTHY = auto()      # 预算充足，正常使用
    CAUTION = auto()      # 预算紧张，建议节省
    CRITICAL = auto()     # 预算告急，强制降级
    EXHAUSTED = auto()    # 预算耗尽，只能使用免费模型


@dataclass
class BudgetPolicy:
    """预算策略配置."""

    daily_budget: float = 5.0
    """每日预算（USD）"""

    caution_threshold: float = 0.6
    """谨慎阈值，超过此比例进入谨慎模式"""

    critical_threshold: float = 0.85
    """临界阈值，超过此比例进入临界模式"""

    max_cost_per_call: float | None = None
    """单次调用最大成本限制"""

    enable_auto_downgrade: bool = True
    """是否启用自动降级"""

    reserve_for_urgent: float = 0.1
    """为紧急任务保留的预算比例"""


class BudgetManager:
    """预算管理器.

    跟踪LLM使用成本，根据预算状态提供路由建议。
    支持自动降级策略和预算预警。
    """

    def __init__(
        self,
        cost_tracker: CostTracker,
        policy: BudgetPolicy | None = None,
    ) -> None:
        self._cost_tracker = cost_tracker
        self._policy = policy or BudgetPolicy()
        self._today = date.today()

    @property
    def daily_budget(self) -> float:
        """每日预算."""
        return self._policy.daily_budget

    @property
    def today_cost(self) -> float:
        """今日已消耗成本."""
        return self._cost_tracker.today_cost

    @property
    def budget_remaining(self) -> float:
        """剩余预算."""
        return max(0, self.daily_budget - self.today_cost)

    @property
    def budget_usage_ratio(self) -> float:
        """预算使用率 0-1."""
        if self.daily_budget <= 0:
            return 1.0
        return min(self.today_cost / self.daily_budget, 1.0)

    def get_status(self) -> BudgetStatus:
        """获取当前预算状态."""
        ratio = self.budget_usage_ratio

        if ratio >= 1.0:
            return BudgetStatus.EXHAUSTED
        elif ratio >= self._policy.critical_threshold:
            return BudgetStatus.CRITICAL
        elif ratio >= self._policy.caution_threshold:
            return BudgetStatus.CAUTION
        else:
            return BudgetStatus.HEALTHY

    def can_afford(self, estimated_cost: float, is_urgent: bool = False) -> bool:
        """检查是否负担得起指定成本.

        Args:
            estimated_cost: 预估成本
            is_urgent: 是否为紧急任务（可以使用保留预算）

        Returns:
            是否可以负担
        """
        remaining = self.budget_remaining

        if is_urgent:
            # 紧急任务可以使用全部剩余预算
            return estimated_cost <= remaining

        # 非紧急任务需要保留部分预算
        reserve = self.daily_budget * self._policy.reserve_for_urgent
        available = max(0, remaining - reserve)

        return estimated_cost <= available

    def get_max_affordable_cost(self, is_urgent: bool = False) -> float:
        """获取当前可负担的最大成本."""
        remaining = self.budget_remaining

        if is_urgent:
            return remaining

        reserve = self.daily_budget * self._policy.reserve_for_urgent
        return max(0, remaining - reserve)

    def get_routing_strategy(self) -> dict[str, Any]:
        """获取当前预算状态下的路由策略.

        返回一个策略字典，指导模型选择。
        """
        status = self.get_status()
        strategy = {
            "status": status.name,
            "can_use_premium": False,
            "can_use_standard": False,
            "can_use_economy": False,
            "can_use_free": True,
            "max_cost_per_call": 0.0,
            "preference": "free",
            "force_cheapest": False,
        }

        if status == BudgetStatus.HEALTHY:
            strategy.update({
                "can_use_premium": True,
                "can_use_standard": True,
                "can_use_economy": True,
                "max_cost_per_call": self._policy.max_cost_per_call or float('inf'),
                "preference": "balanced",
                "force_cheapest": False,
            })

        elif status == BudgetStatus.CAUTION:
            strategy.update({
                "can_use_premium": False,
                "can_use_standard": True,
                "can_use_economy": True,
                "max_cost_per_call": min(
                    self._policy.max_cost_per_call or 0.01,
                    self.budget_remaining * 0.2,
                ),
                "preference": "economy",
                "force_cheapest": False,
            })

        elif status == BudgetStatus.CRITICAL:
            strategy.update({
                "can_use_premium": False,
                "can_use_standard": False,
                "can_use_economy": True,
                "max_cost_per_call": min(
                    self._policy.max_cost_per_call or 0.001,
                    self.budget_remaining * 0.1,
                ),
                "preference": "economy",
                "force_cheapest": True,
            })

        else:  # EXHAUSTED
            strategy.update({
                "max_cost_per_call": 0.0,
                "preference": "free",
                "force_cheapest": True,
            })

        return strategy

    def should_downgrade(self, preferred_cost: float) -> bool:
        """判断是否应该降级到更便宜的模型."""
        if not self._policy.enable_auto_downgrade:
            return False

        status = self.get_status()

        if status == BudgetStatus.EXHAUSTED:
            return True

        if status == BudgetStatus.CRITICAL:
            return preferred_cost > 0.001

        if status == BudgetStatus.CAUTION:
            return preferred_cost > 0.01

        return False

    def get_recommended_tiers(self) -> list[str]:
        """获取当前预算状态下推荐的模型层级列表（按优先级排序）."""
        status = self.get_status()

        if status == BudgetStatus.HEALTHY:
            return ["PREMIUM", "STANDARD", "ECONOMY", "FREE"]
        elif status == BudgetStatus.CAUTION:
            return ["STANDARD", "ECONOMY", "FREE"]
        elif status == BudgetStatus.CRITICAL:
            return ["ECONOMY", "FREE"]
        else:  # EXHAUSTED
            return ["FREE"]

    def record_estimated_cost(self, estimated_cost: float) -> None:
        """记录预估成本（用于预算规划）."""
        # 注意：这只是预估，实际成本由 CostTracker 记录
        log.debug(
            "budget.estimated_cost",
            estimated=estimated_cost,
            remaining=self.budget_remaining,
        )

    def get_status_report(self) -> str:
        """获取预算状态报告（人类可读）."""
        status = self.get_status()
        usage_pct = self.budget_usage_ratio * 100

        lines = [
            f"Budget Status: {status.name}",
            f"Today's Spending: ${self.today_cost:.4f} / ${self.daily_budget:.2f} ({usage_pct:.1f}%)",
            f"Remaining: ${self.budget_remaining:.4f}",
        ]

        if status == BudgetStatus.CAUTION:
            lines.append("⚠️  Budget is running low. Consider using cheaper models.")
        elif status == BudgetStatus.CRITICAL:
            lines.append("🚨 Budget critical! Using economy models only.")
        elif status == BudgetStatus.EXHAUSTED:
            lines.append("❌ Daily budget exhausted. Free models only.")

        return "\n".join(lines)

    def __str__(self) -> str:
        return f"BudgetManager({self.get_status().name}: ${self.today_cost:.4f}/${self.daily_budget:.2f})"
