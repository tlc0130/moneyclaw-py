"""Strategy Generator — 使用AI生成、优化和管理交易策略.

这个模块允许用户通过自然语言与MoneyClaw交互，实现：
1. 策略生成 - 根据描述自动生成策略代码
2. 策略优化 - 根据执行历史优化现有策略
3. 策略管理 - 列出、启用、禁用、删除策略
"""

from __future__ import annotations

import asyncio
import inspect
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from moneyclaw.llm.smart_router import SmartRouter
from moneyclaw.llm.types import TaskRequest
from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy

log = structlog.get_logger()


@dataclass
class GeneratedStrategy:
    """生成的策略结果."""

    name: str
    description: str
    code: str
    config: dict[str, Any]
    risk_level: str = "medium"
    min_llm_layer: int = 1
    reasoning: str = ""


@dataclass
class StrategyOptimization:
    """策略优化建议."""

    strategy_name: str
    current_performance: dict[str, float]
    suggestions: list[str]
    optimized_config: dict[str, Any] | None = None
    optimized_code: str | None = None


class StrategyGenerator:
    """AI策略生成器.

    使用LLM根据用户描述生成可执行的交易策略代码。
    """

    # 策略代码模板
    STRATEGY_TEMPLATE = '''"""{description}"""

from __future__ import annotations

from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy


class {class_name}(Strategy):
    """{description}"""

    name = "{strategy_name}"
    description = """{description}"""
    risk_level = "{risk_level}"
    min_llm_layer = {min_llm_layer}

    async def setup(self) -> None:
        """初始化策略."""
        # 获取配置参数
        self.config = self._config
        {setup_code}

    async def scan(self) -> list[Opportunity]:
        """扫描市场机会."""
        opportunities = []
        {scan_code}
        return opportunities

    async def evaluate(self, opp: Opportunity) -> Score:
        """评估机会价值."""
        {evaluate_code}

    async def execute(self, opp: Opportunity) -> Result:
        """执行交易."""
        {execute_code}

    def estimate_roi(self) -> float:
        """预估ROI倍数."""
        return {estimated_roi}

    async def teardown(self) -> None:
        """清理资源."""
        {teardown_code}
'''

    def __init__(self, llm_router: SmartRouter) -> None:
        self._llm = llm_router
        self._strategies_dir = Path("strategies")

    async def generate(
        self,
        description: str,
        strategy_type: str = "general",
        risk_level: str | None = None,
        constraints: dict[str, Any] | None = None,
        suggested_name: str | None = None,
    ) -> GeneratedStrategy:
        """根据描述生成策略.

        Args:
            description: 策略的自然语言描述
            strategy_type: 策略类型 (trading/savings/automation/general)
            risk_level: 风险等级 (low/medium/high)，None表示自动判断
            constraints: 额外约束条件
            suggested_name: 建议的策略名称

        Returns:
            生成的策略对象
        """
        log.info("strategy_generator.generating", description=description[:50], suggested_name=suggested_name)

        # 构建生成提示
        prompt = self._build_generation_prompt(
            description, strategy_type, risk_level, constraints, suggested_name
        )

        # 调用LLM生成策略代码
        request = TaskRequest(
            prompt=prompt,
            system=self._get_strategy_generation_system_prompt(),
            temperature=0.2,  # 较低温度确保代码稳定性
            max_tokens=3000,
        )

        try:
            response = await self._llm.complete(request)
            generated_code = response.text

            # 解析生成的代码
            strategy = self._parse_generated_code(
                generated_code, description, risk_level
            )

            log.info(
                "strategy_generator.generated",
                name=strategy.name,
                risk_level=strategy.risk_level,
            )
            return strategy

        except Exception as e:
            log.exception("strategy_generator.failed")
            raise StrategyGenerationError(f"策略生成失败: {e}") from e

    def _build_generation_prompt(
        self,
        description: str,
        strategy_type: str,
        risk_level: str | None,
        constraints: dict[str, Any] | None,
        suggested_name: str | None = None,
    ) -> str:
        """构建策略生成提示."""
        prompt = f"""请根据以下描述生成一个MoneyClaw交易策略:

## 策略描述
{description}

## 策略类型
{strategy_type}

"""
        if suggested_name:
            prompt += f"## 建议的策略名称\n{suggested_name}\n\n"

        if risk_level:
            prompt += f"## 风险等级\n{risk_level}\n\n"

        if constraints:
            prompt += "## 约束条件\n"
            for key, value in constraints.items():
                prompt += f"- {key}: {value}\n"
            prompt += "\n"

        prompt += """## 要求
1. 策略类必须继承自 Strategy 基类
2. 必须实现 scan(), evaluate(), execute(), estimate_roi() 方法
3. 使用合理的默认配置参数
4. 代码要健壮，包含适当的错误处理
5. 添加清晰的中文注释

## 输出格式
请输出完整的Python代码，包含：
1. 导入语句
2. 策略类定义
3. 默认配置字典 (DEFAULT_CONFIG)

代码示例:
```python
class MyStrategy(Strategy):
    name = "my_strategy"
    description = "策略描述"
    risk_level = "medium"
    min_llm_layer = 1
    
    async def scan(self) -> list[Opportunity]:
        # 扫描逻辑
        pass
```
"""
        return prompt

    def _get_strategy_generation_system_prompt(self) -> str:
        """获取策略生成的系统提示."""
        return """你是一个专业的量化交易策略生成专家。

你的任务是根据用户的自然语言描述，生成完整的、可执行的MoneyClaw策略代码。

## MoneyClaw策略框架

### 基类方法
- `setup()`: 初始化策略，读取配置
- `scan() -> list[Opportunity]`: 扫描市场，返回机会列表
- `evaluate(opp) -> Score`: 评估机会，返回0-1的分数
- `execute(opp) -> Result`: 执行交易，返回结果
- `estimate_roi() -> float`: 预估ROI倍数
- `teardown()`: 清理资源

### 数据类
- `Opportunity`: 机会对象，包含 title, description, amount, metadata
- `Score`: 评分对象，包含 value (0-1), confidence, reasoning
- `Result`: 结果对象，包含 success, pnl, error

### 策略类型指南
1. **trading**: 交易类策略，关注买入卖出时机
2. **savings**: 省钱类策略，关注降低成本
3. **automation**: 自动化策略，关注定时任务
4. **general**: 通用策略，灵活实现

### 代码规范
1. 使用类型注解
2. 使用 async/await
3. 包含适当的日志记录
4. 处理边界情况
5. 配置参数外部化"""

    def _parse_generated_code(
        self,
        code: str,
        description: str,
        risk_level: str | None,
    ) -> GeneratedStrategy:
        """解析生成的代码."""
        # 提取类名
        class_match = re.search(r"class\s+(\w+)\s*\(\s*Strategy\s*\)", code)
        if not class_match:
            raise StrategyGenerationError("生成的代码中没有找到Strategy子类")

        class_name = class_match.group(1)
        strategy_name = self._to_snake_case(class_name)

        # 提取风险等级
        if risk_level is None:
            risk_match = re.search(r'risk_level\s*=\s*["\'](\w+)["\']', code)
            risk_level = risk_match.group(1) if risk_match else "medium"

        # 提取LLM层级
        layer_match = re.search(r'min_llm_layer\s*=\s*(\d+)', code)
        min_llm_layer = int(layer_match.group(1)) if layer_match else 1

        # 提取配置
        config = self._extract_config(code)

        return GeneratedStrategy(
            name=strategy_name,
            description=description,
            code=code,
            config=config,
            risk_level=risk_level,
            min_llm_layer=min_llm_layer,
            reasoning="基于用户描述生成的策略",
        )

    def _extract_config(self, code: str) -> dict[str, Any]:
        """从代码中提取配置参数."""
        config = {}

        # 尝试匹配 DEFAULT_CONFIG 字典
        config_match = re.search(
            r"DEFAULT_CONFIG\s*=\s*(\{[^}]+\})", code, re.DOTALL
        )
        if config_match:
            try:
                # 安全地评估字典字面量
                config_str = config_match.group(1)
                # 简单的解析，实际使用时应更安全
                config = eval(config_str, {"__builtins__": {}}, {})
            except Exception:
                pass

        return config

    def _to_snake_case(self, name: str) -> str:
        """转换驼峰命名为下划线命名."""
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    async def save_strategy(self, strategy: GeneratedStrategy) -> Path:
        """保存生成的策略到文件.

        Args:
            strategy: 生成的策略

        Returns:
            策略目录路径
        """
        # 创建策略目录
        strategy_dir = self._strategies_dir / strategy.name
        strategy_dir.mkdir(parents=True, exist_ok=True)

        # 写入代码文件
        code_file = strategy_dir / "__init__.py"
        code_file.write_text(strategy.code, encoding="utf-8")

        # 写入配置文件
        import yaml

        config_file = strategy_dir / "config.yaml"
        yaml_content = {
            "name": strategy.name,
            "description": strategy.description,
            "risk_level": strategy.risk_level,
            "min_llm_layer": strategy.min_llm_layer,
            "enabled": True,
            "parameters": strategy.config,
        }
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(yaml_content, f, allow_unicode=True, sort_keys=False)

        log.info("strategy_generator.saved", path=str(strategy_dir))
        return strategy_dir


class StrategyOptimizer:
    """策略优化器.

    分析策略执行历史，提供优化建议。
    """

    def __init__(self, llm_router: SmartRouter) -> None:
        self._llm = llm_router

    async def analyze_performance(
        self,
        strategy_name: str,
        execution_history: list[dict[str, Any]],
        current_config: dict[str, Any],
    ) -> StrategyOptimization:
        """分析策略表现并提供优化建议.

        Args:
            strategy_name: 策略名称
            execution_history: 执行历史记录
            current_config: 当前配置

        Returns:
            优化建议
        """
        log.info("strategy_optimizer.analyzing", strategy=strategy_name)

        # 计算当前表现指标
        performance = self._calculate_metrics(execution_history)

        # 构建分析提示
        prompt = self._build_optimization_prompt(
            strategy_name, execution_history, current_config, performance
        )

        request = TaskRequest(
            prompt=prompt,
            system=self._get_optimization_system_prompt(),
            temperature=0.3,
            max_tokens=2000,
        )

        try:
            response = await self._llm.complete(request)
            optimization = self._parse_optimization_response(
                response.text, strategy_name, performance
            )

            log.info(
                "strategy_optimizer.completed",
                strategy=strategy_name,
                suggestions=len(optimization.suggestions),
            )
            return optimization

        except Exception as e:
            log.exception("strategy_optimizer.failed")
            return StrategyOptimization(
                strategy_name=strategy_name,
                current_performance=performance,
                suggestions=[f"分析失败: {e}"],
            )

    def _calculate_metrics(self, history: list[dict[str, Any]]) -> dict[str, float]:
        """计算性能指标."""
        if not history:
            return {"total_trades": 0, "win_rate": 0, "avg_pnl": 0, "total_pnl": 0}

        total = len(history)
        wins = sum(1 for h in history if h.get("pnl", 0) > 0)
        pnls = [h.get("pnl", 0) for h in history]

        return {
            "total_trades": total,
            "win_rate": wins / total if total > 0 else 0,
            "avg_pnl": sum(pnls) / total if total > 0 else 0,
            "total_pnl": sum(pnls),
            "max_drawdown": min(pnls) if pnls else 0,
            "max_profit": max(pnls) if pnls else 0,
        }

    def _build_optimization_prompt(
        self,
        strategy_name: str,
        history: list[dict[str, Any]],
        config: dict[str, Any],
        performance: dict[str, float],
    ) -> str:
        """构建优化提示."""
        return f"""请分析以下策略的表现并提供优化建议:

## 策略名称
{strategy_name}

## 当前配置
```yaml
{config}
```

## 执行历史摘要
- 总交易次数: {performance['total_trades']}
- 胜率: {performance['win_rate']:.2%}
- 平均盈亏: {performance['avg_pnl']:.2f}
- 总盈亏: {performance['total_pnl']:.2f}
- 最大回撤: {performance['max_drawdown']:.2f}
- 最大盈利: {performance['max_profit']:.2f}

## 详细执行记录
{history[:10]}  # 最近10条

## 请提供
1. 对当前策略表现的分析
2. 具体的优化建议（3-5条）
3. 推荐的配置参数调整
4. 风险管控建议
"""

    def _get_optimization_system_prompt(self) -> str:
        """获取优化系统提示."""
        return """你是一个专业的量化交易策略优化专家。

你的任务是分析策略的历史表现，提供具体的、可执行的优化建议。

## 分析维度
1. 胜率分析 - 交易成功的比例
2. 盈亏比分析 - 平均盈利 vs 平均亏损
3. 频率分析 - 交易频率是否合适
4. 风险分析 - 最大回撤、波动率
5. 参数敏感性 - 哪些参数影响最大

## 建议类型
1. 参数调整 - 具体数值建议
2. 逻辑优化 - 算法改进建议
3. 风险控制 - 止损止盈建议
4. 市场环境 - 适用市场条件建议"""

    def _parse_optimization_response(
        self, content: str, strategy_name: str, performance: dict[str, float]
    ) -> StrategyOptimization:
        """解析优化响应."""
        # 提取建议列表（简单实现）
        suggestions = []
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.")):
                suggestions.append(line.lstrip("- *0123456789.").strip())

        if not suggestions:
            suggestions = ["暂无具体建议"]

        return StrategyOptimization(
            strategy_name=strategy_name,
            current_performance=performance,
            suggestions=suggestions,
        )


class StrategyGenerationError(Exception):
    """策略生成错误."""

    pass
