"""Model intelligence — 根据模型名称智能推断能力和成本."""

from __future__ import annotations

import re
from typing import Any

from moneyclaw.llm.model_profile import CostTier, ModelProfile, TaskType

# 模型能力评分映射表 — 基于模型系列和版本
CAPABILITY_PATTERNS: list[tuple[re.Pattern, float, dict[TaskType, float]]] = [
    # Ultra-high capability models
    (re.compile(r"gpt-4o$|gpt-4-turbo|claude-3-opus|gemini-1\.5-pro|command-r-plus|moonshot/kimi-k2"), 0.95, {
        TaskType.ANALYTICS: 0.95, TaskType.EXECUTION: 0.95, TaskType.CREATIVE: 0.90, TaskType.CONVERSATION: 0.95
    }),
    # High capability models
    (re.compile(r"gpt-4|claude-3-sonnet|gemini-1\.5-flash|llama-3\.3|llama-3\.2|llama-3\.1|qwen2\.5-72b|deepseek-chat|command-r|moonshot/kimi-k1\.5|moonshot/kimi-k1"), 0.85, {
        TaskType.ANALYTICS: 0.90, TaskType.EXECUTION: 0.85, TaskType.CREATIVE: 0.85, TaskType.CONVERSATION: 0.90
    }),
    # Medium-high capability models
    (re.compile(r"gpt-4o-mini|claude-3-haiku|gemini-1\.0|llama-3|qwen2\.5|mistral-large|mixtral|moonshot/kimi"), 0.75, {
        TaskType.ANALYTICS: 0.75, TaskType.EXECUTION: 0.80, TaskType.CREATIVE: 0.75, TaskType.CONVERSATION: 0.80
    }),
    # Standard capability models
    (re.compile(r"gpt-3\.5|claude-instant|gemini-pro|phi-4|phi-3|mistral-medium|codellama"), 0.65, {
        TaskType.ANALYTICS: 0.65, TaskType.EXECUTION: 0.70, TaskType.CREATIVE: 0.65, TaskType.CONVERSATION: 0.70
    }),
    # Lower capability models (but still useful for simple tasks)
    (re.compile(r"llama-2|mistral|qwen2|phi"), 0.55, {
        TaskType.ANALYTICS: 0.55, TaskType.EXECUTION: 0.60, TaskType.CREATIVE: 0.55, TaskType.CONVERSATION: 0.60
    }),
]

# 默认能力评分
DEFAULT_CAPABILITY = 0.50
DEFAULT_TASK_STRENGTHS: dict[TaskType, float] = {
    TaskType.ANALYTICS: 0.50, TaskType.EXECUTION: 0.50,
    TaskType.CREATIVE: 0.50, TaskType.CONVERSATION: 0.50
}

# 模型成本映射表 (USD per 1K tokens) — 输入:输出
COST_PATTERNS: list[tuple[re.Pattern, tuple[float, float]]] = [
    # OpenAI
    (re.compile(r"openai/gpt-4o$"), (0.00250, 0.01000)),
    (re.compile(r"openai/gpt-4o-mini"), (0.00015, 0.00060)),
    (re.compile(r"openai/gpt-4-turbo"), (0.01000, 0.03000)),
    (re.compile(r"openai/gpt-4"), (0.03000, 0.06000)),
    (re.compile(r"openai/gpt-3\.5-turbo"), (0.00050, 0.00150)),
    # Anthropic
    (re.compile(r"anthropic/claude-3-opus"), (0.01500, 0.07500)),
    (re.compile(r"anthropic/claude-3-sonnet"), (0.00300, 0.01500)),
    (re.compile(r"anthropic/claude-3-haiku"), (0.00025, 0.00125)),
    # DeepSeek
    (re.compile(r"deepseek/deepseek-chat"), (0.00014, 0.00028)),
    (re.compile(r"deepseek/deepseek-coder"), (0.00014, 0.00028)),
    # Groq (very cheap)
    (re.compile(r"groq/"), (0.00001, 0.00001)),  # Groq uses per-token pricing
    # Google
    (re.compile(r"gemini-1\.5-pro"), (0.00125, 0.00500)),
    (re.compile(r"gemini-1\.5-flash"), (0.000075, 0.00030)),
    (re.compile(r"gemini-1\.0"), (0.00050, 0.00150)),
    # Moonshot (月之暗面)
    (re.compile(r"moonshot/kimi-k2"), (0.00400, 0.01600)),  # 高性能模型
    (re.compile(r"moonshot/kimi-k1\.5"), (0.00200, 0.00800)),
    (re.compile(r"moonshot/kimi-k1"), (0.00150, 0.00600)),
    (re.compile(r"moonshot/kimi"), (0.00100, 0.00400)),  # 标准版
    # Ollama (free/local)
    (re.compile(r"ollama/"), (0.0, 0.0)),
]

# 上下文长度映射
CONTEXT_LENGTH_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"moonshot/kimi-k2"), 256000),  # Kimi K2 支持 256K 上下文
    (re.compile(r"gpt-4o$|claude-3-opus|gemini-1\.5-pro|command-r-plus"), 128000),
    (re.compile(r"gpt-4-turbo|claude-3-sonnet|gemini-1\.5-flash|llama-3\.3|command-r|moonshot/kimi-k1"), 128000),
    (re.compile(r"gpt-4|claude-3-haiku|gpt-4o-mini|gemini-1\.0|moonshot/kimi"), 128000),
    (re.compile(r"gpt-3\.5-turbo"), 16385),
    (re.compile(r"llama-3|qwen2\.5"), 32768),
    (re.compile(r"deepseek-chat|deepseek-coder"), 64000),
]

# 功能支持映射
TOOL_SUPPORT_PATTERNS: list[tuple[re.Pattern, bool]] = [
    (re.compile(r"gpt-4|gpt-3\.5-turbo|claude-3|gemini-1\.5|llama-3\.2|mistral-large|mixtral|moonshot/kimi"), True),
]

VISION_SUPPORT_PATTERNS: list[tuple[re.Pattern, bool]] = [
    (re.compile(r"gpt-4o|claude-3|gemini-1\.5|llama-3\.2-11b|llama-3\.2-90b|moonshot/kimi-k2"), True),
]

JSON_MODE_PATTERNS: list[tuple[re.Pattern, bool]] = [
    (re.compile(r"gpt-4|gpt-3\.5-turbo|claude-3|gemini-1\.5|mistral|moonshot/kimi"), True),
]


def _match_pattern(value: str, patterns: list[tuple[re.Pattern, Any]], default: Any) -> Any:
    """匹配第一个满足正则的模式."""
    for pattern, result in patterns:
        if pattern.search(value.lower()):
            return result
    return default


def infer_capability(model_id: str) -> tuple[float, dict[TaskType, float]]:
    """根据模型ID推断能力评分和任务专长.

    Args:
        model_id: 模型标识，如 "openai/gpt-4o"

    Returns:
        (综合能力评分, 各任务类型评分字典)
    """
    for pattern, score, strengths in CAPABILITY_PATTERNS:
        if pattern.search(model_id.lower()):
            return score, strengths.copy()
    return DEFAULT_CAPABILITY, DEFAULT_TASK_STRENGTHS.copy()


def infer_cost(model_id: str) -> tuple[float, float, CostTier]:
    """根据模型ID推断成本信息.

    Args:
        model_id: 模型标识

    Returns:
        (输入成本/1K, 输出成本/1K, 成本等级)
    """
    input_cost, output_cost = _match_pattern(model_id, COST_PATTERNS, (0.0, 0.0))

    # 计算成本等级
    avg_cost = (input_cost + output_cost) / 2
    if input_cost == 0 and output_cost == 0:
        tier = CostTier.FREE
    elif avg_cost < 0.001:
        tier = CostTier.ECONOMY
    elif avg_cost < 0.01:
        tier = CostTier.STANDARD
    elif avg_cost < 0.1:
        tier = CostTier.PREMIUM
    else:
        tier = CostTier.ULTRA

    return input_cost, output_cost, tier


def infer_context_length(model_id: str) -> int:
    """根据模型ID推断上下文长度."""
    return _match_pattern(model_id, CONTEXT_LENGTH_PATTERNS, 4096)


def infer_features(model_id: str) -> dict[str, bool]:
    """推断模型支持的功能特性."""
    return {
        "supports_tools": _match_pattern(model_id, TOOL_SUPPORT_PATTERNS, False),
        "supports_vision": _match_pattern(model_id, VISION_SUPPORT_PATTERNS, False),
        "supports_json_mode": _match_pattern(model_id, JSON_MODE_PATTERNS, False),
    }


def extract_display_name(model_id: str) -> str:
    """从model_id提取友好显示名称.

    Examples:
        "openai/gpt-4o" -> "GPT-4o"
        "anthropic/claude-3-opus" -> "Claude 3 Opus"
        "ollama/qwen2.5:7b" -> "Qwen 2.5 7B"
    """
    # 提取模型名称部分
    parts = model_id.split("/")
    name = parts[-1] if len(parts) > 1 else model_id

    # 标准化命名
    name = name.replace(":", " ").replace("-", " ").replace("_", " ")

    # 特殊处理
    replacements = {
        "gpt": "GPT",
        "claude": "Claude",
        "gemini": "Gemini",
        "llama": "Llama",
        "qwen": "Qwen",
        "mistral": "Mistral",
        "mixtral": "Mixtral",
        "phi": "Phi",
        "deepseek": "DeepSeek",
        "command": "Command",
        "kimi": "Kimi",
        "moonshot": "Moonshot",
    }

    words = name.split()
    result = []
    for word in words:
        lower = word.lower()
        if lower in replacements:
            result.append(replacements[lower])
        elif word.isdigit() or (len(word) > 1 and word[1:].isdigit()):
            # 保留数字版本号如 "4o", "3.5", "7b"
            result.append(word.upper() if word.endswith("b") else word)
        else:
            result.append(word.capitalize())

    return " ".join(result)


def create_profile_from_model_id(model_id: str, provider: str, **extra_metadata: Any) -> ModelProfile:
    """从模型ID自动创建完整的ModelProfile.

    这是智能模型注册的核心函数，根据模型名称自动推断所有属性。

    Args:
        model_id: 模型唯一标识，格式 provider/model_name
        provider: Provider名称
        **extra_metadata: 额外元数据

    Returns:
        完整的ModelProfile实例
    """
    # 推断各项属性
    capability_score, task_strengths = infer_capability(model_id)
    cost_input, cost_output, cost_tier = infer_cost(model_id)
    context_length = infer_context_length(model_id)
    features = infer_features(model_id)
    display_name = extract_display_name(model_id)

    return ModelProfile(
        model_id=model_id,
        provider=provider,
        display_name=display_name,
        capability_score=capability_score,
        task_strengths=task_strengths,
        cost_per_1k_input=cost_input,
        cost_per_1k_output=cost_output,
        cost_tier=cost_tier,
        context_length=context_length,
        supports_tools=features["supports_tools"],
        supports_vision=features["supports_vision"],
        supports_json_mode=features["supports_json_mode"],
        metadata=extra_metadata,
    )
