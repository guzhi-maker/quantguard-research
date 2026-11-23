# 运行样本

这里放了三次真实运行的完整 JSON 输出，供 reviewer 直接查看，不需要重跑。

| 文件 | 场景 | 参数 | 对应文档 |
| --- | --- | --- | --- |
| `sim15.json` | 2500 日快照、平衡档、空仓 | risk 5.5 / horizon 2（20 日）/ 3 种子 | `docs/reports/01-usage-and-simulation.md` 第 8 节 |
| `user_flow.json` | 空仓投资者完整流程（含 AI 简报） | risk 5.5 / horizon 2 / 空仓 / `--report` | `docs/reports/04-investor-journey.md` |
| `long_bt_rank_small.json` | 10 年组合层回测（排序模式） | horizon 2 / refit 250 日 / 窗口 400 日 | `docs/reports/02-construction-process.md` 10.2 |

说明：

- 数据快照本身不在仓库里（`data/raw/` 约 1.6 GB，已被 `.gitignore` 忽略），
  需要时用 `python v2/collect_long.py --days 2500` 重新采集，约 6 分钟。
- 完整运行一次 20 日档约 13 分钟（3 种子、997 只、2500 日）。
- JSON 里包含 `panel`、`metrics`、`ladder`、`selected`、`diagnosis`、`advice`、`evidence`、`acceptance` 等字段，
  字段含义见 `docs/reports/01-usage-and-simulation.md` 第 7–10 节。
