# A 股多因子数据采集标准

本文件对应成员 C 给出的中频多因子最小完备因子集。执行层以不可变行情快照
`RAW-*` 为行情主表，以独立基本面快照 `FUND-*` 为财务、股本、分红与不复权价格
来源，最终派生到：

```text
data/derived/<RAW_RUN_ID>/factors/
```

原始快照与基本面快照都不会被因子构建覆盖。因子面板可以重建，但原始证据文件
保留各自 SHA256。

## 1. 数据层级

```text
公开接口
  -> RAW-* daily/quotes/universe
  -> FUND-* profiles/financials/share_capital/dividends/prices/index
  -> factors/factor_panel/year=*/part-*.parquet
  -> factors/factors.duckdb
```

页面与 API 只读取本地快照，不在前端触发全市场网络采集。

## 2. 底层行情字段

| 字段 | 含义 | 口径 |
| --- | --- | --- |
| `date` | 交易日 | `YYYY-MM-DD` |
| `symbol` | 6 位股票代码 | 字符串，保留前导零 |
| `open/high/low/close` | 前复权 OHLC | 原始日线快照 |
| `volume` | 成交量 | 数据源原始单位 |
| `amount` | 成交额 | 数据源原始单位 |
| `turnover` | 换手率 | 百分数数值，例如 `0.77` 表示 `0.77%` |
| `total_cap` | 历史总市值 | 不复权收盘价 × 当日适用总股本 |
| `float_cap` | 历史流通市值 | 不复权收盘价 × 当日适用流通股本 |
| `close_raw` | 不复权收盘价 | 用于历史市值与股息率 |

前复权价格只用于收益、动量和波动类因子；历史市值必须使用不复权价格乘对应
日期已经生效的股本，不能直接用当前市值回填历史。

不复权日线依次尝试新浪、腾讯与东方财富公开接口。每行 `prices_unadjusted.csv`
保留实际 `source`，用于区分新浪、腾讯和东方财富来源。价格请求独立限流，
避免基本面主采集并发触发行情接口限制。

当前 `FUND-20261113-222652-101412` 的不复权价格血缘为：

- 新浪财经：2,637,133 行
- 腾讯证券：340,775 行

因子数据库的 `prices_unadjusted` 表同样保留 `source` 与 `fetched_at`，因子
manifest 写入 `source_summary.unadjusted_price_rows_by_source`，下游可复核。

## 3. 财务与辅助字段

### 财务

| 本地字段 | 成员 C 字段 | 说明 |
| --- | --- | --- |
| `revenue_ytd` | `revenue` | 营业收入累计值，单季值另行差分 |
| `net_profit_ytd` | `net_profit` | 归母净利润累计值 |
| `operating_cf_ytd` | `operating_cf` | 经营活动现金流净额累计值 |
| `cost_of_sales_ytd` | `cost_of_sales` | 营业收入减营业毛利 |
| `total_asset` | `total_asset` | 期末总资产 |
| `total_liability` | `total_liability` | 期末总负债 |
| `equity` | `equity` | 期末归属权益口径 |
| `report_date` | - | 财报报告期 |
| `ann_date` | `ann_date` | 公告披露日，时点对齐核心字段 |

累计报表先转换为单季值：

```text
单季值(Q1) = 一季报累计值
单季值(Q2-Q4) = 本期累计值 - 上期累计值
```

TTM 规则：

```text
Q1-Q3 TTM = 本期累计值 + 上年年报值 - 上年同期单季值
Q4 TTM     = 当年年报累计值
```

Q1-Q3 不得使用当年尚未披露的年报。因子按 `ann_date <= date` 选择最近一次
已公告财务记录，禁止使用 `report_date <= date` 直接回填。

### 股本、行业与分红

| 本地字段 | 说明 |
| --- | --- |
| `industry` | 申万一级行业 |
| `industry_l2` | 申万二级行业 |
| `industry_l3` | 申万三级行业 |
| `list_date` | 上市日期 |
| `total_shares` / `float_shares` | 当日适用股本 |
| `div_ttm_per_share` | 近 12 个月已实施税前每股现金分红 |
| `report_date` / `ann_date` | 当前可用财务报告期与公告日 |
| `financial_template` | `general` 或 `financial` 报表模板 |
| `factor_ready` | 24 个正式因子是否全部可用 |
| `missing_groups` | 缺失因子组，逗号分隔 |

分红只使用 `status` 含“实施”且 `ex_dividend_date <= date` 的记录。银行、保险
等金融模板没有有意义的毛利率，`gross_margin` 保留空值，但不会因此把整个质量组
误判为缺失。

`financial_template` 按申万一级行业划分：`银行`、`非银金融` 为 `financial`，其余
行业为 `general`。不能因为某家工业公司暂缺营业成本就推断其为金融模板。当前基础
快照有 1 条历史财务记录缺少 `ann_date` 且核心数值为空；派生层会剔除该记录，并在
因子 manifest 的 `quality.financial_rows_without_ann_date_excluded` 中披露数量。

## 4. 正式因子

单位说明：

- `ratio`：导出值为小数，页面乘以 100 展示为百分比。
- `percent`：字段本身已是百分数数值。
- `log_cny`：总市值自然对数。

### 价值

| 字段 | 公式 | 时点 |
| --- | --- | --- |
| `ep_ttm` | 归母净利润TTM / 总市值 | 公告日 |
| `bp_ttm` | 归属权益 / 总市值 | 公告日 |
| `sp_ttm` | 营业收入TTM / 总市值 | 公告日 |
| `cfp_ttm` | 经营现金流TTM / 总市值 | 公告日 |
| `div_yield` | 近12个月每股分红 / 不复权收盘价 | 除息日 |

### 质量

| 字段 | 公式 | 时点 |
| --- | --- | --- |
| `roe_ttm` | 归母净利润TTM / 平均净资产 | 公告日 |
| `roa_ttm` | 归母净利润TTM / 平均总资产 | 公告日 |
| `gross_margin` | (营收TTM - 营业成本TTM) / 营收TTM | 公告日 |
| `net_margin` | 归母净利润TTM / 营收TTM | 公告日 |
| `ocf_ni_ratio` | 经营现金流TTM / 归母净利润TTM | 公告日 |

### 成长

| 字段 | 公式 | 时点 |
| --- | --- | --- |
| `ni_growth_q` | (单季净利润 - 上年同季) / abs(上年同季) | 公告日 |
| `rev_growth_q` | (单季营收 - 上年同季) / abs(上年同季) | 公告日 |

### 量价 Alpha

| 字段 | 公式 |
| --- | --- |
| `ret_5d` | 5 个交易日收益 |
| `rev_5d` | `-ret_5d` |
| `ret_10d` | 10 个交易日收益 |
| `rev_10d` | `-ret_10d` |
| `mom_20d` | 20 个交易日收益 |
| `vol_20d` | 20 个日收益标准差 |
| `turn_20d` | 20 个交易日换手率均值 |
| `rs_20d` | 收盘价 / 20 日最高价 |

价格类窗口均使用前复权价格，计算只依赖当前及过去交易日。

### 风险中性

| 字段 | 公式 | 时点 |
| --- | --- | --- |
| `ln_cap` | `ln(不复权收盘价 × 总股本)` | 股本生效日 |
| `beta_20d` | 个股收益对沪深300收益的20日OLS斜率 | 仅历史价格 |
| `residual_vol` | 20日回归残差标准差 | 仅历史价格 |
| `leverage` | 总负债 / 总资产 | 公告日 |

## 5. 构建命令

先续采或采集基本面：

```powershell
& 'C:\Users\25187\Desktop\因果低秩适配\venv\Scripts\python.exe' `
  data_agent\collect_fundamentals.py `
  --from-run RAW-20261110-225213-E359DC `
  --workers 12 --price-workers 2
```

再构建因子面板：

```powershell
& 'C:\Users\25187\Desktop\因果低秩适配\venv\Scripts\python.exe' `
  tools\build_factor_panel.py RAW-20261110-225213-E359DC `
  --fund-id FUND-YYYYMMDD-HHMMSS-XXXXXX --rebuild
```

只验证指定股票：

```powershell
& 'C:\Users\25187\Desktop\因果低秩适配\venv\Scripts\python.exe' `
  tools\build_factor_panel.py RAW-20261110-225213-E359DC `
  --fund-id FUND-YYYYMMDD-HHMMSS-XXXXXX --rebuild `
  --symbols 600519,300750,000001
```

断点续采时使用同一个 `--staging` 路径。最终写快照前会按自然键去重：

公司资料、财务、股本或不复权价格等关键阶段失败时，股票不会被标记为完成。
再次使用同一 staging 续采会自动重试这些股票；只有所有关键阶段成功，快照才会
标记为 `complete`。

- 公司资料：`symbol`
- 财务：`symbol + report_date`
- 股本：`symbol + end_date`
- 分红：`symbol + report_date + ex_dividend_date`
- 不复权价格：`symbol + date`

## 6. DuckDB 使用

```python
import duckdb

con = duckdb.connect("data/derived/RAW-.../factors/factors.duckdb", read_only=True)
con.sql("""
    SELECT date, symbol, name, industry,
           ep_ttm, roe_ttm, mom_20d, ln_cap, beta_20d
    FROM factor_panel
    WHERE factor_ready
    ORDER BY date DESC, symbol
    LIMIT 20
""").show()
```

主要表：

- `factor_panel`
- `financials_quarterly`
- `profiles`
- `share_capital`
- `dividends`
- `prices_unadjusted`
- `index_daily`
- `factor_metadata`

## 7. 已知边界

1. 股票池来自抓取时点的流动性样本，存在幸存者偏差。
2. 财务数据是公开接口当前可见版本，不保留历史财报修订版本。
3. 申万行业是抓取时点分类，不是逐日历史行业成分。
4. 分红为税前现金分红，不处理红利税。
5. Beta 使用 20 日短窗口，只用于中频风险控制，不应当作长期 CAPM 估计。
6. `gross_margin` 对金融模板应保持空值；建模时需要模板缺失指示变量或分行业建模。
7. `financial_template` 以申万一级行业为准；一般企业缺营业成本时，毛利率应视为缺失，不能自动豁免。
8. 复权因子来自行情供应商，拆股、送转和分红的口径以供应商为准。
9. 因接口历史长度限制，2019 年以前财务记录不一定完整；回测起点应与财务覆盖一起审计。
10. 新浪不复权日线单次最多返回约 1,023 个交易日；当前行情快照单股最长 1,000 日，
   因此历史市值覆盖完整。若未来把回测起点继续前移，需要改用支持更长窗口的数据源。
