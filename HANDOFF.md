# QuantGuard 交接说明

本文只回答四件事：拿到了什么、哪些文件在 Git 里、大文件怎么恢复、交接前如何验收。代码和研究的完整说明仍以 [README](README.md) 与 [数据集卡片](docs/dataset-card.md) 为准。

## 当前数据版本

| 项目 | 内容 |
| --- | --- |
| 行情快照 | `RAW-20261110-225213-E359DC` |
| 基本面快照 | `FUND-20261113-222652-101412` |
| 股票数 | `2,983` |
| 证券-日记录 | `2,868,954` |
| 日期范围 | `2022-07-19` 至 `2026-11-10` |
| 多因子面板 | 24 个正式因子，可用率 `96.52%` |
| 研究界面 | 个股风险体检、14 项指标、6 步研究链、24 因子横截面 |

仓库代码以 [GitHub main](https://github.com/guzhi-maker/quantguard-research) 的最新提交为准。

## 仓库与数据边界

Git 仓库只保存以下内容：

- 采集、核验、因子构建、风险分析和网页源码。
- 测试、数据契约、数据集卡片、因子标准和答辩材料。
- 最新行情快照的 manifest；历史 manifest 用于保留既往运行的来源证据。
- `.env.example`，不包含任何真实密钥。

以下大文件不进入 Git，需要通过课程网盘、GitHub Release 或线下拷贝交接：

| 文件 | 大小 | SHA256 | 用途 |
| --- | ---: | --- | --- |
| `RAW-20261110-225213-E359DC.zip` | 91,710,762 B | `91ce1ec7d60777e714df6761eb5b8ff51a5b2a968468d7d8abe2182901dcee91` | 原始行情 CSV 和快照 manifest |
| `FUND-20261113-222652-101412.zip` | 25,130,371 B | `ba9ef36d340c49726be027e5541e227fdf461f96e4a192a19cfbdd8fdccbbc91` | 原始财务、股本、分红、不复权和沪深 300 |
| `RAW-20261110-225213-E359DC_research.zip` | 1,363,424,190 B | `3da2d3ee61f9d7cb14b6187969521fba23fdc821072512588aad480c85b86281` | 24 因子 Parquet、DuckDB、覆盖度与交接文档 |

当前因子数据库单独校验值为：

```text
factors.duckdb
0460b4563efc574d58a075706553a708a025451cd38b1a63d2bcdbf84dbc232c
```

这三个 ZIP 默认位于本地 `exports/`，不会提交到 Git。

## 恢复工作区

### 1. 安装代码环境

```powershell
git clone https://github.com/guzhi-maker/quantguard-research.git
cd quantguard-research

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

DeepSeek 密钥可选。未配置密钥时，公开数据采集、核价、指标计算、规则预警和因子研究仍可使用。

### 2. 恢复原始数据

| 数据包 | 解压位置 | 最终目录 |
| --- | --- | --- |
| `RAW-20261110-225213-E359DC.zip` | `data\raw\` | `data\raw\RAW-20261110-225213-E359DC\` |
| `FUND-20261113-222652-101412.zip` | `data\fundamentals\` | `data\fundamentals\FUND-20261113-222652-101412\` |
| `RAW-20261110-225213-E359DC_research.zip` | 解压后取其 `RESEARCH-RAW-...` 内容 | `data\derived\RAW-20261110-225213-E359DC\` |

数据目录属于本地运行数据，已被 Git 忽略。不要把数据解压到 `data_agent/`、`webapp/` 或其他源码目录。

将两个原始数据 ZIP 放入 `exports/`，然后在仓库根目录执行：

```powershell
Expand-Archive exports\RAW-20261110-225213-E359DC.zip data\raw
Expand-Archive exports\FUND-20261113-222652-101412.zip data\fundamentals
```

解压后应分别得到：

```text
data/raw/RAW-20261110-225213-E359DC/
data/fundamentals/FUND-20261113-222652-101412/
```

### 3. 恢复研究派生层

```powershell
$bundle = Join-Path $env:TEMP 'quantguard-research-handoff'
Expand-Archive exports\RAW-20261110-225213-E359DC_research.zip $bundle
$source = Join-Path $bundle 'RESEARCH-RAW-20261110-225213-E359DC'
$target = 'data\derived\RAW-20261110-225213-E359DC'
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item (Join-Path $source '*') $target -Recurse -Force
```

完成后，网页可直接读取：

```text
data/derived/RAW-20261110-225213-E359DC/factors/factors.duckdb
data/derived/RAW-20261110-225213-E359DC/quantguard.duckdb
```

如果只需要下游建模，也可以直接解压研究 ZIP，在其 `RESEARCH-*` 目录内使用 DuckDB 和 Parquet，不必恢复网页目录结构。

不要直接把解压得到的 `RESEARCH-RAW-...` 文件夹整个放进 `data/derived/`。网页要求研究包中的 `factors/`、`parquet/`、`coverage*.json` 和 `quantguard.duckdb` 出现在 `data/derived/RAW-20261110-225213-E359DC/` 直属目录下。

### 4. 可选：恢复历史 AI 研究报告

当前保存的课堂示例报告不在三个数据 ZIP 中。如果负责人另外提供了 `AUDIT-20261110-000919-14F9EE` 文件夹，将它放到：

```text
data/audits/AUDIT-20261110-000919-14F9EE/
```

恢复该目录后，网页会直接展示贵州茅台、宁德时代和中国平安的 14 项指标、六步研究链和新闻证据。没有该目录不影响行情采集、因子研究和现场重新生成报告。

### 5. 启动研究页面

```powershell
.\.venv\Scripts\python.exe run_server.py --port 8789
```

浏览器打开 `http://127.0.0.1:8789/`。

## 验收命令

```powershell
$env:LOKY_MAX_CPU_COUNT = '1'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\verify_snapshot.py RAW-20261110-225213-E359DC
node --check data_agent\static\app.js
node tools\check_api.mjs
node tools\check_layout.mjs
```

当前验收基线：

```text
75 passed
原始行情 CSV 的 SHA256、行数、日期范围全部一致
2,983 只股票
2,868,954 条证券-日记录
0 个采集失败、0 个重复主键、0 行非法价格
24 因子齐全率 96.52%
桌面和 390px 手机布局均无横向溢出或元素重叠
```

## 交接后的下一步

- 成员 B：以数据集卡片、manifest 和因子字典为底稿完成治理说明、EDA、PPT 和答辩题库。
- 成员 C：直接使用 `factors.duckdb` 的 `factor_panel` 表做标签、时间切分、建模和组合风控。
- 任何数据修复都应创建新的 `RAW-*` 或 `FUND-*` 快照，不覆盖当前版本。
- 建模前必须对极端财务比率做缩尾和行业、市值中性化；负净资产公司的杠杆率可能大于 1，利润接近零时现金流利润比可能极端放大。
- 新闻标题只作为线索，不能直接当作已证实事实或收益信号。

## 禁止提交

```text
.env
API Key
data/raw/
data/fundamentals/
data/derived/
data/audits/
exports/
server*.log
collect-*.log
```

提交前至少执行 `git status --short`，确认只包含源码、测试、文档和小体量 manifest。
