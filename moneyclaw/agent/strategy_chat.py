"""Strategy Chat Interface — AI驱动的策略管理聊天接口.

提供自然语言交互界面，支持：
- "创建一个定投比特币的策略"
- "优化我的smart_rebalance策略"
- "列出所有策略"
- "禁用crypto_dca策略"
- "查看策略版本历史"
- "回滚到某个版本"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

import structlog

from moneyclaw.agent.strategy_generator_v2 import (
    GeneratedStrategy,
    StrategyGenerationError,
    StrategyGeneratorV2,
    StrategyOptimizerV2,
)
from moneyclaw.agent.strategy_version import StrategyVersionManager
from moneyclaw.llm.smart_router import SmartRouter
from moneyclaw.llm.types import TaskRequest
from moneyclaw.plugins.registry import StrategyRegistry

log = structlog.get_logger()


class ChatIntent(Enum):
    """聊天意图类型."""

    GENERATE = auto()      # 生成新策略
    OPTIMIZE = auto()      # 优化现有策略
    LIST = auto()          # 列出策略
    ENABLE = auto()        # 启用策略
    DISABLE = auto()       # 禁用策略
    DELETE = auto()        # 删除策略
    DESCRIBE = auto()      # 描述策略
    VERSIONS = auto()      # 查看版本历史
    ROLLBACK = auto()      # 回滚版本
    ITERATE = auto()       # 迭代改进
    HELP = auto()          # 帮助
    UNKNOWN = auto()       # 未知意图


@dataclass
class ChatCommand:
    """解析的聊天命令."""

    intent: ChatIntent
    strategy_name: str | None = None
    parameters: dict[str, Any] | None = None
    raw_message: str = ""


@dataclass
class ChatResponse:
    """聊天响应."""

    message: str
    success: bool = True
    data: dict[str, Any] | None = None
    actions: list[str] | None = None


class StrategyChatInterface:
    """策略管理聊天接口.

    提供自然语言界面管理策略，集成版本控制功能。
    """

    def __init__(
        self,
        llm_router: SmartRouter,
        strategy_registry: StrategyRegistry,
    ) -> None:
        self._llm = llm_router
        self._registry = strategy_registry
        self._generator = StrategyGeneratorV2(llm_router)
        self._optimizer = StrategyOptimizerV2(llm_router)
        self._version_manager = StrategyVersionManager()

    async def handle_message(self, message: str) -> ChatResponse:
        """处理用户消息.

        Args:
            message: 用户输入的自然语言消息

        Returns:
            聊天响应
        """
        log.info("strategy_chat.received", message=message[:50])

        try:
            # 1. 智能解析意图（使用LLM进行更智能的识别）
            command = await self._smart_parse_intent(message)

            # 2. 执行对应操作
            if command.intent == ChatIntent.GENERATE:
                return await self._handle_generate_v2(command)
            elif command.intent == ChatIntent.OPTIMIZE:
                return await self._handle_optimize_v2(command)
            elif command.intent == ChatIntent.LIST:
                return await self._handle_list(command)
            elif command.intent == ChatIntent.ENABLE:
                return await self._handle_enable(command)
            elif command.intent == ChatIntent.DISABLE:
                return await self._handle_disable(command)
            elif command.intent == ChatIntent.DELETE:
                return await self._handle_delete(command)
            elif command.intent == ChatIntent.DESCRIBE:
                return await self._handle_describe_v2(command)
            elif command.intent == ChatIntent.VERSIONS:
                return await self._handle_versions(command)
            elif command.intent == ChatIntent.ROLLBACK:
                return await self._handle_rollback(command)
            elif command.intent == ChatIntent.ITERATE:
                return await self._handle_iterate(command)
            elif command.intent == ChatIntent.HELP:
                return await self._handle_help_v2(command)
            else:
                return ChatResponse(
                    message="我不太理解您的意思。输入 '帮助' 或 'help' 查看支持的命令。",
                    success=False,
                )

        except Exception as e:
            log.exception("strategy_chat.error")
            return ChatResponse(
                message=f"处理消息时出错: {e}",
                success=False,
            )

    async def _parse_intent(self, message: str) -> ChatCommand:
        """使用LLM解析用户意图."""
        # 简单的规则匹配作为快速路径
        msg_lower = message.lower().strip()

        # 帮助
        if any(kw in msg_lower for kw in ["帮助", "help", "怎么用", "命令"]):
            return ChatCommand(intent=ChatIntent.HELP, raw_message=message)

        # 列出策略
        if any(kw in msg_lower for kw in ["列出", "列表", "list", "所有策略", "有哪些策略"]):
            return ChatCommand(intent=ChatIntent.LIST, raw_message=message)

        # 提取策略名称的模式
        strategy_patterns = [
            r"(?:策略\s*[:：]?\s*)(\w+)",
            r"(\w+)\s*策略",
            r"(?:enable|disable|delete|remove|优化|启用|禁用|删除)\s+(\w+)",
        ]

        strategy_name = None
        for pattern in strategy_patterns:
            match = re.search(pattern, message, re.I)
            if match:
                strategy_name = match.group(1).lower()
                break

        # 生成策略
        if any(kw in msg_lower for kw in ["创建", "生成", "新建", "create", "generate", "new"]):
            return ChatCommand(
                intent=ChatIntent.GENERATE,
                strategy_name=strategy_name,
                parameters={"description": message},
                raw_message=message,
            )

        # 优化策略
        if any(kw in msg_lower for kw in ["优化", "改进", "optimize", "improve", "调优"]):
            return ChatCommand(
                intent=ChatIntent.OPTIMIZE,
                strategy_name=strategy_name,
                raw_message=message,
            )

        # 启用策略
        if any(kw in msg_lower for kw in ["启用", "enable", "激活", "开启"]):
            return ChatCommand(
                intent=ChatIntent.ENABLE,
                strategy_name=strategy_name,
                raw_message=message,
            )

        # 禁用策略
        if any(kw in msg_lower for kw in ["禁用", "disable", "停用", "关闭"]):
            return ChatCommand(
                intent=ChatIntent.DISABLE,
                strategy_name=strategy_name,
                raw_message=message,
            )

        # 删除策略
        if any(kw in msg_lower for kw in ["删除", "remove", "delete", "卸载"]):
            return ChatCommand(
                intent=ChatIntent.DELETE,
                strategy_name=strategy_name,
                raw_message=message,
            )

        # 描述策略
        if any(kw in msg_lower for kw in ["描述", "介绍", "detail", "describe", "info", "信息"]):
            return ChatCommand(
                intent=ChatIntent.DESCRIBE,
                strategy_name=strategy_name,
                raw_message=message,
            )

        # 使用LLM进行意图识别
        return await self._llm_intent_classification(message)

    async def _llm_intent_classification(self, message: str) -> ChatCommand:
        """使用LLM进行意图分类."""
        prompt = f"""分析以下用户消息，判断意图并提取参数:

用户消息: "{message}"

可能的意图:
1. generate - 生成新策略
2. optimize - 优化现有策略
3. list - 列出所有策略
4. enable - 启用策略
5. disable - 禁用策略
6. delete - 删除策略
7. describe - 查看策略详情
8. unknown - 无法识别

请以JSON格式返回:
{{
    "intent": "意图名称",
    "strategy_name": "策略名称或null",
    "description": "策略描述或null"
}}"""

        try:
            request = TaskRequest(
                prompt=prompt,
                system="你是一个意图识别助手。只返回JSON格式的结果。",
                temperature=0.1,
                max_tokens=500,
            )
            response = await self._llm.complete(request)

            # 尝试解析JSON响应
            import json

            content = response.content.strip()
            # 提取JSON部分
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            result = json.loads(content.strip())

            intent_map = {
                "generate": ChatIntent.GENERATE,
                "optimize": ChatIntent.OPTIMIZE,
                "list": ChatIntent.LIST,
                "enable": ChatIntent.ENABLE,
                "disable": ChatIntent.DISABLE,
                "delete": ChatIntent.DELETE,
                "describe": ChatIntent.DESCRIBE,
                "unknown": ChatIntent.UNKNOWN,
            }

            intent = intent_map.get(result.get("intent", "unknown"), ChatIntent.UNKNOWN)

            return ChatCommand(
                intent=intent,
                strategy_name=result.get("strategy_name"),
                parameters={"description": result.get("description", message)},
                raw_message=message,
            )

        except Exception as e:
            log.warning("strategy_chat.llm_intent_failed", error=str(e))
            return ChatCommand(intent=ChatIntent.UNKNOWN, raw_message=message)

    # ========== 新版智能方法 ==========

    async def _smart_parse_intent(self, message: str) -> ChatCommand:
        """智能解析用户意图 - 优先使用LLM进行更准确的识别."""
        msg_lower = message.lower().strip()

        # 快速路径：帮助、列表
        if any(kw in msg_lower for kw in ["帮助", "help", "怎么用"]):
            return ChatCommand(intent=ChatIntent.HELP, raw_message=message)

        if any(kw in msg_lower for kw in ["列出", "列表", "list", "所有策略"]):
            return ChatCommand(intent=ChatIntent.LIST, raw_message=message)

        # 使用LLM进行智能意图识别
        return await self._llm_intent_classification_v2(message)

    async def _llm_intent_classification_v2(self, message: str) -> ChatCommand:
        """使用LLM进行智能意图分类 - 区分创建新策略和管理现有策略."""
        registered = list(self._registry.all_strategies.keys())

        prompt = f"""分析用户消息，判断是创建新策略还是管理现有策略。

用户消息: "{message}"

已注册的策略: {registered if registered else "(无)"}

请分析:
1. 用户意图:
   - CREATE: 想要创建新策略（如"帮我监控ETH价格","创建一个定投策略"）
   - OPTIMIZE: 优化现有策略（明确说优化某个已存在的策略）
   - ITERATE: 基于反馈改进策略（如"加上止损功能","修改触发条件"）
   - VERSIONS: 查看版本历史（如"查看版本","历史版本"）
   - ROLLBACK: 回滚到某个版本（如"回滚","恢复到之前的版本"）
   - MANAGE: 管理策略（启用/禁用/删除/查看某个已存在的策略）
   - CHAT: 只是聊天或询问问题

2. 如果是CREATE:
   - 提取完整的需求描述
   - 建议的策略名称（英文小写下划线，如 eth_price_monitor, btc_dca）

3. 如果是OPTIMIZE, ITERATE, VERSIONS, ROLLBACK:
   - 策略名称（从已注册策略中匹配最接近的）
   - 版本ID（如果是回滚意图，提取版本ID或"latest"）
   - 改进反馈（如果是迭代意图，提取用户的改进需求）

4. 如果是MANAGE:
   - 策略名称（从已注册策略中匹配最接近的）
   - 具体操作: enable/disable/delete/describe

以JSON返回:
{{
    "intent": "CREATE|OPTIMIZE|ITERATE|VERSIONS|ROLLBACK|MANAGE|CHAT",
    "action": "enable|disable|delete|describe|none",
    "strategy_name": "策略名称或null",
    "version_id": "版本ID或null",
    "description": "策略描述或null",
    "suggested_name": "建议的新策略名称或null",
    "feedback": "改进反馈或null"
}}"""

        try:
            request = TaskRequest(
                prompt=prompt,
                system="你是一个专业的交易助手意图分析器。准确判断用户是想要创建新策略还是管理现有策略。",
                temperature=0.1,
                max_tokens=600,
            )
            response = await self._llm.complete(request)

            import json

            content = response.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            result = json.loads(content.strip())
            intent_str = result.get("intent", "CHAT")

            # 映射到ChatIntent
            if intent_str == "CREATE":
                return ChatCommand(
                    intent=ChatIntent.GENERATE,
                    strategy_name=result.get("suggested_name"),
                    parameters={
                        "description": result.get("description", message),
                        "suggested_name": result.get("suggested_name"),
                    },
                    raw_message=message,
                )
            elif intent_str == "OPTIMIZE":
                return ChatCommand(
                    intent=ChatIntent.OPTIMIZE,
                    strategy_name=result.get("strategy_name"),
                    raw_message=message,
                )
            elif intent_str == "ITERATE":
                return ChatCommand(
                    intent=ChatIntent.ITERATE,
                    strategy_name=result.get("strategy_name"),
                    parameters={"feedback": result.get("feedback", message)},
                    raw_message=message,
                )
            elif intent_str == "VERSIONS":
                return ChatCommand(
                    intent=ChatIntent.VERSIONS,
                    strategy_name=result.get("strategy_name"),
                    raw_message=message,
                )
            elif intent_str == "ROLLBACK":
                return ChatCommand(
                    intent=ChatIntent.ROLLBACK,
                    strategy_name=result.get("strategy_name"),
                    parameters={"version_id": result.get("version_id")},
                    raw_message=message,
                )
            elif intent_str == "MANAGE":
                action = result.get("action", "")
                strategy_name = result.get("strategy_name")

                if action == "enable":
                    return ChatCommand(intent=ChatIntent.ENABLE, strategy_name=strategy_name, raw_message=message)
                elif action == "disable":
                    return ChatCommand(intent=ChatIntent.DISABLE, strategy_name=strategy_name, raw_message=message)
                elif action == "delete":
                    return ChatCommand(intent=ChatIntent.DELETE, strategy_name=strategy_name, raw_message=message)
                elif action == "describe":
                    return ChatCommand(intent=ChatIntent.DESCRIBE, strategy_name=strategy_name, raw_message=message)

            # 默认CHAT
            return ChatCommand(intent=ChatIntent.UNKNOWN, raw_message=message)

        except Exception as e:
            log.warning("strategy_chat.llm_v2_failed", error=str(e))
            # 降级到规则匹配
            return await self._fallback_parse_intent(message)

    async def _fallback_parse_intent(self, message: str) -> ChatCommand:
        """降级意图解析 - 使用关键词匹配."""
        msg_lower = message.lower().strip()

        # 创建策略（宽泛匹配）
        create_keywords = ["创建", "生成", "新建", "create", "generate", "new", "帮我做", "帮我写", "想要", "需要"]
        trading_keywords = ["策略", "交易", "投资", "定投", "监控", "提醒", "套利", "价格", "买卖", "跟踪"]

        if any(kw in msg_lower for kw in create_keywords):
            if any(kw in msg_lower for kw in trading_keywords):
                return ChatCommand(
                    intent=ChatIntent.GENERATE,
                    parameters={"description": message},
                    raw_message=message,
                )

        # 提取策略名称
        patterns = [
            r"(?:策略\s*[:：]?\s*)([a-zA-Z_]\w+)",
            r"([a-zA-Z_]\w+)\s*策略",
            r"(?:启用|禁用|删除|优化|enable|disable|delete|optimize|回滚|rollback|版本|versions?)\s+([a-zA-Z_]\w+)",
        ]
        strategy_name = None
        for pattern in patterns:
            match = re.search(pattern, message, re.I)
            if match:
                strategy_name = match.group(1).lower()
                break

        # 版本管理
        if any(kw in msg_lower for kw in ["版本", "历史", "versions?", "history"]):
            return ChatCommand(intent=ChatIntent.VERSIONS, strategy_name=strategy_name, raw_message=message)
        
        if any(kw in msg_lower for kw in ["回滚", "恢复", "rollback", "revert"]):
            # 尝试提取版本ID
            version_match = re.search(r"(?:版本|version)?\s*([a-f0-9]{8,16})", message, re.I)
            version_id = version_match.group(1) if version_match else None
            return ChatCommand(
                intent=ChatIntent.ROLLBACK,
                strategy_name=strategy_name,
                parameters={"version_id": version_id},
                raw_message=message,
            )
        
        if any(kw in msg_lower for kw in ["迭代", "改进", "修改", "加上", "添加", "iterate", "improve"]):
            return ChatCommand(
                intent=ChatIntent.ITERATE,
                strategy_name=strategy_name,
                parameters={"feedback": message},
                raw_message=message,
            )

        if any(kw in msg_lower for kw in ["优化", "optimize"]):
            return ChatCommand(intent=ChatIntent.OPTIMIZE, strategy_name=strategy_name, raw_message=message)
        if any(kw in msg_lower for kw in ["启用", "enable"]):
            return ChatCommand(intent=ChatIntent.ENABLE, strategy_name=strategy_name, raw_message=message)
        if any(kw in msg_lower for kw in ["禁用", "disable"]):
            return ChatCommand(intent=ChatIntent.DISABLE, strategy_name=strategy_name, raw_message=message)
        if any(kw in msg_lower for kw in ["删除", "delete"]):
            return ChatCommand(intent=ChatIntent.DELETE, strategy_name=strategy_name, raw_message=message)
        if any(kw in msg_lower for kw in ["描述", "describe", "详情"]):
            return ChatCommand(intent=ChatIntent.DESCRIBE, strategy_name=strategy_name, raw_message=message)

        return ChatCommand(intent=ChatIntent.UNKNOWN, raw_message=message)

    async def _handle_generate_v2(self, command: ChatCommand) -> ChatResponse:
        """改进的策略生成处理 - 自动保存并加载."""
        description = command.parameters.get("description", command.raw_message)
        suggested_name = command.parameters.get("suggested_name")

        # 宽泛的策略检测
        trading_keywords = ["策略", "交易", "投资", "定投", "监控", "提醒", "套利", "价格", "买卖", "跟踪", "dca", "alert", "monitor", "trade"]
        if not any(kw in description.lower() for kw in trading_keywords):
            return ChatResponse(
                message="请提供更具体的策略描述，例如：\n- 帮我监控ETH价格，突破3000就提醒我\n- 创建一个每天定投100美元BTC的策略\n- 监控币安资金费率，出现大的负费率时通知我",
                success=False,
            )

        try:
            # 确定策略类型
            strategy_type = self._detect_strategy_type(description)

            # 生成策略 - 使用 V2 生成器，会自动保存版本
            strategy = await self._generator.generate(
                description=description,
                strategy_type=strategy_type,
                suggested_name=suggested_name,
            )

            # 检查名称冲突
            existing = self._registry.get(strategy.name)
            if existing:
                for i in range(2, 100):
                    new_name = f"{strategy.name}_{i}"
                    if not self._registry.get(new_name):
                        strategy.name = new_name
                        break

            # 直接保存并激活
            path = await self._generator.save_strategy(strategy, activate=True)

            # 尝试自动加载
            loaded = await self._try_load_strategy(path, strategy.name)

            version_info = f"**版本**: `{strategy.version_id[:8] if strategy.version_id else 'N/A'}`\n" if strategy.version_id else ""

            if loaded:
                return ChatResponse(
                    message=f"""✅ **策略已创建并启动！**

**名称**: `{strategy.name}`
**类型**: {strategy_type}
**风险等级**: {strategy.risk_level}
{version_info}**状态**: 🟢 运行中

📍 文件位置: `{path}`

策略已自动加载并开始运行。使用 "查看 {strategy.name} 的版本" 查看版本历史。""",
                    success=True,
                    data={"strategy_name": strategy.name, "auto_loaded": True, "version_id": strategy.version_id},
                )
            else:
                return ChatResponse(
                    message=f"""✅ **策略已创建！**

**名称**: `{strategy.name}`
**类型**: {strategy_type}
**风险等级**: {strategy.risk_level}
{version_info}
📍 文件位置: `{path}`

策略文件已保存，重启后将自动加载运行。使用 "查看 {strategy.name} 的版本" 查看版本历史。""",
                    success=True,
                    data={"strategy_name": strategy.name, "auto_loaded": False, "version_id": strategy.version_id},
                )

        except StrategyGenerationError as e:
            return ChatResponse(
                message=f"❌ 策略生成失败: {e}\n\n请尝试更清晰的描述，比如：\n'创建一个ETH价格监控策略，突破3000美元时提醒'",
                success=False,
            )
        except Exception as e:
            log.exception("strategy_chat.generate_v2_failed")
            return ChatResponse(
                message=f"❌ 创建策略时出错: {e}",
                success=False,
            )

    def _detect_strategy_type(self, description: str) -> str:
        """检测策略类型."""
        desc_lower = description.lower()
        if any(kw in desc_lower for kw in ["定投", "dca", "定期", "每天买", "每天投", "定期定额"]):
            return "dca"
        if any(kw in desc_lower for kw in ["价格", "突破", "跌破", "提醒", "alert", "监控价格"]):
            return "price_monitor"
        if any(kw in desc_lower for kw in ["套利", "arbitrage", "价差"]):
            return "arbitrage"
        if any(kw in desc_lower for kw in ["资金费", "funding", "费率"]):
            return "funding"
        if any(kw in desc_lower for kw in ["再平衡", "rebalance", "配比", "调仓"]):
            return "rebalance"
        return "general"

    async def _try_load_strategy(self, path: Any, name: str) -> bool:
        """尝试加载并注册新策略."""
        try:
            from pathlib import Path
            from moneyclaw.plugins.loader import StrategyLoader

            loader = StrategyLoader()
            strategies = await loader.load_from_path(Path(path).parent)

            for strategy_cls in strategies:
                if strategy_cls.name == name:
                    strategy = strategy_cls()
                    await self._registry.register(strategy)
                    log.info("strategy_chat.auto_loaded", strategy=name)
                    return True

            return False
        except Exception as e:
            log.warning("strategy_chat.auto_load_failed", error=str(e))
            return False

    # ========== 原有方法保留 ==========

    async def _handle_generate(self, command: ChatCommand) -> ChatResponse:
        """处理生成策略请求."""
        description = command.parameters.get("description", command.raw_message)

        # 确认是否是策略生成请求
        if not any(kw in description.lower() for kw in ["策略", "strategy", "交易", "投资", "定投", "套利"]):
            return ChatResponse(
                message="请提供更详细的策略描述，例如：\n- 创建一个定投比特币的策略\n- 生成一个ETH套利策略\n- 新建一个价格提醒策略",
                success=False,
            )

        try:
            # 生成策略
            strategy = await self._generator.generate(
                description=description,
                strategy_type="general",
            )

            # 预览信息
            preview = f"""✅ 策略生成成功！

**名称**: {strategy.name}
**描述**: {strategy.description}
**风险等级**: {strategy.risk_level}
**LLM层级**: {strategy.min_llm_layer}

**代码预览**:
```python
{strategy.code[:500]}...
```

是否保存并启用此策略？
回复 "是" 或 "yes" 确认保存。"""

            return ChatResponse(
                message=preview,
                success=True,
                data={"strategy": strategy, "pending_confirm": True},
                actions=["确认保存", "取消"],
            )

        except StrategyGenerationError as e:
            return ChatResponse(
                message=f"❌ 策略生成失败: {e}",
                success=False,
            )

    async def confirm_save_strategy(self, strategy: GeneratedStrategy) -> ChatResponse:
        """确认保存策略."""
        try:
            path = await self._generator.save_strategy(strategy)

            return ChatResponse(
                message=f"✅ 策略已保存到: {path}\n\n请重启 MoneyClaw 以加载新策略。",
                success=True,
                data={"path": str(path)},
            )

        except Exception as e:
            return ChatResponse(
                message=f"❌ 保存策略失败: {e}",
                success=False,
            )

    async def _handle_optimize_v2(self, command: ChatCommand) -> ChatResponse:
        """处理优化策略请求 V2 - 使用代码生成模型."""
        strategy_name = command.strategy_name

        if not strategy_name:
            return ChatResponse(
                message="请指定要优化的策略名称，例如：\n- 优化 crypto_dca 策略\n- 优化 smart_rebalance",
                success=False,
            )

        # 检查版本历史
        versions = self._version_manager.list_versions(strategy_name)
        if not versions:
            return ChatResponse(
                message=f"❌ 策略 {strategy_name} 没有版本历史\n使用AI生成或迭代过的策略才有版本记录。",
                success=False,
            )

        # 获取执行历史
        execution_history = []  # 暂时为空，可从 memory 获取

        try:
            optimization = await self._optimizer.optimize(
                strategy_name=strategy_name,
                execution_history=execution_history,
                optimization_goal="improve_performance",
            )

            perf = optimization.current_performance
            version_id = optimization.version_id[:8] if optimization.version_id else "N/A"

            msg = f"""📊 **{strategy_name}** 策略优化完成

**当前表现**:
- 总交易次数: {perf.get('total_trades', 0)}
- 胜率: {perf.get('win_rate', 0):.1%}
- 总盈亏: {perf.get('total_pnl', 0):.2f}
- 平均盈亏: {perf.get('avg_pnl', 0):.2f}

**新版本**: `{version_id}`

已自动生成优化版本，你可以：
- 查看版本历史: "查看 {strategy_name} 的版本"
- 回滚到旧版本: "回滚 {strategy_name} 到 [版本ID]"
- 应用新版本: 重启后自动加载最新版本"""

            return ChatResponse(
                message=msg,
                success=True,
                data={
                    "strategy_name": strategy_name,
                    "version_id": optimization.version_id,
                    "optimized_code": optimization.optimized_code,
                },
            )

        except Exception as e:
            return ChatResponse(
                message=f"❌ 优化策略失败: {e}",
                success=False,
            )

    async def _handle_versions(self, command: ChatCommand) -> ChatResponse:
        """处理查看版本历史请求."""
        strategy_name = command.strategy_name

        if not strategy_name:
            # 列出所有有版本历史的策略
            all_strategies = self._version_manager.list_all_strategies_with_versions()
            if not all_strategies:
                return ChatResponse(
                    message="暂无策略版本历史。\n使用AI创建策略后会自动保存版本。",
                    success=True,
                )

            msg = "📚 **有版本历史的策略**\n\n"
            for name, versions in all_strategies.items():
                latest = versions[0]
                msg += f"• **{name}**: {len(versions)} 个版本，最新 {latest.created_at[:10]}\n"

            msg += "\n使用 '查看 [策略名] 的版本' 查看详细历史。"
            return ChatResponse(message=msg, success=True)

        versions = self._version_manager.list_versions(strategy_name)
        if not versions:
            return ChatResponse(
                message=f"策略 **{strategy_name}** 暂无版本历史。",
                success=True,
            )

        stats = self._version_manager.get_strategy_stats(strategy_name)

        msg = f"""📚 **{strategy_name}** 版本历史

**统计**: {stats.get('total_versions', 0)} 个版本
**最早**: {stats.get('first_version', 'N/A')[:10]}
**最新**: {stats.get('latest_version', 'N/A')[:10]}

**版本列表**:
"""
        for i, v in enumerate(versions[:10], 1):  # 只显示最近10个
            author_icon = "🤖" if v.author == "ai" else "👤" if v.author == "user" else "⚙️"
            tags = f" [{', '.join(v.tags)}]" if v.tags else ""
            msg += f"{i}. `{v.version_id[:8]}` {author_icon} {v.created_at[:16]}{tags}\n"
            if v.change_summary:
                msg += f"   └─ {v.change_summary[:60]}...\n"

        if len(versions) > 10:
            msg += f"\n...还有 {len(versions) - 10} 个更早版本"

        msg += f"\n使用 '回滚 {strategy_name} 到 [版本ID]' 切换到指定版本。"

        return ChatResponse(message=msg, success=True)

    async def _handle_rollback(self, command: ChatCommand) -> ChatResponse:
        """处理回滚版本请求."""
        strategy_name = command.strategy_name
        version_id = command.parameters.get("version_id") if command.parameters else None

        if not strategy_name:
            return ChatResponse(
                message="请指定要回滚的策略名称，例如：\n- 回滚 crypto_dca\n- 将 smart_rebalance 恢复到之前版本",
                success=False,
            )

        # 如果没有指定版本ID，使用最新版本
        if not version_id:
            latest = self._version_manager.get_latest_version(strategy_name)
            if latest:
                version_id = latest.version_id
            else:
                return ChatResponse(
                    message=f"❌ 策略 {strategy_name} 没有版本历史",
                    success=False,
                )

        # 执行回滚
        result = self._version_manager.rollback_to_version(strategy_name, version_id)
        if not result:
            return ChatResponse(
                message=f"❌ 回滚失败，找不到版本: {version_id[:8] if version_id else 'N/A'}",
                success=False,
            )

        version, code = result

        # 保存回滚的代码到策略目录
        from pathlib import Path
        strategy_dir = Path("strategies") / strategy_name
        code_file = strategy_dir / "__init__.py"

        if code_file.exists():
            code_file.write_text(code, encoding="utf-8")

        return ChatResponse(
            message=f"""↩️ **{strategy_name}** 已回滚

**回滚到版本**: `{version_id[:8]}`
**创建时间**: {version.created_at[:16]}
**原始作者**: {version.author}

当前策略代码已更新，重启后生效。
自动备份已保存到版本历史中。""",
            success=True,
            data={
                "strategy_name": strategy_name,
                "version_id": version_id,
            },
        )

    async def _handle_iterate(self, command: ChatCommand) -> ChatResponse:
        """处理迭代改进请求."""
        strategy_name = command.strategy_name
        feedback = command.parameters.get("feedback") if command.parameters else command.raw_message

        if not strategy_name:
            return ChatResponse(
                message="请指定要改进的策略名称，例如：\n- 改进 crypto_dca 策略\n- 给 smart_rebalance 加上止损功能",
                success=False,
            )

        try:
            # 调用生成器的迭代方法
            strategy = await self._generator.iterate(
                strategy_name=strategy_name,
                feedback=feedback,
            )

            # 保存新版本
            await self._generator.save_strategy(strategy, activate=False)

            return ChatResponse(
                message=f"""🔄 **{strategy_name}** 迭代完成

**新版本**: `{strategy.version_id[:8] if strategy.version_id else 'N/A'}`
**变更**: {feedback[:50]}...

已生成新版本并保存，使用以下命令查看或应用：
- 查看版本历史: "查看 {strategy_name} 的版本"
- 回滚到旧版本: "回滚 {strategy_name}"
- 应用新版本: 重启后自动加载""",
                success=True,
                data={
                    "strategy_name": strategy_name,
                    "version_id": strategy.version_id,
                },
            )

        except StrategyGenerationError as e:
            return ChatResponse(
                message=f"❌ 迭代失败: {e}",
                success=False,
            )
        except Exception as e:
            log.exception("strategy_chat.iterate_failed")
            return ChatResponse(
                message=f"❌ 迭代改进时出错: {e}",
                success=False,
            )

    async def _handle_describe_v2(self, command: ChatCommand) -> ChatResponse:
        """处理描述策略请求 V2 - 包含版本信息."""
        strategy_name = command.strategy_name

        if not strategy_name:
            return await self._handle_list(command)

        strategy = self._registry.get(strategy_name)
        if not strategy:
            return ChatResponse(
                message=f"❌ 未找到策略: {strategy_name}",
                success=False,
            )

        # 获取版本信息
        versions = self._version_manager.list_versions(strategy_name)
        version_info = ""
        if versions:
            latest = versions[0]
            version_info = f"""
**版本信息**:
- 最新版本: `{latest.version_id[:8]}` ({latest.created_at[:10]})
- 版本总数: {len(versions)}
- 创建者: {latest.author}
"""

        msg = f"""📖 **{strategy_name}** 策略详情

**名称**: {getattr(strategy, 'name', strategy_name)}
**描述**: {getattr(strategy, 'description', 'N/A')}
**风险等级**: {getattr(strategy, 'risk_level', 'N/A')}
**最小LLM层级**: {getattr(strategy, 'min_llm_layer', 'N/A')}
**状态**: {'启用' if strategy_name in self._registry.active else '禁用'}
{version_info}
使用 "查看 {strategy_name} 的版本" 查看完整版本历史。"""

        return ChatResponse(message=msg, success=True)

    async def _handle_list(self, command: ChatCommand) -> ChatResponse:
        """处理列出策略请求."""
        strategies = self._registry.status()

        if not strategies:
            return ChatResponse(
                message="暂无策略。使用 '创建策略' 来生成新策略。",
                success=True,
            )

        msg = "📋 **策略列表**\n\n"
        for name, info in strategies.items():
            status_icon = "🟢" if info.get("enabled") else "🔴"
            msg += f"{status_icon} **{name}**\n"
            msg += f"   描述: {info.get('description', 'N/A')[:50]}...\n"
            msg += f"   状态: {'启用' if info.get('enabled') else '禁用'}\n\n"

        msg += f"总计: {len(strategies)} 个策略"
        return ChatResponse(message=msg, success=True)

    async def _handle_enable(self, command: ChatCommand) -> ChatResponse:
        """处理启用策略请求."""
        strategy_name = command.strategy_name

        if not strategy_name:
            return ChatResponse(
                message="请指定要启用的策略名称。",
                success=False,
            )

        success = self._registry.enable(strategy_name)

        if success:
            return ChatResponse(message=f"✅ 策略 **{strategy_name}** 已启用。", success=True)
        else:
            return ChatResponse(
                message=f"❌ 无法启用策略: {strategy_name}",
                success=False,
            )

    async def _handle_disable(self, command: ChatCommand) -> ChatResponse:
        """处理禁用策略请求."""
        strategy_name = command.strategy_name

        if not strategy_name:
            return ChatResponse(
                message="请指定要禁用的策略名称。",
                success=False,
            )

        success = self._registry.disable(strategy_name)

        if success:
            return ChatResponse(message=f"✅ 策略 **{strategy_name}** 已禁用。", success=True)
        else:
            return ChatResponse(
                message=f"❌ 无法禁用策略: {strategy_name}",
                success=False,
            )

    async def _handle_delete(self, command: ChatCommand) -> ChatResponse:
        """处理删除策略请求."""
        strategy_name = command.strategy_name

        if not strategy_name:
            return ChatResponse(
                message="请指定要删除的策略名称。",
                success=False,
            )

        # 先禁用
        self._registry.disable(strategy_name)

        # TODO: 实际删除文件操作

        return ChatResponse(
            message=f"⚠️ 策略 **{strategy_name}** 已禁用并标记为删除。\n请手动删除策略目录以完全移除。",
            success=True,
            actions=["确认删除"],
        )

    async def _handle_describe(self, command: ChatCommand) -> ChatResponse:
        """处理描述策略请求."""
        strategy_name = command.strategy_name

        if not strategy_name:
            # 列出所有策略
            return await self._handle_list(command)

        strategy = self._registry.get(strategy_name)
        if not strategy:
            return ChatResponse(
                message=f"❌ 未找到策略: {strategy_name}",
                success=False,
            )

        msg = f"""📖 **{strategy_name}** 策略详情

**名称**: {getattr(strategy, 'name', strategy_name)}
**描述**: {getattr(strategy, 'description', 'N/A')}
**风险等级**: {getattr(strategy, 'risk_level', 'N/A')}
**最小LLM层级**: {getattr(strategy, 'min_llm_layer', 'N/A')}
**状态**: {'启用' if strategy_name in self._registry.active else '禁用'}
"""
        return ChatResponse(message=msg, success=True)

    async def _handle_help_v2(self, command: ChatCommand) -> ChatResponse:
        """处理帮助请求 V2 - 包含版本管理功能."""
        help_text = """🤖 **AI策略管理系统 V2** 使用帮助

## 支持的命令

### 1️⃣ 创建策略
- "创建一个定投比特币的策略"
- "生成ETH价格提醒策略"
- "帮我监控币安资金费率"

### 2️⃣ 迭代改进
- "给策略加上止损功能"
- "修改触发条件为突破均线"
- "添加 telegram 通知"

### 3️⃣ 自动优化
- "优化 crypto_dca 策略"
- "分析 smart_rebalance 的表现"

### 4️⃣ 版本管理
- "查看版本历史"
- "查看 [策略名] 的版本"
- "回滚 [策略名] 到 [版本ID]"
- "恢复到之前的版本"

### 5️⃣ 管理策略
- "列出所有策略" / "有哪些策略"
- "启用 [策略名]"
- "禁用 [策略名]"
- "删除 [策略名]"
- "查看 [策略名] 详情"

### 6️⃣ 其他
- "帮助" / "help" - 显示此帮助信息

## 示例

💡 **创建**: "创建一个每天定投100美元BTC的策略"
💡 **迭代**: "给这个策略加上止损功能，亏损超过5%就卖出"
💡 **优化**: "优化我的smart_rebalance策略参数"
💡 **版本**: "查看 btc_dca 的版本历史"
💡 **回滚**: "回滚 btc_dca 到 abc12345"

开始试试吧！"""

        return ChatResponse(message=help_text, success=True)
