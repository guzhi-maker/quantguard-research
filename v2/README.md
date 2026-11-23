# v2 交付说明（验收用）

本目录是这一轮全部工作的归档：**除原始数据采集部分外，所有新增内容都在这里**。
采集器、落盘、manifest、旧网页工作台均未改动；本目录只读 `data/raw/` 下的快照。

---

## 1. 目录

```text
v2/
├── cli.py                     # 命令行入口（run / backtest / experiment / grid / ladder）
├── conftest.py                # pytest 路径引导
├── research/                  # 建模与分析代码（14 个模块）
├── tests/test_research_v2.py  # 27 项验收测试
├── docs/
│   ├── usage.md               # 使用说明（简明版）
│   ├── walkthrough.md         # 完整示范（1000 日快照，旧版）
│   ├── walkthrough-v2.md      # 完整示范（2500 日 + 真实行业，当前版）
│   ├── summary.md             # 模型与分析总结
│   ├── model-report.md        # 模型构建思路报告（含消融对照）
│   └── workflow.md            # 设计与迭代记录（含验收口径）
└── artifacts/                 # 复跑结果 JSON（供核对，非代码）
```

---

## 2. 三步验收

在仓库根目录执行：

```powershell
# 1) 测试：34 项，约 2 分钟
python -m pytest v2/tests -q

# 2) 单次研究：空仓，五档阶梯 + 推荐组合
python v2/cli.py run --risk 5.5 --horizon 2

# 3) 组合层回测：净值为准
python v2/cli.py backtest --horizon 2 --risks 1,5.5,10 --modes optimize,index
```

第 2 步预期看到（`RAW-20261119-124359-05EA7E` 快照）：

- 期限 3 = 20 日标签，模型 `qg-v2/lightgbm/20d/12f`
- 校准后覆盖率约 10%（P10/P90 各 10% 左右）
- Rank IC 约 0.06，独立样本 43
- 五档 CVaR 单调上升，波动目标 8%/12%/16%/22%/28%

`--holdings "002242:0.30,601857:0.20"` 可验证诊断与建议；`--report` 可验证 AI 简报
（无密钥时走模板回退）。

---

## 三份报告

| 文件 | 内容 | 适合 |
| --- | --- | --- |
| [01-使用说明与模拟报告](docs/reports/01-usage-and-simulation.md) | 安装、采集、参数、一次完整模拟与逐项解读 | 验收者、第一次使用 |
| [02-构建思路与过程报告](docs/reports/02-construction-process.md) | 数据扩容、特征与模型取舍、消融、负面结果 | 老师、答辩 |
| [03-PPT 呈现建议](docs/reports/03-ppt-plan.md) | 14 页结构、讲稿节奏、预置问答、视觉建议 | 做课堂展示 |
| [04-空仓投资者使用报告](docs/reports/04-investor-journey.md) | 以空仓用户视角走完整流程，含 AI 简报与真实感受 | 站在用户侧审视产品 |

## 网页版（组合研究页）

命令行之外，v2 接入了原数据工作台的同一个服务，但**没有改动原有页面逻辑**：
新增一个独立页面与三个接口，原 `index.html` 只加了一个导航链接。

```powershell
python run_server.py --port 8789
```

打开 `http://127.0.0.1:8789/v2.html`：

| 区域 | 内容 |
| --- | --- |
| 参数 | 快照、风险档位（1–10）、期限（1–3）、本金、种子数、模拟持仓 |
| 概览 | 快照规模、模型版本、Rank IC、Top 超额、校准覆盖率、独立样本 |
| 验收 | 8 项检查（通过 / 失败 / 跳过三态） |
| 风险阶梯 | 五档 CVaR、预期收益、波动、仓位、持仓数、现金 |
| 推荐组合 | 权重与整手数（买不起的会标注） |
| 持仓诊断 | 六维卡片，红 / 黄 / 绿 |
| 加减仓建议 | 动作、变动、类型、改善 / 成本、原因 |
| 证据与产出 | 证据包 token、AI 简报、下载完整 JSON |

接口挂在原服务上：

```text
GET  /api/v2/snapshots     快照列表
POST /api/v2/jobs          创建分析任务（异步）
GET  /api/v2/jobs/<id>     查询状态与结果
```

任务串行执行；2500 日 × 997 只的默认配置约 8 分钟，240 只的小快照约 25 秒，
页面每 2.5 秒轮询一次进度。

## 3. 相对原始仓库的改动

| 类型 | 内容 |
| --- | --- |
| 新增 | `v2/` 整个目录（代码、测试、三份文档、结果 JSON） |
| 修改 | 根 `README.md`：增加 v2 入口与目录说明 |
| 修改 | `requirements.txt`：新增 `lightgbm>=4.0`（可选，缺失时自动退回 sklearn） |
| 未改动 | `data_agent/`、`webapp/`、`tools/` 其余脚本、`data/`、`tests/` 其余测试、`docs/` 其余文档 |

没有修改任何采集逻辑；新增代码只读取 `data/raw/<run_id>/` 下的 CSV。

---

## 4. 结果文件（artifacts/）

| 文件 | 内容 |
| --- | --- |
| `recalib_1000d.json` | 1000 日快照三档信号与覆盖率 |
| `calib_compare_1000d.json` | 静态 CQR vs 自适应校准对比 |
| `bt_1000d_h3.json` | 1000 日快照 20 日档组合回测 |
| `exp_new_h1.json` / `exp_new_h3.json` / `exp_new_h5.json` | 600 日快照变体对比 |
| `bt_h1_600.json` / `bt_h5_600.json` / `bt_final_h3.json` | 600 日快照组合回测 |
| `final_h3.json` / `h5_before.json` | 单次运行的完整输出（含证据包与验收项） |

JSON 是运行产物，不是代码；删除不影响任何功能。

---

## 5. 已知限制（验收时的预期）

- 60 日档只有 15 个独立窗口，结论仍不稳健
- 5 日档成本高于信号强度，命令会主动给出警告
- 股票池 300 只、偏成长，基准是等权池，不能外推到全市场
- 回测未建模盘中成交、涨跌停排队、冲击成本随规模变化
- 仓库自带 `.venv` 损坏（缺 `pyvenv.cfg`），请用系统 Python
- 未接入网页；如需接入要改 `data_agent/server.py`（本轮未动）

---

## 6. 设计文档去哪了

原 `docs/quant-research-v2-*.md` 已整体移入本目录并改名为
`usage.md` / `summary.md` / `workflow.md`；根 README 的链接已同步更新。
