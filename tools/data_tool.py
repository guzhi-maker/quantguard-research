"""行情数据工具。

设计目标：
- 统一输出标准日线 DataFrame（MultiIndex: date x symbol）。
- 默认前复权价格，规避除权除息跳空。
- 指数成分股强调“历史时点可得”，默认禁止用当前成分股回测历史。
- AkShare 不可用时自动回退到确定性合成行情，保证项目离线可跑。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

from config.settings import CACHE_DIR, CONSTITUENTS_DIR, DATA_DIR

INDEX_CODES = {
    "000001": "上证指数",
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "399001": "深证成指",
    "399006": "创业板指",
}

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume", "amount"]
STOCK_NAME_KEYWORDS = ("ST", "退")

DATE_LIKE = Union[str, dt.date, pd.Timestamp]


class DataSourceError(RuntimeError):
    """真实数据源不可用。"""


class DataUnavailableError(RuntimeError):
    """请求的数据无法在时点可得性约束下提供。"""


def _normalize_dates(start_date: DATE_LIKE, end_date: DATE_LIKE) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if start > end:
        raise ValueError(f"start_date {start.date()} 晚于 end_date {end.date()}")
    return start, end


def _normalize_pool(pool: Union[str, Iterable[str], pd.DataFrame]) -> List[str]:
    if isinstance(pool, str):
        return [pool]
    if isinstance(pool, pd.DataFrame):
        if "symbol" in pool.columns:
            return [str(s) for s in pool["symbol"].unique()]
        if pool.index.names and "symbol" in pool.index.names:
            return [str(s) for s in pool.index.get_level_values("symbol").unique()]
        if len(pool.columns) == 1:
            return [str(s) for s in pool.iloc[:, 0].unique()]
        raise ValueError("DataFrame 股票池需包含 symbol 列")
    symbols = [str(s) for s in pool]
    if not symbols:
        raise ValueError("股票池为空")
    return symbols


def _is_index_code(symbol: str) -> bool:
    return symbol in INDEX_CODES


# --------------------------------------------------------------------------- #
# 合成行情（离线演示/测试）
# --------------------------------------------------------------------------- #
class SyntheticMarket:
    """确定性合成 A 股市场。

    同一参数组合每次生成完全相同的数据，方便单元测试与演示复现。
    """

    def __init__(self, n_symbols: int = 60, seed: int = 42):
        self.n_symbols = n_symbols
        self.seed = seed
        self.symbols = [f"S{1000 + i}" for i in range(n_symbols)]

    def _rng(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> np.random.Generator:
        key = f"{symbol}|{start.date()}|{end.date()}|{self.seed}".encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        return np.random.default_rng(int(digest[:16], 16))

    def _names(self) -> dict[str, str]:
        names = {}
        for i, symbol in enumerate(self.symbols):
            if i % 17 == 0:
                names[symbol] = f"ST合成{i:02d}"
            elif i % 23 == 0:
                names[symbol] = f"*ST合成{i:02d}"
            else:
                names[symbol] = f"合成股票{i:02d}"
        return names

    def get_daily_price(self, pool: Sequence[str], start_date: DATE_LIKE, end_date: DATE_LIKE) -> pd.DataFrame:
        start, end = _normalize_dates(start_date, end_date)
        symbols = _normalize_pool(pool)
        names = self._names()
        trading_days = pd.bdate_range(start, end)
        frames = []
        for symbol in symbols:
            rng = self._rng(symbol, start, end)
            n = len(trading_days)
            daily_drift = rng.normal(0.0003, 0.015, size=n)
            close = 10.0 * np.exp(np.cumsum(daily_drift))
            open_ = close * (1.0 + rng.normal(0, 0.004, size=n))
            high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0, 0.006, size=n)))
            low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0, 0.006, size=n)))
            volume = rng.integers(500_000, 20_000_000, size=n).astype(float)
            amount = volume * close
            float_shares = rng.uniform(2e8, 8e8)
            frame = pd.DataFrame(
                {
                    "date": trading_days,
                    "symbol": symbol,
                    "open": np.round(open_, 2),
                    "high": np.round(high, 2),
                    "low": np.round(low, 2),
                    "close": np.round(close, 2),
                    "volume": volume,
                    "amount": np.round(amount, 2),
                    "turnover": volume / float_shares,
                    "name": names.get(symbol, symbol),
                }
            )
            frames.append(frame)
        panel = pd.concat(frames, ignore_index=True)
        panel["is_st"] = panel["name"].str.contains("|".join(STOCK_NAME_KEYWORDS), na=False)
        return panel.set_index(["date", "symbol"]).sort_index()

    def get_index_constituents(self, index_code: str, date: DATE_LIKE) -> pd.DataFrame:
        n = self.n_symbols
        index = int(index_code[-3:]) if index_code[-3:].isdigit() else 300
        period = int(pd.Timestamp(date).year) * 100 + int(pd.Timestamp(date).month)
        symbols = []
        for i, symbol in enumerate(self.symbols):
            # 用符号序号与月份做确定性轮换，模拟成分股随时间的变动
            if (i * 31 + period * 7) % 23 < 13:
                symbols.append(symbol)
        if len(symbols) < 20:
            symbols = self.symbols[: max(20, min(n, 40))]
        names = self._names()
        sectors = [f"行业{i % 5}" for i in range(len(symbols))]
        return pd.DataFrame(
            {
                "symbol": symbols,
                "name": [names[s] for s in symbols],
                "sector": sectors,
                "index_code": index_code,
            }
        )


_SYNTHETIC_MARKET = SyntheticMarket()


# --------------------------------------------------------------------------- #
# AkShare 真实数据
# --------------------------------------------------------------------------- #
def _import_akshare():
    try:
        import akshare as ak  # noqa: F401

        return ak
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise DataSourceError("未安装 akshare，请先执行 pip install -r requirements.txt") from exc


def _akshare_daily(symbol: str, start: pd.Timestamp, end: pd.Timestamp, adjust: str) -> pd.DataFrame:
    ak = _import_akshare()
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")
    if _is_index_code(symbol):
        raw = ak.index_zh_a_hist(symbol=symbol, period="daily", start_date=start_str, end_date=end_str, adjust=adjust)
        rename = {
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover",
        }
    else:
        raw = ak.stock_zh_a_hist(
            symbol=symbol, period="daily", start_date=start_str, end_date=end_str, adjust=adjust
        )
        rename = {
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover",
        }
    df = raw.rename(columns=rename)
    df["symbol"] = symbol
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_cache(symbol: str, start: pd.Timestamp, end: pd.Timestamp, adjust: str) -> Optional[pd.DataFrame]:
    path = CACHE_DIR / "market" / f"{symbol}_{adjust}.csv.gz"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df


def _save_cache(df: pd.DataFrame, symbol: str, adjust: str) -> None:
    path = CACHE_DIR / "market" / f"{symbol}_{adjust}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index().to_csv(path, index=False, compression="gzip")


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def get_daily_price(
    pool: Union[str, Iterable[str], pd.DataFrame],
    start_date: DATE_LIKE,
    end_date: DATE_LIKE,
    provider: str = "auto",
    adjust: str = "qfq",
    use_cache: bool = True,
    fallback_to_synthetic: bool = True,
    sleep_seconds: float = 0.0,
) -> pd.DataFrame:
    """获取标准化的日线行情面板。

    返回 MultiIndex(date, symbol) 的 DataFrame，字段统一为：
    open/high/low/close/volume/amount/turnover/name/is_st。

    provider: auto（默认，优先 AkShare）/ akshare / synthetic。
    adjust: qfq（前复权，默认）/ hfq / ''。
    """
    start, end = _normalize_dates(start_date, end_date)
    symbols = _normalize_pool(pool)
    provider = provider.lower()

    if provider == "synthetic":
        return _SYNTHETIC_MARKET.get_daily_price(symbols, start, end)

    frames = []
    for symbol in symbols:
        frame = None
        if use_cache and provider == "auto":
            frame = _load_cache(symbol, start, end, adjust)
        if frame is None:
            try:
                frame = _akshare_daily(symbol, start, end, adjust)
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
            except (DataSourceError, Exception) as exc:  # 网络/限流等均按数据源异常处理
                if not fallback_to_synthetic:
                    raise DataSourceError(f"{symbol} 数据获取失败: {exc}") from exc
                synthetic = _SYNTHETIC_MARKET.get_daily_price([symbol], start, end)
                frame = synthetic.reset_index()
                frame["source"] = "synthetic_fallback"
            if use_cache:
                _save_cache(frame, symbol, adjust)
        else:
            frame["source"] = "cache"
        frames.append(frame)

    panel = pd.concat(frames, ignore_index=True)
    if "name" not in panel.columns:
        panel["name"] = panel["symbol"]
    if "turnover" not in panel.columns:
        panel["turnover"] = np.nan
    if "source" not in panel.columns:
        panel["source"] = "akshare"
    panel = panel.drop_duplicates(subset=["date", "symbol"], keep="last")
    panel = panel.set_index(["date", "symbol"]).sort_index()
    if "is_st" not in panel.columns and "name" in panel.columns:
        panel["is_st"] = panel["name"].str.contains("|".join(STOCK_NAME_KEYWORDS), na=False)
    return panel[["open", "high", "low", "close", "volume", "amount", "turnover", "name", "is_st"]]


def get_index_constituents(
    index_code: str,
    date: DATE_LIKE = None,
    provider: str = "auto",
    strict_history: bool = True,
    fallback_to_synthetic: bool = True,
) -> pd.DataFrame:
    """获取指数成分股，返回 DataFrame(symbol, name, sector)。

    strict_history=True 时，若用当前快照回查历史日期会直接报错，防止未来函数。
    如需历史成分，请把历史快照 CSV 放入 data/constituents/ 后使用
    load_constituents_snapshot。
    """
    if index_code not in INDEX_CODES:
        raise ValueError(f"不支持的指数代码: {index_code}，可选 {sorted(INDEX_CODES)}")
    asof = pd.Timestamp(date).normalize() if date is not None else pd.Timestamp.now().normalize()

    provider = provider.lower()
    if provider == "synthetic":
        return _SYNTHETIC_MARKET.get_index_constituents(index_code, asof)

    snapshot = _try_load_local_constituents(index_code, asof)
    if snapshot is not None:
        return snapshot

    ak = _import_akshare()
    try:
        raw = ak.index_stock_cons_csindex(symbol=index_code)
        symbol_col = "成分券代码" if "成分券代码" in raw.columns else raw.columns[0]
        name_col = "成分券名称" if "成分券名称" in raw.columns else raw.columns[1]
        result = pd.DataFrame(
            {"symbol": raw[symbol_col].astype(str), "name": raw[name_col].astype(str)}
        )
        result["index_code"] = index_code
        result["snapshot_date"] = pd.Timestamp.now().normalize()
    except Exception as exc:
        if not fallback_to_synthetic:
            raise DataSourceError(f"获取 {index_code} 成分股失败: {exc}") from exc
        result = _SYNTHETIC_MARKET.get_index_constituents(index_code, asof)
        result["snapshot_date"] = pd.Timestamp.now().normalize()

    if strict_history and "snapshot_date" in result.columns:
        snapshot_date = pd.Timestamp(result["snapshot_date"].iloc[0]).normalize()
        if asof < snapshot_date - pd.Timedelta(days=30):
            raise DataUnavailableError(
                f"{index_code} 仅有 {snapshot_date.date()} 的当前成分快照，"
                f"请求日期 {asof.date()} 早于快照。请提供历史成分快照到 "
                f"{CONSTITUENTS_DIR} 后重试，禁止用当前成分回测历史。"
            )
    if "sector" not in result.columns:
        result["sector"] = np.nan
    return result[["symbol", "name", "sector"]]


def _try_load_local_constituents(index_code: str, asof: pd.Timestamp) -> Optional[pd.DataFrame]:
    """从本地历史快照读取指定日期可得的成分股。"""
    if not CONSTITUENTS_DIR.exists():
        return None
    for path in sorted(CONSTITUENTS_DIR.glob(f"{index_code}_*.csv")):
        df = pd.read_csv(path, dtype={"symbol": str})
        if "snapshot_date" not in df.columns:
            continue
        snapshot_date = pd.to_datetime(df["snapshot_date"].iloc[0]).normalize()
        if snapshot_date <= asof:
            return df
    return None


def clean_panel(
    panel: pd.DataFrame,
    drop_zero_volume: bool = True,
    fill_missing_days: bool = False,
    ffill_limit: int = 5,
) -> pd.DataFrame:
    """数据清洗：去重、去停牌日、标记 ST、可选按交易日补齐。

    fill_missing_days=True 时按自然交易日补齐缺失行，并使用前值填充行情，
    同时新增 was_filled 标记，便于后续识别停牌/未上市区间。
    """
    if not isinstance(panel.index, pd.MultiIndex):
        panel = panel.set_index(["date", "symbol"])
    panel = panel.reset_index().drop_duplicates(subset=["date", "symbol"], keep="last")
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel.dropna(subset=["close"]).sort_values(["symbol", "date"])

    if "name" in panel.columns and "is_st" not in panel.columns:
        panel["is_st"] = panel["name"].str.contains("|".join(STOCK_NAME_KEYWORDS), na=False)

    if drop_zero_volume and "volume" in panel.columns:
        panel = panel[panel["volume"].fillna(0) > 0]

    if fill_missing_days:
        filled = []
        trading_days = pd.bdate_range(panel["date"].min(), panel["date"].max())
        for symbol, group in panel.groupby("symbol", sort=False):
            group = group.set_index("date").reindex(trading_days)
            group["symbol"] = symbol
            group["was_filled"] = group["close"].isna()
            for col in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
                if col in group.columns:
                    group[col] = group[col].ffill(limit=ffill_limit)
            filled.append(group.reset_index().rename(columns={"index": "date"}))
        panel = pd.concat(filled, ignore_index=True)
        if "volume" in panel.columns:
            panel.loc[panel["was_filled"], "volume"] = 0.0

    panel = panel.set_index(["date", "symbol"]).sort_index()
    if "daily_return" not in panel.columns:
        panel["daily_return"] = panel.groupby(level="symbol")["close"].pct_change()
    return panel


def get_financials(
    pool: Union[str, Iterable[str], pd.DataFrame],
    start_date: DATE_LIKE,
    end_date: DATE_LIKE,
    provider: str = "synthetic",
    fallback_to_synthetic: bool = True,
) -> pd.DataFrame:
    """获取标准化财务数据：symbol / ann_date / pe / pb / roe。

    provider=synthetic 生成确定性的季度公告数据，用于离线演示财务因子。
    AkShare 财务接口可在此扩展，真实数据必须保留 ann_date 做时点对齐。
    """
    start, end = _normalize_dates(start_date, end_date)
    symbols = _normalize_pool(pool)
    if provider == "synthetic":
        rows = []
        quarters = pd.date_range(start, end, freq="QE")
        for symbol in symbols:
            for quarter in quarters:
                rng = _SYNTHETIC_MARKET._rng(symbol, quarter, quarter)
                rows.append(
                    {
                        "symbol": symbol,
                        "ann_date": quarter + pd.Timedelta(days=30),
                        "pe": float(rng.uniform(8.0, 40.0)),
                        "pb": float(rng.uniform(0.8, 6.0)),
                        "roe": float(rng.uniform(0.02, 0.25)),
                    }
                )
        return pd.DataFrame(rows)
    raise DataSourceError("get_financials 目前仅支持 synthetic provider")


def ensure_dirs() -> None:
    for path in (DATA_DIR, CACHE_DIR, CONSTITUENTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
