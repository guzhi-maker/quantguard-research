# QuantGuard v2 机器学习实现契约

**补充：** `2026-11-04-quant-ml-research-design-v2.md`  
**状态：** 待用户审阅

本文件钉死机器学习管线的日期、字段和确定性规则，避免实现时产生数据泄漏或指标口径不一致。

## 1. 日期与样本

- 历史面板按交易日升序排列，不随机打乱。
- 至少 120 个交易日用于计算 `momentum_120`、`volatility_60` 和均线特征。
- 标签期限记为 `h`，允许 `5`、`10`、`20`，默认 `20`。
- 只有 `close[t + h]` 已存在的信号日才能进入有标签样本集。
- `signal_dates` 是满足“特征完整且标签已实现”的升序交易日集合。
- `dev_end = floor(len(signal_dates) * 0.6)`。
- 开发期：`signal_dates[:dev_end]`。
- 留出期：`signal_dates[dev_end:]`。
- 两个集合不得重叠，留出期不得进入模型或超参数选择。

## 2. 开发期验证

- 开发期前 50% 的日期用于初始训练。
- 开发期后 50% 的日期按时间顺序切成 3 个连续验证折。
- 每折训练集必须只包含该折验证开始前、且标签窗口已经结束的样本。
- 预处理、缺失填充、行业编码和标准化器只在每折训练集拟合。
- 超参数只在开发期验证折比较，留出期开始前冻结。
- 如果开发期不足以形成 2 个有效验证折，返回“样本不足”，不退回随机划分。

## 3. 留出期滚动回测

- 留出期从第一个未参与调参的日期开始。
- 每 20 个交易日扩展训练一次。
- 预测日 `t` 的训练样本必须满足 `sample_date + h <= t`。
- 不得使用 `t` 之后的价格、标签、标准化参数或行业编码。
- 同一模型类型和超参数在留出期保持不变。
- 每次训练和预测都记录 `train_start`、`train_end`、`label_cutoff`、`signal_date`、`train_rows` 和 `prediction_rows`。
- 信号在 `t` 收盘生成，目标权重从 `t + 1` 生效。

## 4. 特征与标签字段

连续特征：

```text
momentum_5
momentum_20
momentum_60
momentum_120
volatility_10
volatility_20
volatility_60
price_to_ma20
price_to_ma60
volume_ratio_5_20
amount_ratio_5_20
liquidity_log_20
amihud_20
max_drawdown_60
market_return_20
market_breadth
cross_section_dispersion
```

标签：

```text
future_return_h = close[t + h] / close[t] - 1
future_excess_h = future_return_h - cross_section_mean(future_return_h)
```

- 特征中不能出现 `future_*`、`label_*` 或任何使用 `t + 1` 之后价格计算的字段。
- `future_excess_h` 只用于训练目标、验证指标和评估，不得进入模型特征。
- 极端收益只做显式记录，不因标签值异常而静默删除股票。
- 缺失率、有效样本数和被排除股票必须进入 `data_quality`。

## 5. 模型候选

- `factor`：固定多因子综合分基线，不使用监督标签。
- `ridge`：Ridge 回归。
- `random_forest`：随机森林回归。
- `gradient_boosting`：`HistGradientBoostingRegressor`。

固定随机种子 `20261104`。监督模型使用同一特征列、同一标签、同一日期切分和同一组合约束。

## 6. 指标口径

预测指标：

- `mae`
- `rmse`
- `r2_oos`
- `rank_ic_mean`
- `rank_ic_std`
- `icir`
- `ic_win_rate`
- `top_bucket_excess`
- `top_bucket_capture`

回测指标：

- `gross_annual_return`
- `annual_cost_drag`
- `net_annual_return`
- `benchmark_annual_return`
- `net_excess_annual_return`
- `max_drawdown`
- `annual_volatility`
- `sharpe_ratio`
- `info_ratio`
- `annual_turnover`

`annual_return` 作为 `net_annual_return` 的兼容字段。所有成本明细之和必须等于总成本，允许因显示四舍五入产生不超过 0.01 元的误差。

## 7. 模型选择

1. 在开发期三个连续验证折上计算模型指标。
2. 过滤掉训练失败或预测非有限值的候选。
3. 在 R² 不明显为负的候选中，选择平均 Rank IC 最高者。
4. 若所有监督模型都不优于因子基线，保留因子基线并明确写出“当前样本没有证明机器学习模型有效”。
5. 留出期只评估冻结后的选择，不允许根据留出期结果重选。

## 8. 资金与组合

- 研究资金是请求参数，不写死为 5 万元。
- 默认值仅影响首次界面，不影响模型训练。
- 股票数必须为 100 的整数倍。
- 单票预算不足一手时保留现金。
- 费用按每只股票、每个买卖方向分别计算。
- 组合回测必须使用滚动训练生成的样本外预测，不能使用全样本拟合分数回填历史。

## 9. 研究导出

Markdown 和 JSON 必须包含：

- `data_mode`
- 行情时间
- 演示数据版本或实时数据来源
- 生成时间
- 实验编号
- 完整参数
- 数据质量摘要
- 特征目录
- 标签公式
- 日期切分
- 防泄漏规则
- 模型卡片和验证指标
- 留出期指标
- 成本明细
- 目标持仓
- 风险与局限声明

演示数据导出必须出现“演示数据”，不得被解读为实时行情或收益承诺。

## 10. 最小验收

- 用人工构造的未来价格跳变测试特征日期边界。
- 用人工构造的标签截止日测试训练集过滤。
- 用固定演示数据测试结果可重复。
- 用空数据、样本不足、单模型失败和外部行情失败测试错误路径。
- 用 390px、680px、1280px DOM 尺寸测试无整页横向溢出。
- 不查看截图，只使用 DOM、接口、文本和尺寸检查。
