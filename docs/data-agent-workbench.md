# QuantGuard 数据采集工作台说明

## 1. 当前交付边界

成员 A 负责的闭环是：

```text
任务输入
-> DeepSeek 规划
-> 数据源/证券工具调用
-> 公开行情接口
-> 原始 CSV
-> manifest 与 SHA256
-> 数据浏览、导出和交接
```

不包含特征工程、机器学习、组合优化和研究报告。现有研究模块保留在代码库中，供后续成员继续开发。

## 2. 应用结构

```text
data_agent/
├── catalog.py          # 数据源注册表
├── agent.py            # DeepSeek function calling 与工具循环
├── service.py          # 参数校验、真实采集和异步任务
├── store.py            # 原始 CSV、manifest、哈希与分页读取
├── server.py           # 本地 HTTP API
└── static/             # 数据采集工作台前端
```

数据访问边界：

```text
DeepSeek
-> data_agent/agent.py 注册的工具
-> data_agent/service.py 采集编排
-> webapp/market_client.py 公开行情连接器
```

DeepSeek 不能直接访问价格数据，也不能直接写 CSV。它只能选择工具和参数；价格、日期、来源、行数均由连接器返回，并由采集服务写入磁盘。

## 3. DeepSeek 工具

| 工具 | 作用 | 数据来源 |
| --- | --- | --- |
| `list_data_sources` | 获取数据源、字段、复权口径和限制 | 本地注册表 |
| `probe_data_sources` | 发送真实请求检查接口状态和延迟 | 东方财富、腾讯、新浪 |
| `search_security` | 按代码或名称搜索 A 股证券 | 东方财富搜索接口 |
| `collect_market_data` | 采集股票池、实时快照和历史日线 | 公开行情接口 |

智能体最多执行 6 个规划步骤，单次任务只允许生成一份快照。工具调用轨迹会保存到 manifest。

安全约束：

1. 所有行情数值来自工具返回值，LLM 不自行计算或补全。
2. 工具集合固定，智能体不能执行任意 URL 或任意代码。
3. API Key 只从本地 `.env` 或进程环境读取，不进入提示词和 API 响应。
4. 接口失败保留股票代码、失败阶段和错误，不静默替换成演示数据。
5. 原始数据只追加，不覆盖历史 `run_id`。

## 4. 原始数据契约

日线字段：

```text
date, symbol, name, industry, open, high, low, close,
volume, amount, amplitude, change_pct, change, turnover,
source, adjustment, fetched_at
```

行情快照字段：

```text
symbol, name, industry, price, open, high, low, prev_close,
change, change_pct, turnover_rate, pe, pb, volume, amount,
market_cap, float_market_cap, source, updated_at, fetched_at
```

股票池字段：

```text
symbol, name, industry, price, change_pct, volume, amount,
turnover_rate, pe, market_cap, source, fetched_at
```

复权标记：

- `qfq`：接口明确返回前复权数据。
- `unverified`：接口复权口径无法稳定确认，必须由数据治理阶段复核。

成交额字段必须结合 `source` 使用。腾讯和新浪的成交额可能由成交量与价格估算。

## 5. manifest 内容

每次采集生成：

```text
data/raw/<run_id>/market_daily.csv
data/raw/<run_id>/market_quotes.csv
data/raw/<run_id>/universe.csv
data/manifests/<run_id>.json
```

manifest 记录：

- `run_id`、创建时间、任务状态。
- 自然语言任务、股票池数量、历史天数和指定代码。
- 实际股票数、日线行数、行情行数和失败数。
- 行情起止日期、实际来源和复权口径。
- 文件字段、行数、字节数和 SHA256。
- 重复主键、非法价格、缺失收盘价/成交量/成交额。
- 数据来源血缘和已知限制。
- DeepSeek 模型、最终文本和工具调用轨迹。

## 6. 页面验收

主导航：

1. 采集工作台。
2. 数据源。
3. 采集记录。
4. 原始数据。

关键验收点：

- 未配置 DeepSeek 时，页面明确显示“未配置”，固定流程仍可采集。
- 数据源探测显示实际延迟；失败时显示异常，不伪造在线。
- 采集任务显示进度、阶段、事件和最终 `run_id`。
- 最新摘要显示股票数、行数、日期范围和失败数。
- 采集记录可打开并重新加载 manifest。
- 原始数据可切换日线、行情快照和股票池。
- 支持字段筛选、分页和完整 CSV 导出。
- 原始数据表只在自身容器内横向滚动。
- `390px`、`680px`、`1280px` 页面无整页横向溢出。

## 7. 三分钟演示脚本

### 0:00-0:30 研究问题

“本阶段不是预测股票收益，而是解决量化研究的第一道工程问题：如何从公开源稳定获得可追溯的原始行情。”

### 0:30-1:10 数据源与工具

打开“数据源”页，展示三源注册表、能力、复权口径和成交额限制，现场点击探测。

### 1:10-2:10 智能体采集

在“采集工作台”输入自然语言任务，展示 DeepSeek 工具轨迹。强调 DeepSeek 只负责工具选择，价格和行数由连接器返回。

### 2:10-2:40 原始数据

打开“原始数据”，切换日线和股票池，搜索一只股票，展示来源和抓取时间，并导出完整 CSV。

### 2:40-3:00 交接

打开 manifest，展示 SHA256、失败记录、复权标记和来源血缘。说明 B 只能基于新 `run_id` 建立治理版本，不修改原始快照。

## 8. 答辩问题

### 为什么需要智能体？

公开数据获取涉及数据源选择、接口探测、股票搜索和参数组织。智能体把这些步骤变成可观察的工具调用，但所有数值仍由确定性代码产生。

### 如何避免大模型幻觉？

数值不经过 LLM。LLM 只能调用固定工具，采集结果来自行情连接器；行数、来源和失败项从实际返回结果生成，并写入 manifest。

### 为什么保存原始数据而不是直接建模？

原始快照是可审计基础。如果直接在原始数据上清洗和覆盖，后续无法判断模型问题来自数据、清洗还是模型。原始层只追加，治理层另建版本。

### 如何证明数据来自真实网络接口？

页面显示接口探测延迟、每行 `source`、`fetched_at` 和最新行情日期；manifest 保存文件 SHA256；CSV 可以逐行复核。

### 为什么成交额要特别标注？

腾讯和新浪的回退接口可能在缺少成交额时使用成交量和价格估算。研究时必须区分交易所披露值与估算值。

### 为什么股票池仍有偏差？

当前股票池是抓取时点的成交额快照，不是历史时点成分股。本项目已明确该限制，不会把它伪装成无幸存者偏差的学术样本。

### 接口失败怎么办？

连接器按东方财富、腾讯、新浪顺序回退；单只股票失败会记录在 manifest 的 `failures` 中。页面不会用演示数据替换真实行情。

### 为什么需要 SHA256？

它用于确认交接文件没有被静默修改。B 若需要清洗，应生成新的治理版本和自己的哈希，而不是覆盖 A 的原始快照。

## 9. 交接检查

```text
[ ] 本次 run_id 已记录
[ ] manifest 状态为 complete
[ ] 日线 CSV 行数与 manifest 一致
[ ] daily SHA256 与文件一致
[ ] date_min/date_max 符合请求窗口
[ ] 股票数与 universe.csv 一致
[ ] source 与 adjustment 无空白
[ ] failures 已逐项说明
[ ] B 已复制数据到治理目录，未修改 data/raw
```
