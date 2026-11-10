# QuantGuard A 股千股研究数据集卡片 v1.0

## 1. 数据集定位

本数据集是“数据挖掘与机器学习”课程项目的第一阶段成果，用于支持 A 股横截面收益、风险特征、因子挖掘和组合建模研究。

它属于**带来源追踪的原始研究快照**，不是已经完成缺失值处理、单位统一和模型特征工程的训练集。第二阶段可以在不覆盖原始文件的前提下建立治理版本，第三阶段再按时间切分完成标签、建模和回测。

| 项目 | 内容 |
| --- | --- |
| 数据集名称 | QuantGuard 千股原始行情研究快照 |
| 快照编号 | `RAW-20261110-124549-780A2D` |
| 快照生成时间 | `2026-11-10T12:45:55+08:00` |
| 原始数据范围 | `2025-09-15` 至 `2026-11-10` |
| 请求股票数 | 1,195 |
| 成功股票数 | 1,188 |
| 证券-日记录 | 292,173 |
| 实时行情记录 | 1,188 |
| 采集失败记录 | 14 |
| 主键重复 | 0 |
| 非法价格 | 0 |
| 当前维护者 | 成员 A：数据采集、AI 数据核验、交互网页与数据文档 |
| 下游使用者 | 成员 B：数据整理、展示与汇报；成员 C：标签、机器学习、组合风控与研究报告 |

原始 CSV 位于 `data/raw/RAW-20261110-124549-780A2D/`，默认不提交到 Git。可复核 manifest 位于 `data/manifests/RAW-20261110-124549-780A2D.json`。

## 2. 产物清单

```text
data/raw/RAW-20261110-124549-780A2D/
├── market_daily.csv       # 个股日线，主键为 (date, symbol)
├── market_quotes.csv      # 抓取时点的最新行情快照，主键按 symbol 预期唯一
├── universe.csv           # 抓取时点按流动性筛选出的股票池
└── complete.json          # staging 完成标记，记录 run_id

data/manifests/
└── RAW-20261110-124549-780A2D.json
                           # 请求参数、来源、字段、哈希、质量、失败项和限制

data/audits/
└── AUDIT-20261110-000919-14F9EE/
                           # 3 只样板的六步 AI 研究链与 14 项指标证据
```

`data/raw/` 与 `data/audits/` 是本地研究产物，不应提交到公开仓库。课程网盘交接时，应同时保留这三个 CSV、manifest 和本说明。

## 3. 数据来源与血缘

股票池按抓取时点的成交额排序，优先使用东方财富公开行情接口，腾讯和新浪作为行情回退源。

| 来源 | 日线记录数 | 复权口径 | 说明 |
| --- | ---: | --- | --- |
| 东方财富前复权日行情 | 197,585 | `qfq` | 首选数据源 |
| 新浪财经日行情 | 65,821 | `unverified` | 日线回退源，成交额由成交量与价格估算 |
| 腾讯证券前复权日行情 | 28,767 | `qfq` | 日线回退源，成交额由成交量与价格估算 |
| 合计 | 292,173 | - | 其中 `qfq` 226,352 行，`unverified` 65,821 行 |

每一行均保留：

- `source`：该行实际采用的公开数据源。
- `adjustment`：明确返回前复权时记为 `qfq`；回退源无法稳定确认时记为 `unverified`。
- `fetched_at`：本地抓取时间，采用 UTC+08:00。

来源血缘、各来源行数和复权口径同时记录在 manifest 的 `lineage` 字段中。

## 4. 数据结构

### 4.1 `market_daily.csv`

粒度：一只股票一个交易日一行。主键：`(date, symbol)`。

| 字段 | 类型 | 含义 | 下游注意事项 |
| --- | --- | --- | --- |
| `date` | string | 交易日，`YYYY-MM-DD` | 转为日期后按股票分组排序 |
| `symbol` | string | 六位股票代码 | 保留前导零，禁止转成整数 |
| `name` | string | 股票简称 | 不是历史时点名称，可能有变更 |
| `industry` | string | 接口返回的行业或分类 | 当前回退值可能是“A股”，不能当作严格行业分类 |
| `open` | float | 开盘价 | 前复权或待确认口径，单位为元 |
| `high` | float | 最高价 | 与 `adjustment` 一起使用 |
| `low` | float | 最低价 | 与 `adjustment` 一起使用 |
| `close` | float | 收盘价 | 计算收益时优先在股票内使用 `close.pct_change()` |
| `volume` | float | 成交量 | 不同接口的“手/股”单位可能不同，跨来源比较前必须统一 |
| `amount` | float | 成交额 | 优先源为接口值；回退源可能为估算值，单位为元 |
| `amplitude` | float | 振幅 | 不同来源可能使用百分比点数或小数比例，必须按 `source` 标准化 |
| `change_pct` | float | 涨跌幅 | 不同来源单位不一致，禁止直接混合后建模 |
| `change` | float | 涨跌额 | 前复权序列上的派生值 |
| `turnover` | float | 换手率 | 回退源可能写为 `0.0` 表示不可得，不一定是真实零 |
| `source` | string | 实际数据来源 | 必保字段，用于血缘与单位判断 |
| `adjustment` | enum | `qfq` 或 `unverified` | `unverified` 必须由成员 B 复核 |
| `fetched_at` | ISO datetime | 抓取时间 | 不是交易所行情发布时间 |

### 4.2 `market_quotes.csv`

粒度：抓取时点每只股票一条最新行情。

| 字段 | 含义 | 下游注意事项 |
| --- | --- | --- |
| `symbol`, `name`, `industry` | 股票标识与分类 | 以股票代码为连接键 |
| `price`, `open`, `high`, `low`, `prev_close` | 最新价、开高低和昨收 | 若在开市前抓取，最新价可能是上一交易日收盘价 |
| `change`, `change_pct` | 涨跌额与涨跌幅 | `change_pct` 通常是小数比例，例如 `0.009` 表示约 `0.9%` |
| `turnover_rate` | 换手率 | 通常是小数比例 |
| `pe`, `pb` | 估值字段 | 可为空，不应以零填补 |
| `volume`, `amount` | 成交量和成交额 | 量纲取决于实际 `source`，标准化前不得横向比较 |
| `market_cap`, `float_market_cap` | 总市值与流通市值 | 单位由适配器规范化，使用前仍需抽检 |
| `source`, `updated_at`, `fetched_at` | 来源、源更新时间、本地抓取时间 | `updated_at` 可能早于 `fetched_at` |

### 4.3 `universe.csv`

粒度：本次研究股票池中每只股票一行，字段包括代码、名称、行业、快照价格、成交额、换手率、市盈率和市值。

该表主要用于确定研究成员和连接股票元数据。它描述的是**抓取时点**的流动性股票池，不是历史时点的指数成分股，因此不能直接用于无偏的历史回测。

### 4.4 manifest

manifest 是数据集的机器可读说明，主要包括：

- `request`：股票数、历史窗口、是否包含行情和请求股票列表。
- `summary`：成功股票数、证券-日记录数、日期范围、来源和失败数。
- `quality`：重复主键、非法价格、缺失收盘价/成交量/成交额、按来源和复权口径统计的行数。
- `failures`：失败股票、失败阶段和接口错误。
- `files`：CSV 路径、行数、字节数、列名和 SHA256。
- `lineage`：数据源、来源行数和复权口径。
- `limitations`：已知研究限制。
- `agent` 与 `trace`：智能体或命令行的执行证据。

摘要接口会移除大体量 `symbols` 列表和 `trace`，完整证据通过 manifest 获取。

## 5. 数据质量控制

当前快照已通过以下检查：

```text
[x] manifest status = complete
[x] 成功股票数 1,188 >= 1,000
[x] 请求股票数 1,195，成功股票数 1,188
[x] 日线记录 292,173
[x] 实时行情记录 1,188
[x] 日线日期范围 2025-09-15 至 2026-11-10
[x] 重复主键 0
[x] 非法价格 0
[x] 缺失收盘价 0
[x] 缺失成交量 0
[x] 缺失成交额 0
[x] 三个 CSV 的 SHA256 与 manifest 一致
[x] 14 条失败记录逐项保留
```

关键文件哈希：

| 文件 | SHA256 |
| --- | --- |
| `market_daily.csv` | `93ce56c3c9d2c836718ebf974e5f92c6f9bcde0b60fce6adf73df1d6d15e8b78` |
| `market_quotes.csv` | `ea33c109f3e47589569d4616daea1141cc9957373ff515519dc7049cf18a3062` |
| `universe.csv` | `2d9216ca226211c823213f4fe5791bd3a76c2f9b0c400e072a4a4a2555cfee84` |

一键复核：

```powershell
.\.venv\Scripts\python.exe tools\verify_snapshot.py RAW-20261110-124549-780A2D
```

失败记录主要来自部分北交所代码的腾讯/新浪接口覆盖不足，以及少量 SSL 中断。已知 `quality.symbols_missing_quotes` 包含 `301571`，该股票有日线数据但缺少对应实时行情。失败不能通过伪造数据补齐；需要时重新采集并生成新快照。

## 6. 采集与 AI 机器人流程

```text
研究请求
-> DeepSeek 选择白名单工具与参数
-> 抓取抓取时点流动性股票池
-> 东方财富优先，腾讯/新浪回退
-> 逐只写入 JSONL 与 state.json
-> 网络中断后从已完成股票继续
-> 生成 CSV、manifest 与 SHA256
-> 双源核价、Python 指标、规则预警
-> 新闻/公告证据检索
-> DeepSeek 输出结构化研究简报
```

智能体边界：

- DeepSeek 只能调用应用注册的工具，不能访问任意 URL。
- DeepSeek 不能直接补写价格、成交量、收益或指标数值。
- 数值由公开接口返回，并由 Python 计算。
- 新闻标题和模型摘要属于不可信内容，只能作为待核验线索。
- 本次千股原始快照采用确定性命令行采集以保证断点续采和可复现；AI 机器人负责研究任务编排、数据复核、新闻证据整理和解释，不冒充数值采集器。

采集中断后继续：

```powershell
.\.venv\Scripts\python.exe data_agent\collect_dataset.py `
  --staging data\raw\_staging\<staging目录> `
  --history-days 250
```

新建快照：

```powershell
.\.venv\Scripts\python.exe data_agent\collect_dataset.py `
  --universe-size 1200 `
  --history-days 250
```

## 7. 可交互 AI 网页

运行：

```powershell
.\.venv\Scripts\python.exe run_server.py --port 8789
```

打开 `http://127.0.0.1:8789/`。

页面承担两项与成员 A 相关工作：

1. **研究数据池**：浏览快照摘要、证券-日数据、实时行情和股票池，下载 CSV，发起普通采集或智能体采集任务。
2. **AI 个股研究**：以三只股票为样板，展示重采行情、双源核价、Python 指标、规则评分、新闻公告和 DeepSeek 解读的六步研究链；每只股票展示 14 项指标的值、公式、含义、证据和建模用途。

网页用于课堂展示和他人复查，不连接券商、不发送交易指令，也不承诺投资收益。

## 8. 已知限制与偏差

1. 股票池来自当前抓取时点的流动性排序，存在幸存者偏差和时点选择偏差，不是历史成分股。
2. 行业字段可能只是接口回退分类，不能直接用于严格的行业中性化。
3. 东方财富、腾讯、新浪对成交量、涨跌幅、振幅和换手率的单位或定义不完全一致。
4. 回退源的成交额由成交量与价格估算，只能作为近似值。
5. `unverified` 复权记录必须由数据治理阶段复核，不能直接与 `qfq` 行无差别建模。
6. 实时行情是抓取时点快照，`updated_at` 可能落后；盘前抓取时可能仍为上一交易日收盘信息。
7. 20 日重叠收益标签会降低样本独立性，建模必须使用时间切分和 purge/embargo。
8. 当前 1,188 只股票只覆盖一种流动性选择规则，不代表全市场，也不保证样本外收益。
9. 公开接口可能限流、变更字段或临时不可用，新快照与旧快照不能假定完全同分布。

## 9. 给成员 B 的数据治理建议

成员 B 应复制原始快照到新的治理目录，不覆盖 `RAW-*`：

1. 将 `symbol` 固定为六位字符串。
2. 按 `(date, symbol)` 排序并检查跨来源重复。
3. 只在同一 `source` 内判断量纲；建立成交量、振幅、涨跌幅和换手率的标准单位转换表。
4. 区分缺失、真实零和不可得，尤其是回退源的 `turnover = 0.0`。
5. 单独标记 `adjustment = unverified` 的记录，并说明最终采用或剔除规则。
6. 检查停牌、涨跌停、ST、上市不足窗口和异常跳价。
7. 保存治理前后行数、删除原因、字段转换和新的 SHA256。
8. 产出可供成员 C 直接读取的面板：日期 × 股票 × 特征 × 标签来源。

## 10. 给成员 C 的建模接口

建议至少形成以下时间序列面板：

```text
index: (date, symbol)
price: close_adj
features: return_1d, return_5d, return_20d, volatility_20d,
          drawdown_20d, drawdown_60d, ma20_distance, ma60_distance,
          volume_ratio, valuation, industry, liquidity
labels: forward_return_5d, forward_return_20d,
        excess_return_5d, excess_return_20d
```

建模约定：

- 标签从信号日后的下一个交易日开始计算。
- 训练、验证、测试按日期切分，禁止随机打乱。
- 重叠标签窗口之间设置 purge/embargo。
- 标准化、缺失值填充和特征筛选只能在训练窗口拟合。
- 同时报告 IC、RankIC、ICIR、分层收益、换手率、最大回撤和交易成本敏感性。
- 预测分数用于横截面排序或风险过滤，不把回测收益表述为未来保证。

## 11. 复现与交接检查

```text
[x] 原始快照 run_id 已记录
[x] manifest status = complete
[x] 成功股票数 >= 1,000
[x] CSV 行数与 manifest 一致
[x] 三个 CSV 的 SHA256 与 manifest 一致
[x] 重复主键与非法价格均为 0
[x] 日期范围与请求窗口一致
[x] 失败记录逐项保留
[x] 数据集卡片已提供
[x] 交互网页与 API 验收通过
[ ] 成员 B 建立治理版本并记录转换规则
[ ] 成员 C 完成标签、时间切分、模型、组合回测和研究报告
```

本卡片应与以下文档一起使用：

- [研究数据台使用说明](data-agent-workbench.md)
- [项目总览](../README.md)
- [最新 manifest](../data/manifests/RAW-20261110-124549-780A2D.json)
