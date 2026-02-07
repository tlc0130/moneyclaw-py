"""Performance tracker — 追踪模型性能，支持历史学习和优化."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from moneyclaw.llm.model_profile import ModelProfile, TaskType

log = structlog.get_logger()


@dataclass
class PerformanceRecord:
    """单次调用性能记录."""

    timestamp: float
    model_id: str
    task_type: TaskType
    success: bool
    latency_ms: float
    input_tokens: int
    output_tokens: int
    actual_cost: float
    quality_score: float = 0.0
    """质量评分 0-1，基于响应满足任务需求的程度（可由上层评估后更新）"""

    error_type: str | None = None
    """错误类型，如果调用失败"""

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelPerformanceStats:
    """模型性能统计."""

    model_id: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_cost: float = 0.0
    avg_latency_ms: float = 0.0
    avg_quality_score: float = 0.0
    success_rate: float = 1.0
    last_used: float = 0.0

    # 按任务类型的统计
    by_task: dict[TaskType, dict[str, Any]] = field(default_factory=dict)

    def update(self, record: PerformanceRecord) -> None:
        """用新记录更新统计."""
        self.total_calls += 1
        self.last_used = record.timestamp

        if record.success:
            self.successful_calls += 1
        else:
            self.failed_calls += 1

        self.total_cost += record.actual_cost

        # 更新平均延迟（指数移动平均）
        alpha = 0.3  # 平滑因子
        self.avg_latency_ms = (
            alpha * record.latency_ms + (1 - alpha) * self.avg_latency_ms
            if self.avg_latency_ms > 0
            else record.latency_ms
        )

        # 更新质量评分
        if record.quality_score > 0:
            self.avg_quality_score = (
                alpha * record.quality_score + (1 - alpha) * self.avg_quality_score
                if self.avg_quality_score > 0
                else record.quality_score
            )

        # 更新成功率
        self.success_rate = self.successful_calls / self.total_calls

        # 更新任务类型统计
        task_stats = self.by_task.setdefault(record.task_type, {
            "calls": 0, "success": 0, "avg_quality": 0.0
        })
        task_stats["calls"] += 1
        if record.success:
            task_stats["success"] += 1
        if record.quality_score > 0:
            task_stats["avg_quality"] = (
                (task_stats["avg_quality"] * (task_stats["calls"] - 1) + record.quality_score)
                / task_stats["calls"]
            )


class PerformanceTracker:
    """性能追踪器.

    记录每个模型在各种任务上的实际表现，用于持续优化路由决策。
    支持内存中的实时统计和可选的持久化存储。
    """

    def __init__(self) -> None:
        self._records: list[PerformanceRecord] = []
        self._stats: dict[str, ModelPerformanceStats] = {}
        self._recent_calls: list[PerformanceRecord] = []
        self._max_recent = 100  # 保留最近100条记录

    def record(
        self,
        model: ModelProfile,
        task_type: TaskType,
        success: bool,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        actual_cost: float,
        quality_score: float = 0.0,
        error_type: str | None = None,
    ) -> PerformanceRecord:
        """记录一次调用性能.

        Args:
            model: 使用的模型
            task_type: 任务类型
            success: 是否成功
            latency_ms: 延迟（毫秒）
            input_tokens: 输入token数
            output_tokens: 输出token数
            actual_cost: 实际成本
            quality_score: 质量评分（可选）
            error_type: 错误类型（如果失败）

        Returns:
            创建的性能记录
        """
        record = PerformanceRecord(
            timestamp=time.time(),
            model_id=model.model_id,
            task_type=task_type,
            success=success,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            actual_cost=actual_cost,
            quality_score=quality_score,
            error_type=error_type,
        )

        self._records.append(record)
        self._recent_calls.append(record)

        # 限制最近记录数量
        if len(self._recent_calls) > self._max_recent:
            self._recent_calls.pop(0)

        # 更新统计
        stats = self._stats.setdefault(
            model.model_id,
            ModelPerformanceStats(model_id=model.model_id)
        )
        stats.update(record)

        log.debug(
            "performance.recorded",
            model=model.model_id,
            task=task_type.name,
            success=success,
            latency_ms=round(latency_ms, 2),
            cost=round(actual_cost, 6),
        )

        return record

    def get_stats(self, model_id: str) -> ModelPerformanceStats | None:
        """获取指定模型的统计信息."""
        return self._stats.get(model_id)

    def get_all_stats(self) -> dict[str, ModelPerformanceStats]:
        """获取所有模型的统计信息."""
        return self._stats.copy()

    def get_success_rate(self, model_id: str, task_type: TaskType | None = None) -> float:
        """获取模型的成功率.

        Args:
            model_id: 模型ID
            task_type: 特定任务类型，None表示所有任务

        Returns:
            成功率 0-1
        """
        stats = self._stats.get(model_id)
        if not stats:
            return 1.0  # 没有记录时假设成功

        if task_type and task_type in stats.by_task:
            task_stats = stats.by_task[task_type]
            calls = task_stats["calls"]
            return task_stats["success"] / calls if calls > 0 else 1.0

        return stats.success_rate

    def get_avg_latency(self, model_id: str) -> float:
        """获取模型的平均延迟."""
        stats = self._stats.get(model_id)
        return stats.avg_latency_ms if stats else 0.0

    def get_quality_score(self, model_id: str, task_type: TaskType | None = None) -> float:
        """获取模型的质量评分."""
        stats = self._stats.get(model_id)
        if not stats:
            return 0.0

        if task_type and task_type in stats.by_task:
            return stats.by_task[task_type].get("avg_quality", 0.0)

        return stats.avg_quality_score

    def get_ranked_models(
        self,
        task_type: TaskType | None = None,
        min_calls: int = 3,
    ) -> list[tuple[str, float]]:
        """获取按综合表现排序的模型列表.

        评分公式：success_rate * 0.4 + quality_score * 0.3 + speed_score * 0.3

        Returns:
            [(model_id, score), ...] 按分数降序
        """
        ranked = []

        for model_id, stats in self._stats.items():
            if stats.total_calls < min_calls:
                continue

            success_score = stats.success_rate
            quality_score = min(stats.avg_quality_score / 0.9, 1.0)  # 归一化

            # 速度评分（越快的分数越高）
            speed_score = 1.0 / (1.0 + stats.avg_latency_ms / 1000)

            # 综合评分
            overall_score = (
                success_score * 0.4 +
                quality_score * 0.3 +
                speed_score * 0.3
            )

            # 如果有指定任务类型，考虑任务特定的表现
            if task_type and task_type in stats.by_task:
                task_stats = stats.by_task[task_type]
                task_success = task_stats["success"] / max(task_stats["calls"], 1)
                task_quality = task_stats.get("avg_quality", 0.0)
                # 加权平均
                overall_score = overall_score * 0.5 + (task_success * 0.3 + task_quality * 0.2)

            ranked.append((model_id, overall_score))

        return sorted(ranked, key=lambda x: x[1], reverse=True)

    def get_recent_errors(self, limit: int = 10) -> list[PerformanceRecord]:
        """获取最近的错误记录."""
        errors = [r for r in reversed(self._recent_calls) if not r.success]
        return errors[:limit]

    def get_summary(self) -> dict[str, Any]:
        """获取性能摘要."""
        if not self._stats:
            return {"status": "no_data"}

        total_calls = sum(s.total_calls for s in self._stats.values())
        total_cost = sum(s.total_cost for s in self._stats.values())
        avg_success_rate = sum(s.success_rate for s in self._stats.values()) / len(self._stats)

        return {
            "total_calls": total_calls,
            "total_cost": round(total_cost, 4),
            "models_tracked": len(self._stats),
            "avg_success_rate": round(avg_success_rate, 3),
            "top_models": self.get_ranked_models()[:5],
        }

    def update_quality_score(
        self,
        model_id: str,
        timestamp: float,
        quality_score: float,
    ) -> bool:
        """更新指定记录的质量评分.

        允许上层在评估响应质量后更新记录。
        """
        for record in self._records:
            if record.model_id == model_id and abs(record.timestamp - timestamp) < 1.0:
                record.quality_score = quality_score

                # 重新计算该模型的统计
                stats = self._stats.get(model_id)
                if stats:
                    # 简化处理：只更新平均质量分
                    stats.avg_quality_score = (
                        (stats.avg_quality_score * stats.total_calls + quality_score)
                        / (stats.total_calls + 1)
                    )

                return True

        return False

    def clear(self) -> None:
        """清除所有记录（谨慎使用）."""
        self._records.clear()
        self._recent_calls.clear()
        self._stats.clear()
