"""Strategy Generator V2 — 使用AI自由编写策略代码.

这个模块允许用户通过自然语言与MoneyClaw交互，实现：
1. 自由代码生成 - AI直接编写完整策略代码，无模板限制
2. 策略优化 - 根据执行历史和版本信息优化现有策略
3. 版本管理 - 集成版本控制，自动保存每次生成的版本
4. 迭代改进 - 支持基于反馈的代码迭代优化
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml

from moneyclaw.agent.strategy_version import StrategyVersionManager
from moneyclaw.llm.model_profile import TaskType
from moneyclaw.llm.smart_router import SmartRouter
from moneyclaw.llm.types import TaskRequest

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
    version_id: str | None = None  # 关联的版本ID


@dataclass
class StrategyOptimization:
    """策略优化建议."""

    strategy_name: str
    current_performance: dict[str, float]
    suggestions: list[str]
    optimized_config: dict[str, Any] | None = None
    optimized_code: str | None = None
    version_id: str | None = None  # 新版本ID


class StrategyGeneratorV2:
    """AI策略生成器 V2.

    使用专门的代码生成模型（如 Gemini）根据用户描述
    自由编写完整的交易策略代码，无需模板限制。
    """

    def __init__(self, llm_router: SmartRouter) -> None:
        self._llm = llm_router
        self._strategies_dir = Path("strategies")
        self._version_manager = StrategyVersionManager(self._strategies_dir)

    async def generate(
        self,
        description: str,
        strategy_type: str = "general",
        risk_level: str | None = None,
        constraints: dict[str, Any] | None = None,
        suggested_name: str | None = None,
        preferred_model: str | None = None,
    ) -> GeneratedStrategy:
        """根据描述生成策略代码.

        Args:
            description: 策略的自然语言描述
            strategy_type: 策略类型 (trading/savings/automation/general)
            risk_level: 风险等级 (low/medium/high)，None表示自动判断
            constraints: 额外约束条件
            suggested_name: 建议的策略名称
            preferred_model: 偏好的模型（如 gemini-1.5-pro）

        Returns:
            生成的策略对象
        """
        log.info(
            "strategy_generator_v2.generating",
            description=description[:50],
            suggested_name=suggested_name,
        )

        # 构建代码生成提示
        prompt = self._build_code_generation_prompt(
            description, strategy_type, risk_level, constraints, suggested_name
        )

        # 调用LLM生成策略代码 - 使用 CODE_GENERATION 任务类型
        request = TaskRequest(
            prompt=prompt,
            system=self._get_code_generation_system_prompt(),
            task_type=TaskType.CODE_GENERATION,
            temperature=0.2,  # 较低温度确保代码稳定性
            max_tokens=4000,  # 代码生成需要更多token
            preferred_model=preferred_model,
        )

        try:
            response = await self._llm.complete(request)
            generated_code = self._extract_code(response.text)

            # 解析生成的代码
            strategy = self._parse_generated_code(
                generated_code, description, risk_level
            )

            # 保存版本
            version = self._version_manager.save_version(
                strategy_name=strategy.name,
                code=strategy.code,
                description=strategy.description,
                author="ai",
                change_summary=f"初始生成: {strategy_type} 类型策略",
            )
            strategy.version_id = version.version_id

            log.info(
                "strategy_generator_v2.generated",
                name=strategy.name,
                risk_level=strategy.risk_level,
                version_id=version.version_id,
            )
            return strategy

        except Exception as e:
            log.exception("strategy_generator_v2.failed")
            raise StrategyGenerationError(f"策略生成失败: {e}") from e

    async def iterate(
        self,
        strategy_name: str,
        feedback: str,
        current_version_id: str | None = None,
    ) -> GeneratedStrategy:
        """基于反馈迭代优化策略.

        Args:
            strategy_name: 策略名称
            feedback: 用户的改进反馈
            current_version_id: 当前版本ID，None则使用最新版本

        Returns:
            优化后的策略
        """
        log.info(
            "strategy_generator_v2.iterating",
            strategy=strategy_name,
            feedback=feedback[:50],
        )

        # 获取当前代码
        if current_version_id:
            current_code = self._version_manager.get_version_code(
                strategy_name, current_version_id
            )
            current_version = self._version_manager.get_version(strategy_name, current_version_id)
        else:
            current_version = self._version_manager.get_latest_version(strategy_name)
            current_code = self._version_manager.get_version_code(
                strategy_name, current_version.version_id
            ) if current_version else None

        if not current_code:
            raise StrategyGenerationError(f"找不到策略 {strategy_name} 的代码")

        # 构建迭代提示
        prompt = self._build_iteration_prompt(current_code, feedback, current_version)

        request = TaskRequest(
            prompt=prompt,
            system=self._get_code_generation_system_prompt(),
            task_type=TaskType.CODE_GENERATION,
            temperature=0.25,
            max_tokens=4000,
        )

        try:
            response = await self._llm.complete(request)
            optimized_code = self._extract_code(response.text)

            # 解析优化后的代码
            strategy = self._parse_generated_code(
                optimized_code,
                current_version.description if current_version else f"迭代优化: {strategy_name}",
                None,
            )

            # 保存新版本
            version = self._version_manager.save_version(
                strategy_name=strategy.name,
                code=strategy.code,
                description=strategy.description,
                author="ai",
                change_summary=f"基于反馈迭代: {feedback[:100]}...",
                tags=["iteration"],
            )
            strategy.version_id = version.version_id

            log.info(
                "strategy_generator_v2.iterated",
                name=strategy.name,
                from_version=current_version.version_id if current_version else None,
                to_version=version.version_id,
            )
            return strategy

        except Exception as e:
            log.exception("strategy_generator_v2.iteration_failed")
            raise StrategyGenerationError(f"策略迭代失败: {e}") from e

    def _build_code_generation_prompt(
        self,
        description: str,
        strategy_type: str,
        risk_level: str | None,
        constraints: dict[str, Any] | None,
        suggested_name: str | None = None,
    ) -> str:
        """构建代码生成提示."""
        prompt = f"""请根据以下描述，编写一个完整的 MoneyClaw 交易策略。

## 策略描述
{description}

## 策略类型
{strategy_type}

"""
        if suggested_name:
            prompt += f"## 建议的策略类名\n{suggested_name}\n\n"

        if risk_level:
            prompt += f"## 风险等级\n{risk_level}\n\n"

        if constraints:
            prompt += "## 约束条件\n"
            for key, value in constraints.items():
                prompt += f"- {key}: {value}\n"
            prompt += "\n"

        prompt += """## 代码要求

请输出**完整、可直接运行的 Python 代码**，包含：

1. **所有必要的导入语句** - 包括标准库、第三方库和 MoneyClaw 内部模块
2. **完整的策略类** - 继承自 Strategy 基类
3. **清晰的类属性** - name, description, risk_level, min_llm_layer
4. **完整的生命周期方法** - setup(), scan(), evaluate(), execute(), estimate_roi(), teardown()
5. **详细的注释** - 使用中文注释解释关键逻辑
6. **类型注解** - 使用 Python 类型提示
7. **错误处理** - 包含适当的 try-except 块
8. **配置参数** - 使用 DEFAULT_CONFIG 字典定义可配置参数

## MoneyClaw 基类参考

```python
from moneyclaw.plugins.base import Opportunity, Result, Score, Strategy

class Opportunity:
    title: str
    description: str
    amount: float
    metadata: dict[str, Any]

class Score:
    value: float  # 0-1
    confidence: float  # 0-1
    reasoning: str

class Result:
    success: bool
    pnl: float
    error: str | None
    metadata: dict[str, Any]

class Strategy:
    name: str
    description: str
    risk_level: str  # "low", "medium", "high"
    min_llm_layer: int  # 1-5
    
    async def setup(self) -> None: ...
    async def scan(self) -> list[Opportunity]: ...
    async def evaluate(self, opp: Opportunity) -> Score: ...
    async def execute(self, opp: Opportunity) -> Result: ...
    def estimate_roi(self) -> float: ...
    async def teardown(self) -> None: ...
```

## 输出格式

请直接输出 Python 代码，使用 ```python 代码块包裹。代码应该：
- 完整且可运行
- 遵循 PEP 8 规范
- 包含适当的日志记录
- 处理边界情况和异常
"""
        return prompt

    def _build_iteration_prompt(
        self,
        current_code: str,
        feedback: str,
        current_version: Any | None = None,
    ) -> str:
        """构建迭代优化提示."""
        version_info = f"当前版本: {current_version.version_id[:8]}" if current_version else ""
        
        return f"""请基于以下现有策略代码和用户反馈，进行迭代优化。

{version_info}

## 当前策略代码

```python
{current_code}
```

## 用户反馈/改进需求

{feedback}

## 优化要求

1. **保持原有功能** - 不要删除已有功能，除非明确需要替换
2. **针对性改进** - 重点解决反馈中提到的问题
3. **代码质量** - 保持代码清晰、健壮
4. **完整输出** - 输出完整的优化后代码

请直接输出优化后的 Python 代码。
"""

    def _get_code_generation_system_prompt(self) -> str:
        """获取代码生成的系统提示."""
        return """你是一个专业的量化交易策略代码生成专家。

你的任务是编写完整、健壮、可直接运行的 MoneyClaw 策略代码。

## 核心原则

1. **代码完整性** - 输出完整的可运行代码，不省略任何部分
2. **健壮性** - 包含错误处理、边界检查、类型注解
3. **可读性** - 使用中文注释、清晰的命名、合理的结构
4. **实用性** - 代码应该能处理真实的市场情况

## MoneyClaw 策略框架详解

### 生命周期方法
- `setup()`: 初始化策略，读取配置，建立连接
- `scan() -> list[Opportunity]`: 扫描市场，发现机会
- `evaluate(opp) -> Score`: 评估机会，返回0-1分数和置信度
- `execute(opp) -> Result`: 执行交易，返回结果
- `estimate_roi() -> float`: 预估ROI倍数，用于排序
- `teardown()`: 清理资源，关闭连接

### 配置参数规范
使用 DEFAULT_CONFIG 类属性定义：
```python
DEFAULT_CONFIG = {
    "param_name": default_value,
    "min_amount": 100.0,
    "max_slippage": 0.01,
}
```

### 日志记录
使用 structlog：
```python
import structlog
log = structlog.get_logger()

# 在方法中
log.info("strategy.event", key="value")
```

### 错误处理
```python
async def execute(self, opp: Opportunity) -> Result:
    try:
        # 执行逻辑
        return Result(success=True, pnl=profit)
    except Exception as e:
        log.error("execution.failed", error=str(e))
        return Result(success=False, pnl=0.0, error=str(e))
```

请始终输出完整、可直接运行的代码。"""

    def _extract_code(self, content: str) -> str:
        """从响应内容中提取代码."""
        # 尝试匹配 ```python 代码块
        code_match = re.search(r"```python\n(.*?)```", content, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()
        
        # 尝试匹配 ``` 代码块
        code_match = re.search(r"```\n(.*?)```", content, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()
        
        # 如果没有代码块标记，返回整个内容
        return content.strip()

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
            raise StrategyGenerationError("生成的代码中没有找到 Strategy 子类")

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
            reasoning="由AI自由生成",
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
                config_str = config_match.group(1)
                # 安全地评估字典字面量
                config = eval(config_str, {"__builtins__": {}}, {})
            except Exception:
                pass

        return config

    def _to_snake_case(self, name: str) -> str:
        """转换驼峰命名为下划线命名."""
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    async def save_strategy(
        self, strategy: GeneratedStrategy, activate: bool = True
    ) -> Path:
        """保存生成的策略到文件.

        Args:
            strategy: 生成的策略
            activate: 是否立即激活策略

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
        config_file = strategy_dir / "config.yaml"
        yaml_content = {
            "name": strategy.name,
            "description": strategy.description,
            "risk_level": strategy.risk_level,
            "min_llm_layer": strategy.min_llm_layer,
            "enabled": activate,
            "version_id": strategy.version_id,
            "parameters": strategy.config,
        }
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(yaml_content, f, allow_unicode=True, sort_keys=False)

        log.info(
            "strategy_generator_v2.saved",
            path=str(strategy_dir),
            activated=activate,
        )
        return strategy_dir

    def get_version_manager(self) -> StrategyVersionManager:
        """获取版本管理器实例."""
        return self._version_manager


class StrategyOptimizerV2:
    """策略优化器 V2.

    基于执行历史和版本信息，使用AI优化策略代码。
    """

    def __init__(self, llm_router: SmartRouter) -> None:
        self._llm = llm_router
        self._version_manager = StrategyVersionManager()

    async def optimize(
        self,
        strategy_name: str,
        execution_history: list[dict[str, Any]],
        optimization_goal: str = "improve_performance",
    ) -> StrategyOptimization:
        """分析策略表现并提供优化建议.

        Args:
            strategy_name: 策略名称
            execution_history: 执行历史记录
            optimization_goal: 优化目标

        Returns:
            优化结果，包含优化后的代码
        """
        log.info("strategy_optimizer_v2.optimizing", strategy=strategy_name)

        # 获取最新版本代码
        latest_version = self._version_manager.get_latest_version(strategy_name)
        if not latest_version:
            raise StrategyGenerationError(f"找不到策略 {strategy_name}")

        current_code = self._version_manager.get_version_code(
            strategy_name, latest_version.version_id
        )
        if not current_code:
            raise StrategyGenerationError(f"找不到策略 {strategy_name} 的代码")

        # 计算当前表现指标
        performance = self._calculate_metrics(execution_history)

        # 构建优化提示
        prompt = self._build_optimization_prompt(
            strategy_name, current_code, execution_history, performance, optimization_goal
        )

        request = TaskRequest(
            prompt=prompt,
            system=self._get_optimization_system_prompt(),
            task_type=TaskType.CODE_GENERATION,
            temperature=0.3,
            max_tokens=4000,
        )

        try:
            response = await self._llm.complete(request)
            optimized_code = self._extract_code(response.text)

            # 保存优化后的版本
            version = self._version_manager.save_version(
                strategy_name=strategy_name,
                code=optimized_code,
                description=latest_version.description,
                author="optimizer",
                change_summary=f"自动优化: {optimization_goal}",
                performance=performance,
                tags=["auto-optimized", optimization_goal],
            )

            log.info(
                "strategy_optimizer_v2.completed",
                strategy=strategy_name,
                new_version=version.version_id,
            )

            return StrategyOptimization(
                strategy_name=strategy_name,
                current_performance=performance,
                suggestions=["基于执行历史的自动优化"],
                optimized_code=optimized_code,
                version_id=version.version_id,
            )

        except Exception as e:
            log.exception("strategy_optimizer_v2.failed")
            return StrategyOptimization(
                strategy_name=strategy_name,
                current_performance=performance,
                suggestions=[f"优化失败: {e}"],
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
        current_code: str,
        history: list[dict[str, Any]],
        performance: dict[str, float],
        optimization_goal: str,
    ) -> str:
        """构建优化提示."""
        return f"""请分析以下策略的表现并提供优化后的代码.

## 策略名称
{strategy_name}

## 优化目标
{optimization_goal}

## 当前代码

```python
{current_code}
```

## 执行历史摘要
- 总交易次数: {performance['total_trades']}
- 胜率: {performance['win_rate']:.2%}
- 平均盈亏: {performance['avg_pnl']:.2f}
- 总盈亏: {performance['total_pnl']:.2f}
- 最大回撤: {performance['max_drawdown']:.2f}
- 最大盈利: {performance['max_profit']:.2f}

## 详细执行记录（最近10条）
{history[-10:]}

## 优化要求

1. **针对性优化** - 基于表现数据改进策略逻辑
2. **保持接口兼容** - 方法签名和返回值类型不变
3. **改进异常处理** - 增强鲁棒性
4. **添加/优化日志** - 便于后续分析
5. **输出完整代码** - 可直接替换原代码

请输出优化后的完整 Python 代码。
"""

    def _get_optimization_system_prompt(self) -> str:
        """获取优化系统提示."""
        return """你是一个专业的量化交易策略优化专家。

你的任务是基于策略的历史表现，改进策略代码以提高性能。

## 优化维度
1. **参数调优** - 调整阈值、窗口大小等参数
2. **逻辑改进** - 改进信号生成、风险评估逻辑
3. **风控加强** - 添加止损、仓位控制
4. **效率提升** - 减少不必要的计算

## 代码规范
- 保持原有类名和接口
- 优化核心算法逻辑
- 增强错误处理
- 添加性能监控日志

输出必须是完整的可运行代码。"""

    def _extract_code(self, content: str) -> str:
        """从响应内容中提取代码."""
        code_match = re.search(r"```python\n(.*?)```", content, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()
        
        code_match = re.search(r"```\n(.*?)```", content, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()
        
        return content.strip()


class StrategyGenerationError(Exception):
    """策略生成错误."""

    pass
