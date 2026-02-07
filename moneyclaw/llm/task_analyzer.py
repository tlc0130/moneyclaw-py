"""Task analyzer — 分析任务类型，用于匹配合适的模型."""

from __future__ import annotations

import re
from dataclasses import dataclass

from moneyclaw.llm.model_profile import TaskType


# 任务类型关键词模式
TASK_PATTERNS: dict[TaskType, list[re.Pattern]] = {
    TaskType.ANALYTICS: [
        re.compile(r"\b(analyz|analyis|data|calculate|compute|math|statistics|trend|forecast|predict|evaluate|assess|compare|research|investigate)\b", re.I),
        re.compile(r"\b(what is|how much|why does|explain|reasoning|logic|proof|verification|validation)\b", re.I),
        re.compile(r"\b(market|price|trading|financial|economic|indicator|signal|pattern)\b", re.I),
    ],
    TaskType.EXECUTION: [
        re.compile(r"\b(code|program|script|function|class|implement|build|create|generate|write|develop|debug|fix|refactor|optimize)\b", re.I),
        re.compile(r"\b(execute|run|call|invoke|api|request|query|fetch|scrape|parse|transform|convert|format|json|xml|csv)\b", re.I),
        re.compile(r"\b(sql|database|query|insert|update|delete|select|schema|table|index)\b", re.I),
        re.compile(r"\b(algorithm|workflow|pipeline|automation|schedule|task|job|cron)\b", re.I),
    ],
    TaskType.CREATIVE: [
        re.compile(r"\b(write|draft|compose|create|design|imagine|brainstorm|ideate|concept|story|narrative|poem|essay|article|blog|post|content)\b", re.I),
        re.compile(r"\b(marketing|copy|headline|slogan|tagline|ad|campaign|brand|messaging|tone|voice|style)\b", re.I),
        re.compile(r"\b(creative|innovative|unique|original|engaging|compelling|persuasive|inspiring|entertaining)\b", re.I),
    ],
    TaskType.CONVERSATION: [
        re.compile(r"\b(chat|talk|discuss|conversation|dialogue|q&a|answer|question|help|assist|support|advice|suggestion|recommendation)\b", re.I),
        re.compile(r"\b(summarize|summarization|brief|overview|extract|highlight|key points|takeaway)\b", re.I),
        re.compile(r"\b(translate|translation|interpret|clarify|explain simply|simplify|rephrase|reword)\b", re.I),
    ],
}


@dataclass
class TaskAnalysis:
    """任务分析结果."""

    primary_type: TaskType
    """主要任务类型"""

    confidence: float
    """置信度 0-1"""

    type_scores: dict[TaskType, float]
    """各任务类型的得分"""

    complexity_hint: float
    """复杂度提示 0-1，基于prompt长度和结构"""


class TaskAnalyzer:
    """任务分析器.

    分析用户prompt，识别任务类型和复杂度，用于模型选择。
    """

    # 复杂度评估阈值
    COMPLEXITY_THRESHOLDS = {
        "length": [100, 500, 1000],  # 短、中、长
        "structure": ["simple", "structured", "multi_part"],
    }

    def analyze(self, prompt: str, system: str = "") -> TaskAnalysis:
        """分析任务.

        Args:
            prompt: 用户提示词
            system: 系统提示词

        Returns:
            任务分析结果
        """
        full_text = f"{system} {prompt}".lower()

        # 计算各任务类型的得分
        type_scores: dict[TaskType, float] = {}
        for task_type, patterns in TASK_PATTERNS.items():
            score = sum(1 for p in patterns if p.search(full_text))
            type_scores[task_type] = min(score / max(len(patterns) * 0.5, 1), 1.0)

        # 确定主要任务类型
        primary_type = max(type_scores, key=type_scores.get)
        max_score = type_scores[primary_type]

        # 计算置信度
        total_score = sum(type_scores.values())
        confidence = max_score / total_score if total_score > 0 else 0.5

        # 评估复杂度
        complexity = self._assess_complexity(prompt, system)

        return TaskAnalysis(
            primary_type=primary_type,
            confidence=confidence,
            type_scores=type_scores,
            complexity_hint=complexity,
        )

    def _assess_complexity(self, prompt: str, system: str) -> float:
        """评估任务复杂度.

        基于以下因素：
        - 文本长度
        - 结构复杂度（列表、步骤、约束等）
        - 特定复杂度指示词
        """
        full_text = f"{system} {prompt}"
        length = len(full_text)

        # 基础复杂度（基于长度）
        if length < 200:
            base_complexity = 0.2
        elif length < 800:
            base_complexity = 0.4
        elif length < 2000:
            base_complexity = 0.6
        else:
            base_complexity = 0.8

        # 结构复杂度加分
        structure_score = 0.0

        # 检查是否有步骤/列表
        if re.search(r"\n\s*(\d+[\.\)]|\-|\*)\s+", prompt):
            structure_score += 0.1

        # 检查是否有多个部分
        if len(re.findall(r"\n\n", prompt)) > 2:
            structure_score += 0.1

        # 检查复杂度关键词
        complexity_keywords = [
            r"\b(complex|complicated|sophisticated|advanced|expert|professional)\b",
            r"\b(detailed|comprehensive|thorough|in-depth|extensive|elaborate)\b",
            r"\b(multiple|various|diverse|several|many|numerous)\b",
            r"\b(consider|evaluate|analyze|compare|contrast|synthesize)\b",
        ]
        for pattern in complexity_keywords:
            if re.search(pattern, full_text, re.I):
                structure_score += 0.05

        # 特殊任务类型调整
        if re.search(r"\b(code|program|implement|algorithm)\b", full_text, re.I):
            structure_score += 0.1

        if re.search(r"\b(data analysis|statistical|mathematical|proof|theorem)\b", full_text, re.I):
            structure_score += 0.15

        # 合并并限制范围
        final_complexity = min(base_complexity + structure_score, 1.0)
        return final_complexity

    def get_task_recommendation(self, analysis: TaskAnalysis) -> str:
        """获取任务类型的推荐说明."""
        recommendations = {
            TaskType.ANALYTICS: "分析类任务 - 推荐使用推理能力强、擅长数据处理的模型",
            TaskType.EXECUTION: "执行类任务 - 推荐使用代码能力强、指令遵循准确的模型",
            TaskType.CREATIVE: "创意类任务 - 推荐使用创造力强、文风优美的模型",
            TaskType.CONVERSATION: "对话类任务 - 推荐使用响应快、对话流畅的模型",
        }
        return recommendations.get(analysis.primary_type, "通用任务")


def quick_analyze(prompt: str, system: str = "") -> TaskType:
    """快速分析任务类型，返回主要类型.

    这是一个便捷函数，用于简单的任务类型识别。
    """
    analyzer = TaskAnalyzer()
    result = analyzer.analyze(prompt, system)
    return result.primary_type
