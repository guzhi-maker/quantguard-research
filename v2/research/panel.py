"""原始快照读取与面板构建（只读，不改动采集链路）。

本模块把 data/raw/<run_id>/ 下的日线 CSV 转成研究用的宽面板，并给出
A 股特有的可成交性掩码：一字涨停、封板、停牌、上市不足、流动性不足。
采集与落盘逻辑仍在 data_agent/ 内，本模块不写任何数据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import DATA_DIR

RAW_DIR = DATA_DIR / "raw"
MANIFEST_DIR = DATA_DIR / "manifests"

MIN_LISTED_DAYS = 20
MIN_ADV = 5_000_000.0  # 20 日均成交额下限（元）
LIMIT_TOLERANCE = 0.2  # 涨跌幅判定容差（百分点）


@dataclass
class Panels:
    """统一面板：所有宽表的 index=交易日，columns=股票代码。"""

    close: pd.DataFrame
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame
    amount: pd.DataFrame
    change_pct: pd.DataFrame
    turnover: pd.DataFrame
    meta: pd.DataFrame
    run_id: str
    universe: pd.DataFrame | None = None

    @property
    def dates(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.close.index)

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    def industry_map(self) -> pd.Series:
        return self.meta.reindex(self.close.columns)["industry"].fillna("未分类")


def list_runs(raw_dir: Path | None = None) -> list[str]:
    directory = Path(raw_dir or RAW_DIR)
    if not directory.exists():
        return []
    return sorted(
        (path.name for path in directory.iterdir() if (path / "market_daily.csv").exists())
    )


def latest_run(raw_dir: Path | None = None) -> str:
    runs = list_runs(raw_dir)
    if not runs:
        raise FileNotFoundError("未找到任何原始快照，请先运行采集")
    return runs[-1]


def _read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"symbol": str})
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"])
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    return frame


def load_snapshot(run_id: str | None = None, raw_dir: Path | None = None) -> Panels:
    """读取一个原始快照，返回研究面板。"""
    run = run_id or latest_run(raw_dir)
    directory = Path(raw_dir or RAW_DIR) / run
    daily = _read_csv_safe(directory / "market_daily.csv")
    if daily.empty:
        raise FileNotFoundError(f"快照 {run} 缺少 market_daily.csv")
    universe = _read_csv_safe(directory / "universe.csv")

    daily = daily.sort_values(["date", "symbol"]).reset_index(drop=True)
    numeric = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "change_pct",
        "turnover",
        "amplitude",
    ]
    for column in numeric:
        if column in daily.columns:
            daily[column] = pd.to_numeric(daily[column], errors="coerce")

    def wide(column: str) -> pd.DataFrame:
        if column not in daily.columns:
            return pd.DataFrame(index=sorted(daily["date"].unique()))
        return daily.pivot_table(
            index="date", columns="symbol", values=column, aggfunc="last"
        )

    close = wide("close").sort_index()
    meta = (
        daily.groupby("symbol", observed=True)
        .agg(name=("name", "last"), industry=("industry", "last"))
        .reindex(close.columns)
    )
    meta["name"] = meta["name"].fillna(meta.index.to_series())
    meta["industry"] = meta["industry"].fillna("未分类")

    if not universe.empty:
        cap = universe.set_index(universe["symbol"].astype(str).str.zfill(6))
        cap = cap.reindex(close.columns)
        for column in ("market_cap", "pe", "turnover_rate"):
            if column in cap.columns:
                meta[column] = pd.to_numeric(cap[column], errors="coerce")

    return Panels(
        close=close,
        open=wide("open").reindex(index=close.index, columns=close.columns),
        high=wide("high").reindex(index=close.index, columns=close.columns),
        low=wide("low").reindex(index=close.index, columns=close.columns),
        volume=wide("volume").reindex(index=close.index, columns=close.columns),
        amount=wide("amount").reindex(index=close.index, columns=close.columns),
        change_pct=wide("change_pct").reindex(index=close.index, columns=close.columns),
        turnover=wide("turnover").reindex(index=close.index, columns=close.columns),
        meta=meta,
        run_id=run,
        universe=universe if not universe.empty else None,
    )


def limit_pct(symbol: str, name: str = "") -> float:
    """按板块返回涨跌停幅度（小数）。ST 按 5% 处理。"""
    code = str(symbol).zfill(6)
    label = str(name or "")
    if "ST" in label.upper() or "退" in label:
        return 0.05
    if code.startswith(("688", "689")):
        return 0.20
    if code.startswith(("300", "301")):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30
    return 0.10


def _limit_by_row(panels: Panels) -> pd.DataFrame:
    limits = pd.Series(
        {
            symbol: limit_pct(symbol, panels.meta.loc[symbol, "name"] if symbol in panels.meta.index else "")
            for symbol in panels.symbols
        }
    )
    return pd.DataFrame(np.tile(limits.to_numpy(), (len(panels.dates), 1)), index=panels.dates, columns=panels.symbols)


def board_group(symbol: str) -> str:
    """按代码前缀判断板块，用于行业分类缺失时的代理分组。"""
    code = str(symbol).zfill(6)
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("8", "4", "920")):
        return "北交所"
    return "主板"


def industry_proxy(panels: Panels) -> pd.Series:
    """行业分组：真实行业可用时用行业，否则退化为板块代理。

    代理分组只用于截面中性化，报告里必须标注为"板块代理"。
    """
    industry = panels.industry_map()
    if industry.nunique() >= 2:
        return industry
    return pd.Series(
        {symbol: board_group(symbol) for symbol in panels.symbols}, dtype=object
    ).reindex(panels.symbols)


def tradability(panels: Panels, min_history: int = MIN_LISTED_DAYS, min_adv: float = MIN_ADV) -> pd.DataFrame:
    """可买入掩码：True 表示当日可以建仓。

    判定为一字涨停、收盘封涨停、停牌、历史过短、流动性不足时不可买入。
    """
    limits = _limit_by_row(panels)
    change = panels.change_pct.fillna(0.0) / 100.0
    high = panels.high
    low = panels.low
    volume = panels.volume.fillna(0.0)

    one_word_up = (high - low).abs() < 1e-9
    sealed_up = change >= (limits - LIMIT_TOLERANCE / 100.0)
    limit_up = one_word_up & sealed_up
    halted = volume <= 0
    history_days = panels.close.notna().cumsum()
    short_history = history_days < min_history
    adv = panels.amount.rolling(20, min_periods=5).mean()
    illiquid = adv < min_adv

    tradable = ~(limit_up | halted | short_history | illiquid)
    return tradable.fillna(False)


def sellable(panels: Panels) -> pd.DataFrame:
    """可卖出掩码：跌停、停牌不可卖。"""
    limits = _limit_by_row(panels)
    change = panels.change_pct.fillna(0.0) / 100.0
    sealed_down = change <= -(limits - LIMIT_TOLERANCE / 100.0)
    halted = panels.volume.fillna(0.0) <= 0
    return ~(sealed_down | halted)


def reject_rate(tradable: pd.DataFrame, candidates: pd.DataFrame | None = None) -> float:
    """信号拒单率：候选池中因不可成交而放弃的比例（按当日横截面均值）。"""
    universe = candidates if candidates is not None else tradable.notna()
    valid = universe.fillna(False).to_numpy().sum()
    if valid == 0:
        return 0.0
    accepted = (tradable & universe.fillna(False)).to_numpy().sum()
    return float(1.0 - accepted / valid)


def manifest_for(run_id: str) -> dict:
    path = MANIFEST_DIR / f"{run_id}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def panel_summary(panels: Panels) -> dict:
    return {
        "run_id": panels.run_id,
        "symbols": len(panels.symbols),
        "dates": int(len(panels.dates)),
        "start": str(panels.dates.min().date()) if len(panels.dates) else None,
        "end": str(panels.dates.max().date()) if len(panels.dates) else None,
        "rows": int(panels.close.notna().to_numpy().sum()),
        "industries": int(panels.industry_map().nunique()),
    }


def equal_weight_return(panels: Panels) -> pd.Series:
    returns = panels.close.pct_change(fill_method=None)
    return returns.mean(axis=1).fillna(0.0)
