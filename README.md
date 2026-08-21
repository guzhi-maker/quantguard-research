# 量化投研 Agent

按《量化投研Agent 全流程落地执行手册》落地的可运行 MVP：已完成第 0 周工程规范、阶段 1 的「数据 -> 因子 -> 回测 -> 绩效 -> 风控 -> 研报」闭环，以及阶段 2 的记忆增强、反思迭代与批量因子检验基础版。

## 当前状态

| 手册阶段 | 内容 | 状态 |
| --- | --- | --- |
| 第 0 周 | 目录结构、配置、数据源验证、Git 规范 | 已完成 |
| 第 1 周 | 行情数据、因子计算、去极值/Z-Score | 已完成 |
| 第 2 周 | 回测引擎、交易成本、绩效统计、可视化 | 已完成 |
| 第 3 周 | 工具注册、Agent 编排、异常重试 | 已完成基础版 |
| 第 4 周 | Markdown 研报、命令行交互、MVP 验收 | 已完成 |
| 阶段 2 | 双层记忆、反思迭代、风控归因、批量因子检验 | 已完成基础版 |
| 阶段 3-4 | 多角色协作、实盘监控 | 待开发 |

## 快速开始

项目默认使用内置确定性合成行情，离线即可跑通全流程。

```powershell
# 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 运行完整回测研报流水线（离线演示）
.venv\Scripts\python.exe main.py demo

# 阶段2：策略反思与自动迭代
.venv\Scripts\python.exe main.py stage2

# 阶段2：批量因子有效性检验（11 个经典因子）
.venv\Scripts\python.exe main.py research

# 使用 AkShare 真实数据（需联网）
.venv\Scripts\python.exe main.py demo --provider akshare --strict-history false

# 命令式交互
.venv\Scripts\python.exe main.py repl

# 单元测试
.venv\Scripts\python.exe -m pytest -q
```

## 核心设计

- **时点可得性**：财务因子只在公告日之后生效；指数成分股默认拒绝用当前快照回测历史，历史成分需放入 `data/constituents/` 快照文件。
- **交易成本**：默认佣金万 2（单笔最低 5 元）、卖出印花税千 1、双向过户费十万 1、双向滑点千 1。
- **T+1 约束**：信号在调仓日收盘生成，权重从下一交易日生效，天然满足 A 股 T+1。
- **风控独立**：仓位、行业暴露、换手率、回撤规则以纯函数硬编码，不依赖 LLM，回测后输出风控意见。
- **LLM 边界**：数值计算全部由工具完成；LangChain 工具注册已预留（`agents/langchain_tools.py`），未安装 LangChain 时自动降级。

## 阶段 2 能力

- **双层记忆**：SQLite 沉淀策略/回测/因子库，文本记忆支持 ChromaDB 或 TF-IDF 降级检索。
- **反思迭代**：规则化诊断收益/风险/成本/稳定性，自动生成 6 组参数变体并选出最优方案。
- **批量因子检验**：计算 Rank IC、IC_IR、胜率、半衰期、样本内外 IC 与 5 分组单调性。
- **风控归因**：行业/风格暴露表已进入回测研报，风控规则仍为独立硬编码层。

## 目录结构

```text
quant_agent/
├── config/          # 全局配置：目录、成本、回测、风控参数
├── tools/           # 数据、因子、回测、绩效、可视化、风控、研报、反思、因子检验
├── agents/          # Agent 编排与 LangChain 工具注册
├── memory/          # SQLite 结构化记忆与文本记忆
├── tests/           # 单元测试
├── reports/         # 研报与图表产物（默认 git 忽略）
├── main.py          # 演示与命令行交互入口
└── requirements.txt
```

## 真实数据说明

- AkShare 安装后，`--provider akshare` 会尝试获取沪深 300 等指数日线与成分股。
- 指数成分股的严格历史校验默认开启；如需用当前成分做演示，显式传 `--strict-history false`，但正式回测应提供历史快照。
- 所有行情默认前复权，避免除权除息跳空扭曲回测。
