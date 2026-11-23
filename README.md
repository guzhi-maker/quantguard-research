# QuantGuard 量化研究数据台

面向“数据挖掘与机器学习”课程期末项目的研究型应用。当前仓库重点交付成员 A 的第一阶段：用 AI 机器人编排真实数据采集，形成可供后续建模的近三千股原始数据池，并给出可解释的个股研究报告示例。

```text
公开行情/新闻/公告
-> 东方财富、腾讯、新浪接口
-> 断点续采与双源核价
-> 原始 CSV + manifest + SHA256
-> Python 指标计算与规则预警
-> DeepSeek 结构化解读
-> 三千股数据池与建模交接说明
```

应用定位是课程研究工具，不连接券商、不发送交易指令，也不承诺收益。

正式的数据交接材料见 [数据集卡片](docs/dataset-card.md)，其中记录快照范围、来源血缘、字段字典、质量门控、已知偏差、复现步骤以及给成员 B/C 的治理和建模说明。

第一次接手仓库请先阅读 [交接说明](HANDOFF.md)，其中列出当前数据版本、Git 与外部数据包的边界、启动步骤和验收命令。

## 当前能力

- **三千股、跨市场周期数据池**：股票池可配置到 5,000 只，历史窗口可配置到 5,000 个交易日；默认采集 3,000 只 × 1,000 个交易日，扩容时生成独立且可复核的新快照。
- **真实网络数据**：东方财富优先，腾讯与新浪按接口回退；每行保留 `source`、`adjustment` 和 `fetched_at`。
- **断点续采**：命令行采集器逐只写入 JSONL 与 `state.json`，网络中断后可继续，不会丢掉整批数据。
- **数据质量核验**：统计股票数、证券-日行数、日期范围、失败数、重复主键、非法价格和缺失字段。
- **双源核价**：东方财富与腾讯实时价格交叉检查，偏差超阈值时先进入复核，不把单一报价当作事实。
- **AI 研究链**：页面逐股展示“重采行情 -> 双源核价 -> Python 指标 -> 规则打分 -> 新闻公告 -> DeepSeek 解读”六步流程。
- **14 项可解释指标**：每项都展示当前值、公式与输入、衡量含义、后续建模用法和证据来源。
- **资讯与风险提醒**：检索近 30 天新闻和公司公告，用风险词典标记待核验线索；标题不能被模型外推为确定结论。
- **建模交接**：明确未来 5/20 日收益标签、时间切分、泄漏检查、IC/RankIC、分层回测、组合约束和成本敏感性。
- **覆盖度审计与列式导出**：记录快照交易日、每股历史长度、首末日期和区间缺口，并生成按年分区的 Parquet 与 DuckDB；原始 CSV 保持不变，可随时重建复核。
- **24 因子研究面板**：按成员 C 的最小完备因子集生成价值、质量、成长、量价 Alpha 和风险中性五组因子；财务按公告日对齐，股本按生效日对齐，分红按除息日对齐，并提供 Parquet、DuckDB、因子字典和覆盖审计。
- **研究流水线 v2**：分位数主模型 + 自适应区间校准 + 排序组合 + 六维持仓诊断 + 建议引擎 + AI 证据包；默认 10 特征、3 种子、无元模型，按风险偏好输出五档组合阶梯。

## 数据工作台（网页）

`python run_server.py --port 8789` 后打开 `http://127.0.0.1:8789/`，从上到下四步：

| 步骤 | 位置 | 做什么 |
| --- | --- | --- |
| 1. 采集数据池 | 左上表单 | 选股票数与历史天数，点"采集数据池" |
| 2. 选快照 | "历史快照"下拉框 | 选中要处理的快照 |
| 3. 生成研究导出 | 覆盖度区右上 | 点"生成研究导出"，得到覆盖度、Parquet、DuckDB |
| 4. 采集基本面 | 因子区底部 | 点"采集基本面"（后台任务，可看进度） |
| 5. 构建因子面板 | 因子区右上 | 采集完成后按钮出现，点击生成 24 因子面板 |

右侧"股票风险体检"是独立入口：输入最多 8 个代码、选 60/120/250 日窗口，点"生成研究报告"，
会逐只重采、双源核价、检索资讯并调用 DeepSeek 生成风险简报。

## 研究流水线 v2

`v2/` 是新增的建模与迭代层（`v2/research/` + `v2/cli.py`），只读 `data/raw/` 下的快照，不改动采集、落盘与网页代码。

- [三份报告](v2/docs/reports/01-usage-and-simulation.md)：使用说明与模拟、构建过程、PPT 建议
- [网页版组合研究](v2/README.md#网页版组合研究页)：`python run_server.py` 后打开 `/v2.html`
- [完整示范 v2](v2/docs/walkthrough-v2.md)：2500 日快照 + 真实行业，从采集到选股的完整流程
- [使用说明](v2/docs/usage.md)：环境、采集、两条默认命令、参数与输出含义
- [模型与分析总结](v2/docs/summary.md)：当前结果、决策依据、局限与下一步
- [模型构建思路报告](v2/docs/model-report.md)：数据取舍、特征与模型选择、消融对照
- [设计与迭代记录](v2/docs/workflow.md)：九阶段设计、验收口径、变体实验与校准

```powershell
# 空仓：五档风险阶梯 + 推荐组合 + 整手建仓计划
python v2/cli.py run --risk 5.5 --horizon 2

# 已持股：额外输出六维诊断与加减仓建议，并生成简报
python v2/cli.py run --risk 7 --horizon 2 `
  --holdings "002242:0.30,601857:0.20,300760:0.10" --report

# 组合层事后回测：净值为准，含换手缓冲与交易成本
python v2/cli.py backtest --horizon 2 --risks 1,5.5,10 --modes optimize,index
```

默认只走一条路径：10 个固定特征 → 20 日行业中性标签 → 分位数模型（3 种子）→ 自适应校准 → 排序组合；
元模型默认关闭，样本外 IC 低于 0.02 时自动切换为等权分散。期限三档（5/20/60 日），默认 20 日。
`grid`、`experiment`、组合模式、标签变换等对比开关属于研究用途，用法见
[进阶用法](v2/docs/workflow.md#14-进阶用法默认路径之外的可选项)，
实测记录见 [迭代记录](v2/docs/workflow.md#11-迭代记录v10--v11)、
[组合层回测](v2/docs/workflow.md#12-组合层回测v12) 与
[600 交易日复跑](v2/docs/workflow.md#13-600-交易日数据复跑v13)。

命令行的 `--report` 会调用 DeepSeek 生成简报；未配置 `DEEPSEEK_API_KEY` 时自动回退为规则模板，并在结果中记录原因。

## 快速开始

Windows PowerShell：

```powershell
git clone https://github.com/guzhi-maker/quantguard-research.git
cd quantguard-research

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe run_server.py --port 8789
```

浏览器打开 `http://127.0.0.1:8789/`。

Git 不包含原始 CSV、财务快照、因子库和研究交接 ZIP。只启动界面时不需要下载大文件；需要复现当前报表时，按 [交接说明](HANDOFF.md) 将外部数据包放回对应目录。

在本地 `.env` 中配置 DeepSeek：

```dotenv
DEEPSEEK_API_KEY=your-deepseek-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

`.env` 已被 Git 忽略。未配置密钥时，行情采集、核价、指标计算和规则预警仍可运行；页面会明确显示 DeepSeek 未配置，不会用模板冒充模型输出。

## 采集三千股数据

### 页面采集

在“研究数据池”中选择股票数量和历史交易日，点击“采集数据池”。页面会显示任务进度、事件、最终快照编号、行数、日期范围和失败数，并提供日线、行情、股票池 CSV 与 manifest 下载。

公开接口的流动性股票池经去重、过滤 ST/退市后，实际数量可能略少于请求值。例如请求 3,000 只时，当前实际得到 2,983 只；3,000 档的验收门槛为成功股票数不少于 2,500、记录可追溯且失败项明确。

### 命令行采集与断点续采

```powershell
.\.venv\Scripts\python.exe data_agent\collect_dataset.py --universe-size 3000 --history-days 1000
```

如果网络中断：

```powershell
.\.venv\Scripts\python.exe data_agent\collect_dataset.py --staging data\raw\_staging\<staging目录> --history-days 1000
```

采集完成后自动生成不可覆盖的正式快照：

```text
data/raw/<run_id>/market_daily.csv
data/raw/<run_id>/market_quotes.csv
data/raw/<run_id>/universe.csv
data/manifests/<run_id>.json
```

`data/raw/`、`data/audits/` 和 `.env` 不应提交到 Git。仓库只保存采集程序、契约、测试和研究说明；数据文件通过本地快照或课程网盘交接。

当前正式快照 `RAW-20261110-225213-E359DC` 已通过独立复核：2,983 只股票、2,868,954 条证券-日记录、2,983 条实时行情，覆盖 2022-07-19 至 2026-11-10；0 条失败记录、0 个重复主键、0 行非法价格。全部日线均来自东方财富前复权接口，`unverified` 行为 0。运行以下命令可重新核对文件哈希、行数和日期范围：

```powershell
.\.venv\Scripts\python.exe tools\verify_snapshot.py RAW-20261110-225213-E359DC
```

## 研究派生层

原始 CSV 是唯一证据，派生层用于审计覆盖度并加速后续建模。派生文件写入 `data/derived/<run_id>/`，不覆盖 `data/raw/`、manifest 或任何原始 SHA256。

当前快照的派生层结果：

```text
快照交易日并集: 1,045
每股记录中位数: 1,000
达到请求 1,000 日: 2,676 只
少于 950 日: 267 只（主要是上市较晚）
生命周期完整度: 99.885%
内部存在会话缺口: 454 只，合计 3,303 个会话
Parquet 行数: 2,868,954（按自然年分区）
```

缺口只表示股票首末记录之间缺少行情行，可能来自停牌或接口缺行，不等同于采集失败；采集失败仍以 manifest 的 `failures` 为准。

生成或重建：

```powershell
.\.venv\Scripts\python.exe tools\build_research_exports.py RAW-20261110-225213-E359DC
.\.venv\Scripts\python.exe tools\build_research_exports.py RAW-20261110-225213-E359DC --rebuild
```

如需包含按年 Parquet 副本的完整研究包：

```powershell
.\.venv\Scripts\python.exe tools\package_research_exports.py RAW-20261110-225213-E359DC
```

目录结构、覆盖度口径、示例 SQL 和派生文件哈希见 [研究派生层与覆盖度审计](docs/research-exports.md)。

课程交接推荐生成精简包。它省略与 DuckDB 重复的大体量 Parquet 分区，保留
全部行情、因子和辅助数据：

```powershell
.\.venv\Scripts\python.exe tools\package_research_exports.py RAW-20261110-225213-E359DC --compact
```

输出 `exports/<run_id>_research_compact.zip`。当前包为 618,530,441 B，约 590 MiB；
连同原始行情包和基本面包合计约 701 MiB，低于课程交接的 1 GB 限制。

## 24 因子面板

当前全量结果对应行情快照 `RAW-20261110-225213-E359DC` 与基本面快照
`FUND-20261113-222652-101412`：

```text
股票: 2,983
证券-日: 2,868,954
24 因子齐全: 2,769,089（96.52%）
交易日: 2022-07-19 至 2026-11-10
```

构建命令：

```powershell
.\.venv\Scripts\python.exe tools\build_factor_panel.py `
  RAW-20261110-225213-E359DC `
  --fund-id FUND-20261113-222652-101412 --rebuild
```

产物位于 `data/derived/<run_id>/factors/`，包含按年分区的 `factor_panel`、
`factors.duckdb`、季度财务、股本、分红、不复权价格、沪深 300、因子字典、覆盖度
和 manifest。完整公式、已知边界和网页抽查口径见
[A 股多因子数据采集标准](docs/factor-data-standard.md)。

## 个股研究报告

“股票风险体检”默认用 `600519,300750,601318` 作为课堂示例。每次报告都会重新抓取真实行情，并把结果保存为独立 `AUDIT-*` 文件。

报告包含：

1. 个股当前风险等级与数据核对状态。
2. 六步 AI 研究链，逐步说明输入、输出和执行者。
3. 14 项指标明细，包括最新价、1/5/20 日收益、20 日年化波动率、20/60 日最大回撤、20/60 日均线、均线偏离、成交量比、双源核价偏差和资讯信号。
4. 新闻与公告证据、数据质量问题和原始 CSV 链接。
5. DeepSeek 中文研究简报，以及给建模同学的研究设计和组合风控约定。

数值边界：

- Python 负责价格、收益、波动率、回撤、均线、量比和规则分数。
- DeepSeek 负责整理证据、解释指标和撰写简报，不能补写数值或捏造事实。
- 新闻标题只作为线索；结论必须回到公告原文、行情和财务证据复核。

## 数据契约

日线主键为 `(date, symbol)`，核心字段：

```text
date, symbol, name, industry, open, high, low, close,
volume, amount, amplitude, change_pct, change, turnover,
source, adjustment, fetched_at
```

复权标记：

- `qfq`：接口明确返回前复权数据。
- `unverified`：复权口径无法稳定确认，交给数据治理同学复核。

manifest 记录请求参数、来源、复权口径、行数、日期范围、失败股票、质量计数、文件 SHA256、来源血缘和已知限制。原始快照只追加，不覆盖；需要清洗时由成员 B 建立新的治理版本。

## 建模交接

数据池面向横截面监督学习，推荐约定：

- **标签**：未来 5 日和 20 个交易日收益，或是否跑赢行业内中位数。
- **特征**：收益、波动、回撤、均线偏离、量比、核价偏差、资讯信号及行业/流动性控制变量。
- **切分**：按日期切分训练、验证和样本外区间，禁止随机打乱；重叠标签窗口之间设置间隔。
- **防泄漏**：特征只使用信号日及以前可得的信息；标签从下个交易日开始计算；检查复权、停牌和公告发布时间。
- **评估**：同时报告 IC/RankIC、分层收益、换手率、最大回撤、样本外稳定性和交易成本敏感性。
- **组合**：预测分数只用于横截面排序、风险过滤或权重约束，并结合行业中性、单票上限、波动率目标和现金约束。

## API

- `GET /api/overview`：智能体状态、数据源注册表和最近快照摘要。
- `GET /api/runs`：最近原始快照摘要，不返回大体量 trace。
- `GET /api/runs/{run_id}`：读取完整 manifest。
- `GET /api/runs/{run_id}/rows?view=daily`：分页浏览日线、行情或股票池。
- `GET /api/runs/{run_id}/csv?view=daily`：导出完整 CSV。
- `GET /api/runs/{run_id}/coverage`：读取覆盖度审计摘要、年度分布和缺口说明。
- `GET /api/runs/{run_id}/derived`：读取 Parquet、DuckDB 与派生层 manifest。
- `GET /api/runs/{run_id}/derived-file?path=...`：下载指定派生文件。
- `GET /api/runs/{run_id}/fundamentals`：读取基本面快照摘要与文件清单。
- `GET /api/runs/{run_id}/factors`：读取 24 因子字典、覆盖度、来源和 DuckDB 清单。
- `GET /api/runs/{run_id}/factor-sample?limit=30&q=...`：读取最近交易日的因子横截面。
- `GET /api/runs/{run_id}/factor-file?path=...`：下载因子 Parquet、DuckDB、字典或 manifest。
- `POST /api/runs/{run_id}/exports`：生成派生层，可传 `{"rebuild": true}` 重建。
- `POST /api/jobs`：创建 `fixed` 数据池采集、`agent` 智能体采集或 `risk` 研究报告任务。
- `GET /api/jobs/{job_id}`：读取进度、事件和结果。
- `GET /api/audits`：列出历史研究报告。
- `GET /api/audits/{audit_id}`：读取一份完整研究报告。

## 项目结构

```text
data_agent/
├── agent.py             # DeepSeek function calling 与工具循环
├── catalog.py           # 数据源注册表
├── collect_dataset.py   # 三千股断点续采 CLI
├── collect_fundamentals.py # 财务、股本、分红、不复权价采集 CLI
├── fundamentals.py      # FUND-* 快照与来源血缘
├── factor_panel.py      # 24 因子时点对齐、覆盖审计与 DuckDB
├── news_client.py       # 新闻与公告检索
├── research_exports.py  # 覆盖度审计、Parquet 与 DuckDB 派生层
├── risk.py              # 指标、核价、规则预警与研究链
├── risk_service.py      # 个股研究报告流水线
├── service.py           # 固定/智能采集任务编排
├── store.py             # CSV、manifest、哈希和分页读取
├── server.py            # 本地 HTTP API
└── static/              # 研究数据台前端
tools/
├── build_research_exports.py    # 生成覆盖度与列式导出
├── build_factor_panel.py        # 重建 24 因子面板
├── package_research_exports.py  # 打包课程交接 ZIP
└── verify_snapshot.py           # 独立复核原始快照
v2/                              # v2 交付目录，只读 data/raw
├── cli.py                       # 命令行入口
├── collect_long.py              # 长历史采集（腾讯 proxy 分页，2500 日）
├── fetch_industry.py            # 抓取真实行业分类，生成映射表
├── research/                    # 建模与分析代码
│   ├── preferences.py           # 风险预算与期限偏好
│   ├── panel.py                 # 面板与可成交性掩码
│   ├── features.py              # 特征、中性化、去冗余、IC 衰减
│   ├── labels.py                # 行业中性标签与防泄漏
│   ├── primary.py               # 分位数主模型与区间校准
│   ├── meta.py                  # 元模型仓位乘数
│   ├── portfolio.py             # 均值-CVaR 组合与整手
│   ├── diagnosis.py             # 六维持仓诊断
│   ├── advice.py                # 建议引擎与预算
│   ├── validation.py            # 时间折、DSR、PBO
│   ├── scoring.py               # 滚动打分面板
│   ├── backtest.py              # 组合层回测
│   ├── evidence.py              # 六块证据包
│   ├── report.py                # AI 报告与回退
│   └── pipeline.py              # 端到端编排
├── tests/test_research_v2.py    # 27 项验收测试
├── docs/                        # 使用说明、完整示范、模型报告、设计记录
├── data/industry_map.csv        # 离线行业映射
└── artifacts/                   # 复跑结果 JSON
webapp/
├── market_client.py     # 东方财富、腾讯、新浪真实行情连接器
└── ...
docs/
├── dataset-card.md
├── research-exports.md
└── data-agent-workbench.md
tests/
```

## 测试

```powershell
$env:LOKY_MAX_CPU_COUNT='1'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests\test_research_v2.py -q
node --check data_agent\static\app.js
node tools\check_api.mjs
node tools\check_layout.mjs
```

测试覆盖参数校验、原始 CSV、manifest、SHA256、来源血缘、分页、DeepSeek 工具循环、双源核价、14 项指标解释、六步研究链、新闻失败记录和 HTTP API。v2 流水线另有 16 项测试，覆盖标签不越界、偏好插值、中性化、分位数覆盖率、CVaR 阶梯单调、建议预算与安全豁免、证据包规模、报告校验与回退。

`check_api.mjs` 和 `check_layout.mjs` 需要先启动本地服务；布局检查使用 Edge 的 DOM 模式，不需要截图。

派生层另包含覆盖度分布、Parquet 行数、DuckDB 可读性和交接 ZIP 内容测试。

## 研究边界

- 当前股票池是抓取时点的流动性快照，不是历史时点成分股，存在幸存者偏差。
- 行业分类不是严格的历史快照，公开接口可能有延迟、限流或字段调整。
- 腾讯和新浪的成交额在部分回退场景下可能由成交量与价格估算，必须结合 `source` 使用。
- 20 日重叠标签会降低样本独立性，模型验证必须按时间切分。
- 风险评分和新闻关键词只用于课程研究，不构成投资建议或交易指令。
