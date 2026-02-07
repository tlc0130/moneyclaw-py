---
name: 智能LLM路由系统重构
overview: 重构现有的四层LLM路由机制，实现基于环境变量配置的灵活模型注册、多维度智能路由决策（成本/能力/速度/可用性）、历史表现学习、任务类型感知和预算感知降级策略
todos:
  - id: design-model-registry
    content: 设计并实现ModelRegistry模型注册表系统，支持从环境变量动态加载任意模型配置
    status: pending
  - id: implement-model-profile
    content: 实现ModelProfile模型画像类，包含成本、能力、延迟、任务专长等属性定义
    status: pending
    dependencies:
      - design-model-registry
  - id: extend-types
    content: 扩展types.py新增TaskType枚举、动态层级类型、增强TaskRequest
    status: pending
  - id: implement-task-analyzer
    content: 实现TaskAnalyzer任务分析器，根据prompt内容自动识别任务类型
    status: pending
    dependencies:
      - extend-types
  - id: implement-layer-classifier
    content: 实现LayerClassifier自动分层器，根据模型画像计算动态层级
    status: pending
    dependencies:
      - implement-model-profile
  - id: implement-performance-tracker
    content: 实现PerformanceTracker性能追踪器，记录每次调用的成功率和质量
    status: pending
  - id: implement-budget-manager
    content: 实现BudgetManager预算管理器，支持动态降级策略
    status: pending
  - id: implement-model-selector
    content: 实现ModelSelector核心选择器，综合多维度计算最优模型
    status: pending
    dependencies:
      - implement-layer-classifier
      - implement-performance-tracker
      - implement-budget-manager
  - id: refactor-router
    content: 重构router.py为SmartRouter，整合所有新组件
    status: pending
    dependencies:
      - design-model-registry
      - implement-model-selector
  - id: create-unified-provider
    content: 创建UnifiedProvider统一包装层，标准化所有Provider接口
    status: pending
  - id: adapt-providers
    content: 适配Ollama和LiteLLM Provider以支持新接口
    status: pending
    dependencies:
      - create-unified-provider
  - id: update-settings
    content: 更新settings.py支持新的环境变量配置格式
    status: pending
    dependencies:
      - design-model-registry
  - id: update-cli
    content: 更新cli.py初始化逻辑以使用新的路由系统
    status: pending
    dependencies:
      - refactor-router
  - id: add-performance-storage
    content: 实现DuckDB持久化存储性能数据
    status: pending
    dependencies:
      - implement-performance-tracker
  - id: write-tests
    content: 编写新路由系统的单元测试和集成测试
    status: pending
    dependencies:
      - refactor-router
  - id: update-tests
    content: 更新现有测试以兼容新的路由系统
    status: pending
    dependencies:
      - refactor-router
---

## 产品概述

重构MoneyClaw的LLM路由系统，从固定的三层架构升级为灵活的智能路由系统。用户可通过环境变量配置任意模型，系统根据成本、能力、速度、任务特性等多维度智能选择最优模型，并支持历史表现学习和预算感知降级。

## 核心功能需求

1. **灵活模型配置**：通过环境变量动态配置任意数量和类型的模型，不限于固定三层
2. **智能自动分层**：系统根据模型特性（成本、能力评分、延迟）自动计算并划分层级
3. **多维度路由决策**：综合考虑任务类型、金额、复杂度、预算、历史表现选择模型
4. **任务类型感知**：识别分析类、执行类、创意类任务，匹配最适合的模型
5. **历史学习优化**：记录每个模型的成功率、质量评分、实际成本，持续优化路由决策
6. **预算感知降级**：预算紧张时自动降级到更便宜的模型，确保服务连续性

## 技术栈

- **基础框架**: Python 3.12 + Pydantic + Pydantic-Settings
- **LLM调用**: LiteLLM (已集成)
- **数据持久化**: DuckDB (已集成) 用于存储模型历史表现
- **配置管理**: 环境变量 + Pydantic Settings
- **日志**: structlog (已集成)

## 实现方案

### 核心架构设计

```mermaid
flowchart TB
    subgraph Config["配置层"]
        A[ModelRegistry<br/>模型注册表]
        B[ModelProfile<br/>模型画像]
    end
    
    subgraph Router["路由核心"]
        C[SmartRouter<br/>智能路由器]
        D[LayerClassifier<br/>自动分层器]
        E[TaskAnalyzer<br/>任务分析器]
    end
    
    subgraph Intelligence["智能决策"]
        F[PerformanceTracker<br/>性能追踪]
        G[BudgetManager<br/>预算管理]
        H[ModelSelector<br/>模型选择器]
    end
    
    subgraph Providers["Provider层"]
        I[UnifiedProvider<br/>统一Provider]
        J[HealthChecker<br/>健康检查]
    end
    
    A --> C
    B --> D
    C --> E
    E --> H
    F --> H
    G --> H
    H --> I
    I --> J
```

### 关键设计决策

1. **动态层级系统**：从固定4层改为动态N层，系统根据模型成本和能力自动计算层级分数
2. **模型画像系统**：每个模型维护一个`ModelProfile`，包含成本系数、能力评分、延迟基准、任务专长
3. **任务分类器**：扩展`TaskRequest`支持`task_type`字段（ANALYTICS/EXECUTION/CREATIVE）
4. **性能反馈闭环**：每次调用后记录响应质量，用于调整模型权重
5. **预算感知路由**：当今日成本超过预算阈值时，自动应用更保守的路由策略

### 数据模型

**ModelProfile** - 模型画像

- model_id: 唯一标识
- provider_type: ollama/litellm
- cost_per_1k_tokens: 成本系数
- capability_score: 能力评分(0-1)
- avg_latency_ms: 平均延迟
- task_strengths: 任务类型专长
- availability_score: 可用性评分

**ModelPerformance** - 历史表现

- model_id
- timestamp
- task_type
- success: 是否成功
- quality_score: 质量评分
- actual_cost: 实际成本
- latency_ms: 实际延迟

### 路由算法

```
score(model, task) = 
    w1 * capability_match(model, task) +
    w2 * (1 - normalized_cost(model)) +
    w3 * (1 - normalized_latency(model)) +
    w4 * historical_success_rate(model, task) +
    w5 * availability_score(model)

budget_factor = (1 - today_cost / daily_budget)^2  # 预算紧张时降低

final_score = score * budget_factor
```

### 目录结构

```
moneyclaw/llm/
├── __init__.py                    # [MODIFY] 导出新的类和类型
├── types.py                       # [MODIFY] 扩展TaskRequest，新增ModelTier等
├── router.py                      # [MODIFY] 重构为SmartRouter
├── cache.py                       # [KEEP] 无需修改
├── cost_tracker.py                # [MODIFY] 扩展支持按模型追踪
├── model_registry.py              # [NEW] 模型注册表，管理所有配置模型
├── model_profile.py               # [NEW] 模型画像定义
├── performance_tracker.py         # [NEW] 历史性能追踪
├── budget_manager.py              # [NEW] 预算感知管理
├── task_analyzer.py               # [NEW] 任务类型分析器
├── layer_classifier.py            # [NEW] 自动分层计算器
├── providers/
│   ├── __init__.py                # [MODIFY] 导出统一Provider
│   ├── base.py                    # [KEEP] 基础接口不变
│   ├── unified_provider.py        # [NEW] 统一包装层
│   ├── litellm_provider.py        # [MODIFY] 适配新接口
│   └── ollama.py                  # [MODIFY] 适配新接口
└── storage/
    └── performance_store.py       # [NEW] DuckDB持久化

moneyclaw/config/
├── settings.py                    # [MODIFY] 扩展LLMSettings支持动态模型
└── defaults.py                    # [KEEP] 保留默认系统prompt

moneyclaw/cli.py                   # [MODIFY] 更新初始化逻辑

tests/test_llm/
├── test_smart_router.py           # [NEW] 新路由测试
├── test_model_registry.py         # [NEW] 注册表测试
└── test_performance_tracker.py    # [NEW] 性能追踪测试
```

### 环境变量配置方案

```
# 模型配置格式: LLM_MODEL_<ID>_<属性>
LLM_MODEL_1_PROVIDER=ollama
LLM_MODEL_1_MODEL=qwen2.5:7b
LLM_MODEL_1_COST_PER_1K=0
LLM_MODEL_1_CAPABILITY=0.6
LLM_MODEL_1_TASKS=execution,creative

LLM_MODEL_2_PROVIDER=litellm
LLM_MODEL_2_MODEL=deepseek/deepseek-chat
LLM_MODEL_2_API_KEY=${DEEPSEEK_API_KEY}
LLM_MODEL_2_COST_PER_1K=0.0005
LLM_MODEL_2_CAPABILITY=0.8
LLM_MODEL_2_TASKS=analytics,execution

# 路由配置
LLM_ROUTER_STRATEGY=balanced  # cost_optimized/quality_optimized/balanced
LLM_AUTO_LAYER=true           # 是否自动分层
LLM_LEARN_ENABLED=true        # 是否启用历史学习
```

## 实施注意事项

1. **向后兼容**: 保留原有的`LLMLayer`枚举和`select_layer`方法作为兼容层
2. **性能考虑**: 模型选择计算在毫秒级，使用预计算的评分缓存
3. **健康检查**: 定期检测模型可用性，自动排除故障模型
4. **降级安全**: 预算紧张时优先保证任务完成，而非强制使用最便宜的模型
5. **数据持久化**: 性能数据使用DuckDB按日分区存储，避免数据膨胀