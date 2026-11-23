# v2 使用说明（简明版）

三件事：装依赖、跑命令、看结果。参数细节和设计理由见 [workflow.md](workflow.md)；
想看一次完整运行长什么样，见 [walkthrough-v2.md](walkthrough-v2.md)（当前版），
旧版对照见 [walkthrough.md](walkthrough.md)。

---

## 一、装依赖

```powershell
cd quantguard-research
python -m pip install -r requirements.txt
```

最少需要 `pandas numpy scipy scikit-learn`；有 `lightgbm` 会用更好的模型，没有也能跑。

---

## 二、准备数据

```powershell
python data_agent/collect_dataset.py --universe-size 300 --history-days 1000 --no-quotes
```

跑完会打印一个快照编号（`RAW-...`），数据在 `data/raw/<编号>/`。
已有快照就不用再采，程序默认用最新的一个。

---

## 三、两条命令

**空仓：看该买什么**

```powershell
python v2/cli.py run --risk 5.5 --horizon 2
```

**已持股：看手里怎么样、该动哪只**

```powershell
python v2/cli.py run --risk 7 --horizon 2 --holdings "002242:0.30,601857:0.20"
```

（持仓写成 `代码:权重`，逗号分隔。加 `--report` 会生成中文简报，需要 `DEEPSEEK_API_KEY`。）

---

## 四、两个参数怎么填

**`--risk` 风险 1–10**：越大越激进。

| 值 | 感觉 | 波动目标 | 持仓数 |
| --- | --- | --- | --- |
| 1 | 保守 | 8% | 50 只 |
| 5.5 | 平衡 | 16% | 15 只 |
| 10 | 激进 | 28% | 6 只 |

**`--horizon` 期限 1–3**：持有期（已从 5 档收敛到 3 档，去掉没有独立结论的 10/40 日）。

| 值 | 标签 | 调仓 | 独立窗口（2500 日快照） |
| --- | --- | --- | --- |
| 1 | 5 日 | 每周 | 约 475 |
| 2 | 20 日 | 每月 | 约 133 |
| 3 | 60 日 | 每季 | 约 43 |

**建议默认 `--risk 5.5 --horizon 2`。**

还有三个可选参数：`--capital`（本金，默认 100 万）、`--positions`（推荐持仓数，例如 15 只；0=随风险档位：保守 50 / 平衡 15 / 激进 6）、`--seeds`（1/3/5）。 5 日档成本高，60 日档样本偏少。

---

## 五、结果怎么看

```text
风险阶梯：每行一档，看 CVaR 和波动是否随档位变大
推荐组合：推荐哪些股票、建议权重；整手数在执行参考里，买不买由你决定
六维诊断：只在你给了 --holdings 时出现，红/黄/绿三档
建议清单：安全类必须处理；优化型每周最多 5 条
验收项：一堆 true 表示通过，false 会有中文提示解释原因
```

同样的结果也能在网页看：`python run_server.py --port 8789` 后打开
`http://127.0.0.1:8789/v2.html`，选快照和参数点“开始分析”即可。

---

## 六、想回测就多一条命令

```powershell
python v2/cli.py backtest --horizon 2 --risks 1,5.5,10 --modes optimize,index
```

输出每档的年化收益、波动、夏普、回撤、换手、成本和超额。

---

## 七、常见问题

| 现象 | 怎么办 |
| --- | --- |
| 报错"样本过短" | 快照历史不够，换更大的期限或补数据 |
| 提示"IC 不足 0.02" | 正常保护，已自动改成等权分散组合 |
| 现金占一半 | 风险档太低或股价太高，属正常；想满仓就把 `--risk` 调高 |
| 某只股票 0 手 | 一手都买不起，资金和股价不匹配 |
| 报告是模板 | 没配 `DEEPSEEK_API_KEY`，或接口失败 |

---

## 八、想改代码看哪里

| 想改 | 文件 |
| --- | --- |
| 特征 | `v2/research/features.py` |
| 标签 | `v2/research/labels.py` |
| 模型与区间校准 | `v2/research/primary.py` |
| 组合权重 | `v2/research/portfolio.py` |
| 诊断与建议 | `v2/research/diagnosis.py`、`advice.py` |
| 整条流程 | `v2/research/pipeline.py` |

改完跑一次 `python -m pytest v2/tests -q` 确认没弄坏。
