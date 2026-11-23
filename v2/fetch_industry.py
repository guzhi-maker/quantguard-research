"""补充真实行业分类：从 baostock 拉取证监会行业分类，写成映射表。

输出 `v2/data/industry_map.csv`，列为 symbol, industry, industry_code, updated_at。
分析代码在加载快照后按 symbol 覆盖 industry 字段（快照本身保持不可变）。

用法：
  python v2/fetch_industry.py
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = Path(__file__).resolve().parent / "data" / "industry_map.csv"


def main() -> int:
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        print(f"baostock 登录失败: {login.error_msg}", file=sys.stderr)
        return 1
    try:
        result = bs.query_stock_industry()
        if result.error_code != "0":
            print(f"查询失败: {result.error_msg}", file=sys.stderr)
            return 1
        rows: dict[str, dict] = {}
        while result.next():
            parts = result.get_row_data()
            if len(parts) < 5:
                continue
            update_date, code, name, industry, industry_class = parts[:5]
            symbol = code.split(".")[-1]
            if not symbol.isdigit() or len(symbol) != 6:
                continue
            label = (industry or "").strip() or (industry_class or "").strip() or "未分类"
            rows[symbol] = {
                "symbol": symbol,
                "industry": label,
                "industry_code": (industry_class or "").strip(),
                "updated_at": update_date or datetime.now().strftime("%Y-%m-%d"),
            }
    finally:
        bs.logout()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "industry", "industry_code", "updated_at"])
        writer.writeheader()
        for symbol in sorted(rows):
            writer.writerow(rows[symbol])
    labels = {row["industry"] for row in rows.values()}
    print(f"写入 {OUT}：{len(rows)} 只股票，{len(labels)} 个行业")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
