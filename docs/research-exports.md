# 研究派生层与覆盖度审计

本层从不可变原始快照生成，不修改 `data/raw/`、manifest 或任何 SHA256。用途是让数据治理、机器学习和回测人员更快理解样本覆盖，并通过 Parquet/DuckDB 读取数据。

## 当前交付

原始快照：`RAW-20261110-225213-E359DC`

```text
data/derived/RAW-20261110-225213-E359DC/
├── coverage.json
├── coverage_by_symbol.csv
├── coverage_by_year.csv
├── quantguard.duckdb
├── query_examples.sql
├── export_manifest.json
└── parquet/
    ├── market_daily/
    │   ├── year=2022/part-00000.parquet
    │   ├── year=2023/part-00000.parquet
    │   ├── year=2024/part-00000.parquet
    │   ├── year=2025/part-00000.parquet
    │   └── year=2026/part-00000.parquet
    ├── market_quotes.parquet
    └── universe.parquet
```

本次派生层结果：

| 指标 | 结果 |
| --- | ---: |
| 股票数 | 2,983 |
| 证券-日记录 | 2,868,954 |
| 快照交易日并集 | 1,045 |
| 每股记录中位数 | 1,000 |
| 每股记录 P10 | 994 |
| 达到请求 1,000 日 | 2,676 只 |
| 少于 950 日 | 267 只 |
| 少于 500 日 | 101 只 |
| 生命周期完整度 | 99.885% |
| 内部存在会话缺口 | 454 只 |
| 内部缺失会话合计 | 3,303 |

少于 1,000 日主要来自上市较晚的股票。例如 `688827 N钶锐` 在快照日刚上市，只有 1 条记录。页面会展示对应分布，不会为这些股票补造上市前行情。

## 覆盖度口径

`coverage.json` 的 `method` 说明了计算方式：

1. 用全快照所有股票的证券-日并集构造会话索引。
2. 只在每只股票自身的第一条和最后一条记录之间检查缺口。
3. `missing_sessions` 表示区间内没有行情记录的会话数。
4. 停牌会导致区间内缺口，因此该字段只能提示“可能停牌或接口缺行”，不能直接当作网络采集失败。
5. 网络采集是否成功以原始 manifest 的 `failures`、重复主键和非法价格为准。

`coverage_by_symbol.csv` 保存全部股票的历史长度、首末日期、缺失会话、最大连续缺口、生命周期完整度和缺口日期范围。`coverage_by_year.csv` 保存每个自然年的交易日数与记录数。

## 生成命令

首次生成：

```powershell
.\.venv\Scripts\python.exe tools\build_research_exports.py RAW-20261110-225213-E359DC
```

重建派生层：

```powershell
.\.venv\Scripts\python.exe tools\build_research_exports.py RAW-20261110-225213-E359DC --rebuild
```

网页在快照尚未生成派生层时显示“生成研究导出”按钮。原始快照不会被改写。

## 交接 ZIP

课程交接时将派生层、原始 manifest 和文档打成一个 ZIP。原始 CSV 已在原始快照交接包中，不重复打包。

```powershell
.\.venv\Scripts\python.exe tools\package_research_exports.py RAW-20261110-225213-E359DC
```

输出 `exports/<run_id>_research.zip` 与同名 `.zip.sha256`，包含：

```text
README.md                交接说明、统计与使用方式
coverage*.json/csv      覆盖度证据
parquet/                按年分区日线与行情、股票池
quantguard.duckdb       DuckDB 数据库
query_examples.sql      示例 SQL
export_manifest.json    派生文件清单与 SHA256
raw_manifest.json       原始快照 manifest
docs/                   数据集卡片与本说明
```

## Parquet

`market_daily` 按自然年分区，使用 Zstandard 压缩。日期字段为 Arrow `date32`，股票代码保持字符串。适合：

- pandas + PyArrow 按年份或股票读取。
- Polars 延迟扫描。
- DuckDB 使用 `hive_partitioning` 直接扫描。
- 成员 C 按日期构建训练、验证和样本外切分。

CSV 继续作为原始证据；Parquet 是加速分析的可再生副本。

## DuckDB

`quantguard.duckdb` 包含：

```text
market_daily
market_quotes
universe
coverage_by_symbol
research_metadata
```

命令行查询：

```powershell
python -c "import duckdb; c=duckdb.connect('data/derived/RAW-20261110-225213-E359DC/quantguard.duckdb', read_only=True); print(c.execute('select count(*), count(distinct symbol) from market_daily').fetchone())"
```

示例 SQL：

```sql
SELECT date, symbol, close, volume
FROM market_daily
WHERE symbol = '600519'
ORDER BY date DESC
LIMIT 20;

SELECT symbol, rows, first_date, last_date, missing_sessions
FROM coverage_by_symbol
ORDER BY missing_sessions DESC, rows ASC
LIMIT 50;
```

## API

- `GET /api/runs/{run_id}/coverage`：覆盖度摘要和年份分布。
- `GET /api/runs/{run_id}/derived`：派生层 manifest、Parquet 与 DuckDB 信息。
- `GET /api/runs/{run_id}/derived-file?path=...`：下载指定派生文件。
- `POST /api/runs/{run_id}/exports`：生成派生层，请求体可传 `{"rebuild": true}`。

## 仍然存在的限制

- 股票池仍是快照时点的流动性样本，不是历史时点成分股，存在幸存者偏差。
- 内部缺失会话不等于交易所正式停牌标记，仍建议后续接入历史交易状态。
- 当前价格为抓取时点的前复权口径，未来公司行动可能改变历史复权价格。
- 派生层可重建，不能替代原始 CSV、manifest 和独立快照复核。
