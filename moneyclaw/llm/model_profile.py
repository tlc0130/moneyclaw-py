"""Model profile — 模型画像，描述模型的能力、成本、特性等元数据."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class CostTier(Enum):
    """成本等级 — 基于每1K token的成本."""

    FREE = auto()       # $0 — 本地模型
    ECONOMY = auto()    # <$0.001 — 极便宜
    STANDARD = auto()   # $0.001-$0.01 — 标准
    PREMIUM = auto()    # $0.01-$0.1 — 较贵
    ULTRA = auto()      # >$0.1 — 最贵


class TaskType(Enum):
    """任务类型 — 用于匹配模型专长."""

    ANALYTICS = auto()      # 分析类：数据分析、逻辑推理、数学
    EXECUTION = auto()      # 执行类：代码生成、指令遵循、结构化输出
    CREATIVE = auto()       # 创意类：写作、头脑风暴、内容生成
    CONVERSATION = auto()   # 对话类：聊天、问答
    CODE_GENERATION = auto()  # 代码生成：专门的策略/代码编写任务


@dataclass(frozen=True)
class ModelProfile:
    """模型画像 — 包含模型的完整元数据.

    用于智能路由决策，包含能力、成本、可用性等多维度信息。
    """

    # 基础标识
    model_id: str
    """唯一标识，格式: provider/model_name，如 openai/gpt-4o"""

    provider: str
    """Provider类型: openai, anthropic, deepseek, groq, ollama等"""

    display_name: str
    """友好显示名称，如 GPT-4o"""

    # 能力评估
    capability_score: float = 0.5
    """综合能力评分 0-1，基于模型系列和版本推断"""

    task_strengths: dict[TaskType, float] = field(default_factory=dict)
    """各任务类型的专长评分，如 {TaskType.ANALYTICS: 0.9}"""

    # 成本信息
    cost_per_1k_input: float = 0.0
    """每1K输入token成本 (USD)"""

    cost_per_1k_output: float = 0.0
    """每1K输出token成本 (USD)"""

    cost_tier: CostTier = CostTier.FREE
    """成本等级，由系统根据成本自动计算"""

    # 技术规格
    context_length: int = 4096
    """上下文窗口长度"""

    supports_tools: bool = False
    """是否支持工具调用"""

    supports_vision: bool = False
    """是否支持视觉输入"""

    supports_json_mode: bool = False
    """是否支持JSON模式"""

    # 状态
    is_available: bool = True
    """当前是否可用（通过健康检查）"""

    avg_latency_ms: float = 0.0
    """平均响应延迟（毫秒）"""

    success_rate: float = 1.0
    """历史调用成功率 0-1"""

    # 扩展
    metadata: dict[str, Any] = field(default_factory=dict)
    """额外元数据，如原始API返回的信息"""

    def __post_init__(self) -> None:
        """验证并设置默认值."""
        # 确保 capability_score 在有效范围
        object.__setattr__(
            self, "capability_score",
            max(0.0, min(1.0, self.capability_score))
        )

    @property
    def estimated_cost_per_call(self) -> float:
        """估算单次调用成本（假设平均输入500 tokens，输出300 tokens）."""
        input_cost = (500 / 1000) * self.cost_per_1k_input
        output_cost = (300 / 1000) * self.cost_per_1k_output
        return input_cost + output_cost

    @property
    def effective_score(self) -> float:
        """有效评分 — 综合考虑能力和成功率."""
        return self.capability_score * self.success_rate

    def matches_task(self, task_type: TaskType, min_score: float = 0.5) -> bool:
        """检查模型是否适合指定任务类型."""
        score = self.task_strengths.get(task_type, self.capability_score * 0.8)
        return score >= min_score

    def is_cheaper_than(self, other: ModelProfile) -> bool:
        """比较成本是否低于另一个模型."""
        return self.estimated_cost_per_call < other.estimated_cost_per_call

    def __str__(self) -> str:
        return (
            f"{self.display_name} ({self.model_id}) "
            f"[capability={self.capability_score:.2f}, "
            f"cost={self.estimated_cost_per_call:.6f}, "
            f"available={self.is_available}]"
        )

    def __hash__(self) -> int:
        """使用 model_id 作为哈希值，支持在集合和字典中使用."""
        return hash(self.model_id)
