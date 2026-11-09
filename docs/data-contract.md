# 数据与实验交接契约

本文件是三个人交接数据的唯一接口说明。接口变更必须同时修改本文件、实现和测试。

## 1. 原始行情清单

每次抓取生成唯一 `run_id`，并写出 `data/manifests/<run_id>.json`。

```json
{
  "run_id": "RAW-20261109-001",
  "created_at": "2026-11-09T10:00:00+08:00",
  "market_as_of": "2026-11-07",
  "sources": [
    {
      "name": "新浪财经公开行情",
      "url": "https://example.invalid",
      "fetched_at": "2026-11-09T10:00:00+08:00"
    }
  ],
  "universe_size": 80,
  "row_count": 40800,
  "symbol_count": 80,
  "adjustment": "qfq",
  "amount_definition": "交易所成交额或明确标注的估算值",
  "sha256": "原始文件哈希",
  "known_issues": []
}
```

## 2. 原始行情字段

必选字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `date` | date | 交易日期，`YYYY-MM-DD` |
| `symbol` | string | 六位 A 股代码 |
| `name` | string | 证券名称 |
| `industry` | string | 当前可得行业分类 |
| `close` | number | 收盘价或前复权收盘价 |
| `volume` | number | 成交量，单位为股 |
| `amount` | number | 成交额，单位为元 |
| `source` | string | 数据源名称 |
| `fetched_at` | datetime | 抓取时间 |

可选字段：

```text
open, high, low, pre_close, change_pct, turnover_rate,
pe, pb, market_cap, float_market_cap, adjustment
```

规则：

- `date + symbol` 在原始清单中必须唯一。
- `close` 必须大于零；非法价格不能进入特征。
- 成交额估算来源必须保留“估算”标记。
- 不允许用前向填充值替代原始抓取记录。

## 3. 数据治理产物

B 生成 `data/curated/<dataset_id>/` 和 `data/manifests/<dataset_id>.json`。

清单至少包含：

```json
{
  "dataset_id": "DATA-XXXXXXXXXXXX",
  "raw_run_id": "RAW-20261109-001",
  "market_as_of": "2026-11-07",
  "date_start": "2024-10-01",
  "date_end": "2026-11-07",
  "symbol_count": 78,
  "raw_rows": 39780,
  "model_rows": 27710,
  "duplicate_records": 0,
  "invalid_price_records": 0,
  "missing_close_pct": 0.0,
  "missing_amount_pct": 0.0,
  "excluded_symbols": [],
  "cleaning_rules": [],
  "quality_status": "pass"
}
```

治理规则：

1. 删除重复的 `date + symbol`，保留最后一条并计数。
2. 非正价格、未来日期和非法股票代码不得进入面板。
3. 短历史股票整只排除，并保留排除理由。
4. 时间序列缺口最多前向填充五个交易日，原始值保持不变。
5. 成交额和成交量缺失不伪造。
6. 所有规则必须同时出现在报告和机器可读清单中。

## 4. 建模输入

模型只接收以下字段：

```text
date
symbol
industry
features...
future_excess
label_end_date
```

约束：

- `future_excess` 和 `label_end_date` 不能进入特征矩阵。
- 时间切分必须按日期排序，不能随机打乱样本。
- 训练折内只使用标签已经结束的样本。
- 标准化、缺失填充和类别编码只能在训练折拟合。
- 留出期只能用于最终评估，不能用于选模型或调参数。

## 5. 实验产物

C 输出的实验 JSON 至少包含：

```json
{
  "experiment_id": "QG-YYYYMMDD-HHMMSS",
  "dataset_id": "DATA-XXXXXXXXXXXX",
  "data_mode": "live",
  "source": "实际数据源",
  "market_as_of": "YYYY-MM-DD",
  "parameters": {},
  "split": {
    "development": ["YYYY-MM-DD", "YYYY-MM-DD"],
    "holdout": ["YYYY-MM-DD", "YYYY-MM-DD"]
  },
  "model": {
    "selected": "random_forest",
    "selection_metric": "rank_ic"
  },
  "metrics": {
    "holdout_rank_ic": 0.0,
    "holdout_excess_return": 0.0,
    "net_annual_return": 0.0,
    "max_drawdown": 0.0
  },
  "holdings": [],
  "risk_actions": [],
  "limitations": []
}
```

任何负的样本外结果都必须保留。报告和 PPT 不得只截取全样本表现或验证期表现。

## 6. 智能体约束

DeepSeek 智能体可以负责：

- 选择已注册的数据源连接器。
- 生成抓取计划、重试策略和字段检查步骤。
- 汇总工具返回的统计结果和失败信息。
- 生成人类可读的数据来源说明。

DeepSeek 不得负责：

- 直接编写不存在的价格、收益、财务或风险数据。
- 绕过接口限制、付费墙或登录权限。
- 将未经验证的网页文本直接转为结构化行情。
- 在日志中记录 API Key、Cookie 或个人信息。

每次智能体运行必须保留可重放的工具调用记录。模型输出和工具输出需要分开保存。
