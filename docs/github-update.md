# 如何把这份代码更新到 GitHub

## 1. 这次更新包含什么

| 模块 | 内容 | 说明 |
| --- | --- | --- |
| `v2/research/` | 研究流水线 15 个模块 | 新增 |
| `v2/cli.py`、`collect_long.py`、`fetch_industry.py` | 命令行、长历史采集、行业映射 | 新增 |
| `v2/tests/` | 36 项测试 | 新增 |
| `v2/docs/` | 使用说明、构建过程、四份报告 | 新增 |
| `v2/data/industry_map.csv` | 行业映射表 | 新增 |
| `data_agent/v2_api.py` | Web 接口 | 新增 |
| `data_agent/static/v2.html|css|js` | 组合研究页面 | 新增 |
| `data_agent/server.py` | 加了 4 个路由 + 静态白名单 | **修改**，未动原有接口 |
| `data_agent/service.py` | 新增 fundamentals 任务模式 | **修改**，只加一个分支 |
| `data_agent/static/index.html` | 因子区新增“采集基本面”按钮 | **修改**，一行 |
| `data_agent/static/app.js` | 按钮显隐、点击与完成回调 | **修改**，约 30 行 |
| `data_agent/static/index.html` | 加了一个导航链接 | **修改**，一行 |
| `README.md`、`requirements.txt` | v2 章节与依赖 | 修改 |

数据采集部分（`data_agent/` 的原有逻辑、`webapp/`、`tools/`）**没有改动**。

## 2. 没有包含什么（已放进 `.gitignore`）

- `data/raw/`：行情快照，约 1.6 GB
- `data/manifests/*.json`：指向上面的快照
- `.env`：含 API 密钥
- `v2/artifacts/*`：运行产物（只保留 `samples/` 三个 JSON）
- `pydeps/`、`wheels/`、`tmp/`、`pip-*`：安装残留
- `__pycache__/`、`.pytest_cache/`、`*.log`

## 3. 两种更新方式

### 方式 A：覆盖式（最快）

```powershell
# 1. 克隆协作者的仓库
git clone https://github.com/<owner>/<repo>.git D:\quantguard-git
cd D:\quantguard-git

# 2. 用本包内容覆盖（保留 .git）
robocopy D:\deepseek-workspace\quantguard-v2-clean D:\quantguard-git /E
# 或先清空除 .git 外的内容再复制

# 3. 检查
git status
git diff --stat

# 4. 提交
git checkout -b model-v2
git add -A
git commit -m "feat: v2 研究流水线与组合研究页面"
git push -u origin model-v2
```

### 方式 B：只提 PR（最稳）

只复制新增目录，不改协作者的文件：

```powershell
git checkout -b model-v2
Copy-Item -Recurse D:\deepseek-workspace\quantguard-v2-clean\v2 .\v2
git add v2
git commit -m "feat(v2): 分位数选股流水线、风险阶梯与持仓诊断"
git push -u origin model-v2
```

网页部分（`server.py`、`index.html`）等对方同意后，再单独提一个 PR。

## 4. 提交前自检

```powershell
python -m pytest v2/tests -q        # 期望 35 passed, 1 skipped（无快照时跳过端到端）
git grep -i "sk-" --cached          # 应为空
git ls-files | Select-String "data/raw|\.env|artifacts"   # 应为空
```

## 5. 运行

```powershell
python -m pip install -r requirements.txt
python v2/collect_long.py --days 2500 --workers 6   # 采集（约 6 分钟）
python v2/fetch_industry.py                          # 行业映射（一次性）
python v2/cli.py run --risk 5.5 --horizon 2          # 分析
python run_server.py --port 8789                     # 网页：/v2.html
```
