"""Build a point-in-time multi-factor research panel.

The panel joins the immutable daily snapshot with a ``FUND-*`` fundamental
snapshot. Financial values are used only after their announcement date; share
capital and dividends use their effective dates. Returns use adjusted prices,
while market caps use unadjusted close times shares outstanding.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from config.settings import DATA_DIR
from data_agent.fundamentals import (
    latest_fundamental_for_run,
    load_fundamental_manifest,
)
from data_agent.store import CHINA_TZ, RawRunStore

FACTOR_VERSION = 1
FINANCIAL_INDUSTRIES = frozenset({"银行", "非银金融"})

FACTOR_DEFINITIONS = [
    ("ep_ttm", "value", "盈利收益率", "净利润TTM / 总市值", "ratio", "公告日<=交易日"),
    ("bp_ttm", "value", "账面市值比", "归属母公司净资产 / 总市值", "ratio", "公告日<=交易日"),
    ("sp_ttm", "value", "营收市值比", "营业收入TTM / 总市值", "ratio", "公告日<=交易日"),
    ("cfp_ttm", "value", "现金流市值比", "经营现金流TTM / 总市值", "ratio", "公告日<=交易日"),
    ("div_yield", "value", "股息率", "近12个月每股现金分红 / 不复权收盘价", "ratio", "除息日<=交易日"),
    ("roe_ttm", "quality", "净资产收益率", "净利润TTM / 平均净资产", "ratio", "公告日<=交易日"),
    ("roa_ttm", "quality", "总资产收益率", "净利润TTM / 平均总资产", "ratio", "公告日<=交易日"),
    ("gross_margin", "quality", "毛利率", "(营收TTM-营业成本TTM) / 营收TTM", "ratio", "公告日<=交易日"),
    ("net_margin", "quality", "净利率", "净利润TTM / 营收TTM", "ratio", "公告日<=交易日"),
    ("ocf_ni_ratio", "quality", "现金流利润比", "经营现金流TTM / 净利润TTM", "ratio", "公告日<=交易日"),
    ("ni_growth_q", "growth", "单季净利润同比", "(当季净利润-去年同季) / |去年同季|", "ratio", "公告日<=交易日"),
    ("rev_growth_q", "growth", "单季营收同比", "(当季营收-去年同季) / |去年同季|", "ratio", "公告日<=交易日"),
    ("ret_5d", "price", "5日收益率", "收盘价 / 5交易日前收盘价 - 1", "ratio", "仅用历史价格"),
    ("rev_5d", "price", "5日短期反转", "-5日收益率", "ratio", "仅用历史价格"),
    ("ret_10d", "price", "10日收益率", "收盘价 / 10交易日前收盘价 - 1", "ratio", "仅用历史价格"),
    ("rev_10d", "price", "10日反转", "-10日收益率", "ratio", "仅用历史价格"),
    ("mom_20d", "price", "20日动量", "收盘价 / 20交易日前收盘价 - 1", "ratio", "仅用历史价格"),
    ("vol_20d", "price", "20日波动率", "20个日收益率的标准差", "ratio", "仅用历史价格"),
    ("turn_20d", "price", "20日均换手率", "20个交易日换手率均值", "percent", "仅用历史价格"),
    ("rs_20d", "price", "20日相对位置", "收盘价 / 20日最高价", "ratio", "仅用历史价格"),
    ("ln_cap", "risk", "对数总市值", "ln(不复权收盘价 × 总股本)", "log_cny", "股本生效日<=交易日"),
    ("beta_20d", "risk", "20日市场Beta", "个股日收益对沪深300日收益的20日OLS斜率", "ratio", "仅用历史价格"),
    ("residual_vol", "risk", "残差波动率", "20日回归残差的标准差", "ratio", "仅用历史价格"),
    ("leverage", "risk", "杠杆率", "总负债 / 总资产", "ratio", "公告日<=交易日"),
]

FACTOR_FIELDS = [item[0] for item in FACTOR_DEFINITIONS]
FACTOR_GROUPS = {
    "value": "价值",
    "quality": "质量",
    "growth": "成长",
    "price": "量价 Alpha",
    "risk": "风险中性",
}

FACTOR_GUIDANCE = {
    "ep_ttm": (
        "每投入 1 元市值对应的过去 12 个月归母净利润，越高通常越偏低估。",
        "适合作为价值组的核心排序因子，建模前按行业与市值中性化。",
    ),
    "bp_ttm": (
        "账面净资产相对市值的折价程度，衡量资产端估值保护。",
        "适合与盈利质量组合使用，避免只买低估值但资产回报差的股票。",
    ),
    "sp_ttm": (
        "过去 12 个月营业收入相对市值的比例，反映市场为营收支付的倍数。",
        "适合成长与周期行业横截面比较，建议同时控制净利率差异。",
    ),
    "cfp_ttm": (
        "经营现金流相对市值的比例，现金创造能力比会计利润更难被操纵。",
        "适合作为价值因子的稳健替代，尤其用于识别利润含金量偏低公司。",
    ),
    "div_yield": (
        "过去 12 个月已实施现金分红相对当前不复权股价的收益率。",
        "适合红利与防御组合筛选，需与派息持续性、杠杆率一起评估。",
    ),
    "roe_ttm": (
        "过去 12 个月归母净利润相对平均净资产的回报率，衡量股东资本效率。",
        "适合质量选股与盈利稳定性筛选，金融企业需按行业分别标准化。",
    ),
    "roa_ttm": (
        "过去 12 个月净利润相对平均总资产的回报率，衡量全部资产使用效率。",
        "适合跨资本结构比较，与 ROE 联用可区分经营效率与财务杠杆贡献。",
    ),
    "gross_margin": (
        "营业收入扣除营业成本后的剩余比例，反映产品定价与成本控制能力。",
        "适合行业内比较；银行、保险等金融模板不适用，应保留空值而非补造。",
    ),
    "net_margin": (
        "过去 12 个月归母净利润占营业收入的比例，反映最终盈利转化效率。",
        "适合与毛利率联用定位费用或减值压力，并按行业做横截面标准化。",
    ),
    "ocf_ni_ratio": (
        "经营现金流与归母净利润之比，用于检查利润兑现为现金的程度。",
        "适合质量防雷和异常利润排查，极端值应在建模时缩尾处理。",
    ),
    "ni_growth_q": (
        "本季度归母净利润相对去年同季的变化率，消除季节性影响。",
        "适合成长与盈利加速识别，负基数时以绝对值作分母并保留异常标记。",
    ),
    "rev_growth_q": (
        "本季度营业收入相对去年同季的变化率，衡量业务规模增长。",
        "适合成长因子，与利润率变化联用可识别牺牲盈利换收入的情况。",
    ),
    "ret_5d": (
        "最近 5 个交易日的前复权价格收益，捕捉极短期价格方向。",
        "适合短周期动量研究，A 股中常与反转因子同时检验稳定性。",
    ),
    "rev_5d": (
        "最近 5 日收益的相反数，衡量短期超跌后的反转倾向。",
        "适合短周期反转策略，换手较高，应计入交易成本与冲击成本。",
    ),
    "ret_10d": (
        "最近 10 个交易日的前复权价格收益，捕捉两周趋势。",
        "适合与 5 日、20 日动量做多周期比较，检验信号衰减速度。",
    ),
    "rev_10d": (
        "最近 10 日收益的相反数，衡量两周期反转倾向。",
        "适合作为短反转备选，须在行业和市值中性后评估。",
    ),
    "mom_20d": (
        "最近 20 个交易日的前复权价格收益，衡量约一个月的趋势强度。",
        "适合中频动量研究，建议做行业中性与去极值后进入模型。",
    ),
    "vol_20d": (
        "最近 20 个交易日日收益的标准差，衡量短期价格风险。",
        "适合风险惩罚、波动率目标与组合约束，不宜单独作为收益信号。",
    ),
    "turn_20d": (
        "最近 20 个交易日换手率均值，反映交易活跃度与情绪拥挤。",
        "适合流动性过滤、拥挤度监控和交易成本预估。",
    ),
    "rs_20d": (
        "当前收盘价相对最近 20 日最高价的位置，越接近 1 表示越靠近区间高位。",
        "适合突破与趋势强度研究，同时检查追高导致的回撤风险。",
    ),
    "ln_cap": (
        "总市值的自然对数，是规模风格与流动性的基础代理变量。",
        "主要用于市值中性化和 Size 风险暴露控制，不直接作为选股信号。",
    ),
    "beta_20d": (
        "个股最近 20 日收益对沪深 300 收益的敏感度，衡量市场风险暴露。",
        "适合组合 Beta 约束与市场中性化，短窗口需结合稳定性检验。",
    ),
    "residual_vol": (
        "剥离市场收益后最近 20 日日收益残差的标准差，衡量个股特质风险。",
        "适合特质波动约束、风险预算和异常波动预警。",
    ),
    "leverage": (
        "总负债除以总资产，衡量资本结构与偿债压力暴露。",
        "适合风险筛查与行业中性化，金融企业应与普通行业分模板处理。",
    ),
}
CONTEXT_FIELDS = [
    "date",
    "symbol",
    "name",
    "industry",
    "industry_l2",
    "industry_l3",
    "list_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turnover",
    "total_cap",
    "float_cap",
    "close_raw",
    "report_date",
    "ann_date",
    "financial_template",
    "total_shares",
    "float_shares",
    "div_ttm_per_share",
    "factor_ready",
    "missing_groups",
]
PANEL_FIELDS = CONTEXT_FIELDS + FACTOR_FIELDS
STRING_FIELDS = {
    "date",
    "symbol",
    "name",
    "industry",
    "industry_l2",
    "industry_l3",
    "list_date",
    "report_date",
    "ann_date",
    "financial_template",
    "missing_groups",
}

FINANCIAL_DERIVED_FIELDS = [
    "symbol",
    "report_date",
    "ann_date",
    "report_type",
    "fiscal_year",
    "quarter",
    "revenue_ytd",
    "net_profit_ytd",
    "operating_cf_ytd",
    "cost_of_sales_ytd",
    "revenue_q",
    "net_profit_q",
    "operating_cf_q",
    "cost_of_sales_q",
    "revenue",
    "net_profit",
    "operating_cf",
    "cost_of_sales",
    "revenue_ttm",
    "net_profit_ttm",
    "operating_cf_ttm",
    "cost_of_sales_ttm",
    "total_asset",
    "total_liability",
    "equity",
    "equity_avg",
    "total_asset_avg",
    "ni_growth_q",
    "rev_growth_q",
    "financial_template",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_write(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _safe_div(left: pd.Series, right: pd.Series) -> pd.Series:
    result = left / right.replace(0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def _safe_growth(current: pd.Series, previous: pd.Series) -> pd.Series:
    return _safe_div(current - previous, previous.abs())


def prepare_financials(
    raw: pd.DataFrame,
    industry_by_symbol: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Convert cumulative YTD reports into single-quarter and TTM rows."""
    if raw.empty:
        return pd.DataFrame(columns=FINANCIAL_DERIVED_FIELDS)
    df = raw.copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    for column in (
        "revenue_ytd",
        "net_profit_ytd",
        "operating_cf_ytd",
        "cost_of_sales_ytd",
        "total_asset",
        "total_liability",
        "equity",
        "total_shares",
        "float_shares",
    ):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    df["ann_date"] = pd.to_datetime(df["ann_date"], errors="coerce")
    # A report without an announcement date cannot be aligned without look-ahead.
    df = df.dropna(subset=["symbol", "report_date", "ann_date"]).sort_values(
        ["symbol", "report_date"]
    )
    df["fiscal_year"] = df["report_date"].dt.year
    df["quarter"] = df["report_date"].dt.quarter
    df["report_date"] = df["report_date"].dt.strftime("%Y-%m-%d")
    df["ann_date"] = df["ann_date"].dt.strftime("%Y-%m-%d")

    for source, target in (
        ("revenue_ytd", "revenue_q"),
        ("net_profit_ytd", "net_profit_q"),
        ("operating_cf_ytd", "operating_cf_q"),
        ("cost_of_sales_ytd", "cost_of_sales_q"),
    ):
        df[target] = df.groupby(["symbol", "fiscal_year"])[source].diff()
        first_quarter = df["quarter"] == 1
        df.loc[first_quarter, target] = df.loc[first_quarter, source]

    for quarter_field, c_field in (
        ("revenue_q", "revenue"),
        ("net_profit_q", "net_profit"),
        ("operating_cf_q", "operating_cf"),
        ("cost_of_sales_q", "cost_of_sales"),
    ):
        df[c_field] = df[quarter_field]

    annual = df[df["quarter"] == 4][
        [
            "symbol",
            "fiscal_year",
            "revenue_ytd",
            "net_profit_ytd",
            "operating_cf_ytd",
            "cost_of_sales_ytd",
        ]
    ].rename(
        columns={
            "revenue_ytd": "annual_revenue",
            "net_profit_ytd": "annual_net_profit",
            "operating_cf_ytd": "annual_operating_cf",
            "cost_of_sales_ytd": "annual_cost",
        }
    )
    # Q1-Q3 TTM needs the previous fiscal year's annual report, never the
    # current year's future annual report.
    annual["fiscal_year"] = annual["fiscal_year"] + 1
    prior = df[
        [
            "symbol",
            "fiscal_year",
            "quarter",
            "revenue_q",
            "net_profit_q",
            "operating_cf_q",
            "cost_of_sales_q",
            "total_asset",
            "equity",
        ]
    ].copy()
    prior["fiscal_year"] = prior["fiscal_year"] + 1
    prior = prior.rename(
        columns={
            "revenue_q": "prior_revenue_q",
            "net_profit_q": "prior_net_profit_q",
            "operating_cf_q": "prior_operating_cf_q",
            "cost_of_sales_q": "prior_cost_q",
            "total_asset": "prior_total_asset",
            "equity": "prior_equity",
        }
    )
    df = df.merge(annual, on=["symbol", "fiscal_year"], how="left")
    df = df.merge(prior, on=["symbol", "fiscal_year", "quarter"], how="left")

    for ytd, annual_col, prior_col, target in (
        ("revenue_ytd", "annual_revenue", "prior_revenue_q", "revenue_ttm"),
        (
            "net_profit_ytd",
            "annual_net_profit",
            "prior_net_profit_q",
            "net_profit_ttm",
        ),
        (
            "operating_cf_ytd",
            "annual_operating_cf",
            "prior_operating_cf_q",
            "operating_cf_ttm",
        ),
        ("cost_of_sales_ytd", "annual_cost", "prior_cost_q", "cost_of_sales_ttm"),
    ):
        ttm = df[ytd] + df[annual_col] - df[prior_col]
        ttm = ttm.where(df["quarter"] != 4, df[ytd])
        df[target] = ttm

    df["equity_avg"] = (df["equity"] + df["prior_equity"]) / 2.0
    df["total_asset_avg"] = (df["total_asset"] + df["prior_total_asset"]) / 2.0
    df["ni_growth_q"] = _safe_growth(df["net_profit_q"], df["prior_net_profit_q"])
    df["rev_growth_q"] = _safe_growth(df["revenue_q"], df["prior_revenue_q"])
    # Missing cost data can occur for newly listed industrial companies.
    # Only the SW level-1 financial industries receive the financial template.
    industry = df["symbol"].map(industry_by_symbol or {})
    df["financial_template"] = np.where(
        industry.isin(FINANCIAL_INDUSTRIES),
        "financial",
        "general",
    )
    return df[FINANCIAL_DERIVED_FIELDS].sort_values(["symbol", "ann_date", "report_date"])


def _merge_asof(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_on: str,
    right_on: str,
) -> pd.DataFrame:
    if right.empty:
        result = left.copy()
        for column in right.columns:
            if column not in result.columns and column not in {right_on, "symbol"}:
                result[column] = np.nan
        return result
    right = right.dropna(subset=[right_on]).sort_values([right_on]).drop_duplicates(
        right_on, keep="last"
    )
    return pd.merge_asof(
        left.sort_values(left_on),
        right.sort_values(right_on),
        left_on=left_on,
        right_on=right_on,
        direction="backward",
        allow_exact_matches=True,
    )


def _trailing_dividend(
    dates: pd.Series,
    events: pd.DataFrame,
    window_days: int = 365,
) -> pd.Series:
    result = np.zeros(len(dates), dtype="float64")
    if events.empty:
        return pd.Series(result, index=dates.index)
    events = events.dropna(subset=["ex_dividend_date", "cash_per_share"]).copy()
    events = events.sort_values("ex_dividend_date")
    event_dates = pd.to_datetime(events["ex_dividend_date"]).values.astype("datetime64[D]")
    values = events["cash_per_share"].astype(float).values
    if len(event_dates) == 0:
        return pd.Series(result, index=dates.index)
    prefix = np.concatenate([[0.0], np.cumsum(values)])
    day_values = pd.to_datetime(dates).values.astype("datetime64[D]")
    left = np.searchsorted(
        event_dates,
        day_values - np.timedelta64(window_days, "D"),
        side="right",
    )
    right = np.searchsorted(event_dates, day_values, side="right")
    result = prefix[right] - prefix[left]
    return pd.Series(result, index=dates.index)


def _final_schema() -> pa.Schema:
    fields = []
    for name in PANEL_FIELDS:
        if name in STRING_FIELDS:
            fields.append(pa.field(name, pa.string()))
        elif name == "factor_ready":
            fields.append(pa.field(name, pa.bool_()))
        else:
            fields.append(pa.field(name, pa.float64()))
    return pa.schema(fields)


def _write_factor_rows(
    frame: pd.DataFrame,
    writers: dict[int, pq.ParquetWriter],
    schema: pa.Schema,
    root: Path,
) -> None:
    if frame.empty:
        return
    years = frame["date"].str.slice(0, 4)
    for year in sorted(years.unique()):
        part = frame[years == year].copy()
        for name in PANEL_FIELDS:
            if name in STRING_FIELDS:
                part[name] = part[name].fillna("").astype(str)
            elif name == "factor_ready":
                part[name] = part[name].astype(bool)
            else:
                part[name] = pd.to_numeric(part[name], errors="coerce").astype("float64")
        table = pa.Table.from_pandas(
            part[PANEL_FIELDS],
            schema=schema,
            preserve_index=False,
        )
        year_int = int(year)
        if year_int not in writers:
            target = root / f"year={year_int}" / "part-00000.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            writers[year_int] = pq.ParquetWriter(target, schema, compression="zstd")
        writers[year_int].write_table(table)


def _factor_ready(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    missing = pd.DataFrame(
        {name: frame[name].isna() for name in FACTOR_FIELDS},
        index=frame.index,
    )
    financial_mask = frame["financial_template"].astype(str).eq("financial")
    if financial_mask.any():
        missing.loc[financial_mask, "gross_margin"] = False
    ready = ~missing.any(axis=1)
    groups = {
        "value": ["ep_ttm", "bp_ttm", "sp_ttm", "cfp_ttm", "div_yield"],
        "quality": ["roe_ttm", "roa_ttm", "gross_margin", "net_margin", "ocf_ni_ratio"],
        "growth": ["ni_growth_q", "rev_growth_q"],
        "price": ["ret_5d", "rev_5d", "ret_10d", "rev_10d", "mom_20d", "vol_20d", "turn_20d", "rs_20d"],
        "risk": ["ln_cap", "beta_20d", "residual_vol", "leverage"],
    }
    labels = []
    for index in frame.index:
        missing_groups = [
            key
            for key, columns in groups.items()
            if any(
                pd.isna(frame.at[index, column])
                for column in columns
                if not (
                    bool(financial_mask.at[index])
                    and column == "gross_margin"
                )
            )
        ]
        labels.append(",".join(missing_groups))
    return ready, pd.Series(labels, index=frame.index)


def _build_symbol_frame(
    daily: pd.DataFrame,
    profile: dict,
    financial: pd.DataFrame,
    capital: pd.DataFrame,
    dividends: pd.DataFrame,
    prices: pd.DataFrame,
    index_returns: pd.DataFrame,
) -> pd.DataFrame:
    g = daily.copy().sort_values("date")
    g["date"] = pd.to_datetime(g["date"])
    if not financial.empty:
        financial = financial.copy()
        financial["ann_date"] = pd.to_datetime(financial["ann_date"], errors="coerce")
        f = financial.sort_values(["ann_date", "report_date"]).dropna(
            subset=["ann_date"]
        )
        f = f.drop_duplicates("ann_date", keep="last")
        f = f.drop(columns=["symbol"], errors="ignore")
        rename = {
            "total_shares": "fin_total_shares",
            "float_shares": "fin_float_shares",
        }
        f = f.rename(columns=rename)
        g = pd.merge_asof(
            g.sort_values("date"),
            f.sort_values("ann_date"),
            left_on="date",
            right_on="ann_date",
            direction="backward",
            allow_exact_matches=True,
        )
    else:
        for name in FINANCIAL_DERIVED_FIELDS:
            if name not in g.columns:
                g[name] = np.nan
        g["report_date"] = ""
        g["ann_date"] = ""

    if not capital.empty:
        capital = capital.copy()
        capital["end_date"] = pd.to_datetime(capital["end_date"], errors="coerce")
        c = capital.sort_values(["end_date", "ann_date"]).drop_duplicates(
            "end_date", keep="last"
        )
        c = c[["end_date", "total_shares", "float_shares"]].rename(
            columns={
                "total_shares": "cap_total_shares",
                "float_shares": "cap_float_shares",
            }
        )
        g = pd.merge_asof(
            g.sort_values("date"),
            c.sort_values("end_date"),
            left_on="date",
            right_on="end_date",
            direction="backward",
            allow_exact_matches=True,
        )
    else:
        g["cap_total_shares"] = np.nan
        g["cap_float_shares"] = np.nan

    if not prices.empty:
        prices = prices.copy()
        prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
        p = prices[["date", "close_raw"]].sort_values("date").drop_duplicates(
            "date", keep="last"
        )
        g = g.merge(p, on="date", how="left")
    else:
        g["close_raw"] = np.nan

    if not index_returns.empty:
        g = g.merge(index_returns, on="date", how="left")
    else:
        g["mkt_ret"] = np.nan

    close = pd.to_numeric(g["close"], errors="coerce")
    ret_1d = close.pct_change(fill_method=None)
    ret_5d = close.pct_change(5, fill_method=None)
    ret_10d = close.pct_change(10, fill_method=None)
    ret_20d = close.pct_change(20, fill_method=None)
    vol_20d = ret_1d.rolling(20, min_periods=20).std()
    turn_20d = pd.to_numeric(g["turnover"], errors="coerce").rolling(
        20, min_periods=20
    ).mean()
    high_20d = pd.to_numeric(g["high"], errors="coerce").rolling(
        20, min_periods=20
    ).max()
    rs_20d = close / high_20d.replace(0, np.nan)

    mkt = pd.to_numeric(g["mkt_ret"], errors="coerce")
    mean_y = ret_1d.rolling(20, min_periods=20).mean()
    mean_x = mkt.rolling(20, min_periods=20).mean()
    cov = (ret_1d * mkt).rolling(20, min_periods=20).mean() - mean_y * mean_x
    var_x = (mkt * mkt).rolling(20, min_periods=20).mean() - mean_x * mean_x
    var_y = (ret_1d * ret_1d).rolling(20, min_periods=20).mean() - mean_y * mean_y
    beta = _safe_div(cov, var_x)
    residual_var = var_y - beta * cov
    residual_vol = np.sqrt(residual_var.clip(lower=0))

    total_cap = pd.to_numeric(g["close_raw"], errors="coerce") * pd.to_numeric(
        g["cap_total_shares"], errors="coerce"
    )
    float_cap = pd.to_numeric(g["close_raw"], errors="coerce") * pd.to_numeric(
        g["cap_float_shares"], errors="coerce"
    )
    net_profit_ttm = pd.to_numeric(g["net_profit_ttm"], errors="coerce")
    revenue_ttm = pd.to_numeric(g["revenue_ttm"], errors="coerce")
    cost_ttm = pd.to_numeric(g["cost_of_sales_ttm"], errors="coerce")
    operating_cf_ttm = pd.to_numeric(g["operating_cf_ttm"], errors="coerce")
    equity = pd.to_numeric(g["equity"], errors="coerce")
    equity_avg = pd.to_numeric(g["equity_avg"], errors="coerce")
    total_asset = pd.to_numeric(g["total_asset"], errors="coerce")
    total_asset_avg = pd.to_numeric(g["total_asset_avg"], errors="coerce")
    total_liability = pd.to_numeric(g["total_liability"], errors="coerce")
    div_ttm = _trailing_dividend(g["date"], dividends)

    out = pd.DataFrame(index=g.index)
    out["date"] = g["date"].dt.strftime("%Y-%m-%d")
    out["symbol"] = g["symbol"].astype(str).str.zfill(6)
    out["name"] = profile.get("name") or g["name"].astype(str)
    out["industry"] = profile.get("industry") or g.get("industry", "")
    out["industry_l2"] = profile.get("industry_l2", "")
    out["industry_l3"] = profile.get("industry_l3", "")
    out["list_date"] = profile.get("list_date") or ""
    for name in ("open", "high", "low", "close", "volume", "amount", "turnover"):
        out[name] = pd.to_numeric(g.get(name), errors="coerce")
    out["total_cap"] = total_cap
    out["float_cap"] = float_cap
    out["close_raw"] = pd.to_numeric(g["close_raw"], errors="coerce")
    out["report_date"] = g.get("report_date", pd.Series("", index=g.index)).fillna("")
    out["ann_date"] = g.get("ann_date", pd.Series("", index=g.index)).fillna("")
    out["financial_template"] = g.get(
        "financial_template", pd.Series("", index=g.index)
    ).fillna("")
    out["total_shares"] = pd.to_numeric(g["cap_total_shares"], errors="coerce")
    out["float_shares"] = pd.to_numeric(g["cap_float_shares"], errors="coerce")
    out["div_ttm_per_share"] = div_ttm
    out["ep_ttm"] = _safe_div(net_profit_ttm, total_cap)
    out["bp_ttm"] = _safe_div(equity, total_cap)
    out["sp_ttm"] = _safe_div(revenue_ttm, total_cap)
    out["cfp_ttm"] = _safe_div(operating_cf_ttm, total_cap)
    out["div_yield"] = _safe_div(div_ttm, out["close_raw"])
    out["roe_ttm"] = _safe_div(net_profit_ttm, equity_avg)
    out["roa_ttm"] = _safe_div(net_profit_ttm, total_asset_avg)
    out["gross_margin"] = _safe_div(revenue_ttm - cost_ttm, revenue_ttm)
    out["net_margin"] = _safe_div(net_profit_ttm, revenue_ttm)
    out["ocf_ni_ratio"] = _safe_div(operating_cf_ttm, net_profit_ttm)
    out["ni_growth_q"] = pd.to_numeric(g.get("ni_growth_q"), errors="coerce")
    out["rev_growth_q"] = pd.to_numeric(g.get("rev_growth_q"), errors="coerce")
    out["ret_5d"] = ret_5d
    out["rev_5d"] = -ret_5d
    out["ret_10d"] = ret_10d
    out["rev_10d"] = -ret_10d
    out["mom_20d"] = ret_20d
    out["vol_20d"] = vol_20d
    out["turn_20d"] = turn_20d
    out["rs_20d"] = rs_20d
    out["ln_cap"] = np.log(total_cap.where(total_cap > 0))
    out["beta_20d"] = beta
    out["residual_vol"] = residual_vol
    out["leverage"] = _safe_div(total_liability, total_asset)
    ready, missing_groups = _factor_ready(out)
    out["factor_ready"] = ready
    out["missing_groups"] = missing_groups
    return out[PANEL_FIELDS]


def _write_table_csv(df: pd.DataFrame, path: Path) -> dict:
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return {"path": path.name, "rows": len(df), "bytes": path.stat().st_size}


def _write_parquet(df: pd.DataFrame, path: Path) -> dict:
    df.to_parquet(path, index=False, compression="zstd")
    return {"path": path.name, "rows": len(df), "bytes": path.stat().st_size}


def _coverage(con: duckdb.DuckDBPyConnection, panel_glob: str) -> dict:
    summary = con.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(date), MAX(date),
               SUM(CASE WHEN factor_ready THEN 1 ELSE 0 END)
        FROM read_parquet(?, hive_partitioning = true)
        """,
        [panel_glob],
    ).fetchone()
    by_factor = {}
    for name, group, label, formula, unit, as_of in FACTOR_DEFINITIONS:
        row = con.execute(
            f"""
            SELECT COUNT({name}), COUNT(DISTINCT CASE WHEN {name} IS NOT NULL THEN symbol END)
            FROM read_parquet(?, hive_partitioning = true)
            """,
            [panel_glob],
        ).fetchone()
        by_factor[name] = {
            "group": group,
            "group_label": FACTOR_GROUPS[group],
            "label": label,
            "formula": formula,
            "meaning": FACTOR_GUIDANCE[name][0],
            "usage": FACTOR_GUIDANCE[name][1],
            "unit": unit,
            "as_of_rule": as_of,
            "non_null_rows": int(row[0] or 0),
            "coverage": float((row[0] or 0) / summary[0]) if summary[0] else 0.0,
            "symbols": int(row[1] or 0),
        }
    by_base_field = {}
    for name in (
        "close",
        "volume",
        "amount",
        "turnover",
        "total_cap",
        "float_cap",
        "close_raw",
        "report_date",
        "ann_date",
        "total_shares",
        "float_shares",
    ):
        row = con.execute(
            f"""
            SELECT COUNT({name}),
                   COUNT(DISTINCT CASE WHEN {name} IS NOT NULL THEN symbol END)
            FROM read_parquet(?, hive_partitioning = true)
            """,
            [panel_glob],
        ).fetchone()
        by_base_field[name] = {
            "non_null_rows": int(row[0] or 0),
            "coverage": float((row[0] or 0) / summary[0]) if summary[0] else 0.0,
            "symbols": int(row[1] or 0),
        }
    by_year = [
        {"year": row[0], "rows": int(row[1]), "factor_ready_rows": int(row[2] or 0)}
        for row in con.execute(
            """
            SELECT substr(CAST(date AS VARCHAR), 1, 4) AS year,
                   COUNT(*),
                   SUM(CASE WHEN factor_ready THEN 1 ELSE 0 END)
            FROM read_parquet(?, hive_partitioning = true)
            GROUP BY 1 ORDER BY 1
            """,
            [panel_glob],
        ).fetchall()
    ]
    by_symbol = con.execute(
        """
        SELECT symbol, COUNT(*) AS rows,
               SUM(CASE WHEN factor_ready THEN 1 ELSE 0 END) AS factor_ready_rows,
               COUNT(DISTINCT ann_date) AS announced_periods,
               MIN(date) AS first_date, MAX(date) AS last_date,
               MAX(ann_date) AS latest_ann_date,
               MAX(industry) AS industry
        FROM read_parquet(?, hive_partitioning = true)
        GROUP BY symbol ORDER BY factor_ready_rows DESC, symbol
        """,
        [panel_glob],
    ).fetchdf()
    return {
        "summary": {
            "rows": int(summary[0] or 0),
            "symbols": int(summary[1] or 0),
            "date_min": summary[2],
            "date_max": summary[3],
            "factor_ready_rows": int(summary[4] or 0),
            "factor_ready_ratio": float((summary[4] or 0) / summary[0])
            if summary[0]
            else 0.0,
        },
        "by_factor": by_factor,
        "by_base_field": by_base_field,
        "by_year": by_year,
        "by_symbol": by_symbol,
    }


def _write_duckdb(root: Path, panel_glob: str, manifest: dict) -> dict:
    target = root / "factors.duckdb"
    connection = duckdb.connect(str(target))
    try:
        connection.execute(
            "CREATE OR REPLACE TABLE factor_panel AS "
            "SELECT * FROM read_parquet(?, hive_partitioning = true)",
            [panel_glob],
        )
        for name in (
            "financials_quarterly",
            "profiles",
            "share_capital",
            "dividends",
            "prices_unadjusted",
            "index_daily",
        ):
            path = root / f"{name}.parquet"
            if path.exists():
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_parquet(?)",
                    [str(path.resolve())],
                )
        connection.execute(
            "CREATE OR REPLACE TABLE factor_metadata "
            "(dataset VARCHAR, raw_run_id VARCHAR, fund_id VARCHAR, generated_at VARCHAR)"
        )
        connection.execute(
            "INSERT INTO factor_metadata VALUES (?, ?, ?, ?)",
            [
                "QuantGuard multi-factor panel",
                manifest["raw_run_id"],
                manifest["fund_id"],
                manifest["generated_at"],
            ],
        )
    finally:
        connection.close()
    return {
        "status": "ready",
        "path": target.name,
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "tables": [
            "factor_panel",
            "financials_quarterly",
            "profiles",
            "share_capital",
            "dividends",
            "prices_unadjusted",
            "index_daily",
            "factor_metadata",
        ],
    }


def factor_root(run_id: str, derived_dir: Path | None = None) -> Path:
    return Path(derived_dir or DATA_DIR / "derived") / run_id / "factors"


def load_factor_manifest(run_id: str, derived_dir: Path | None = None) -> dict:
    path = factor_root(run_id, derived_dir) / "factor_manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到因子面板: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def factor_file(run_id: str, relative_path: str, derived_dir: Path | None = None) -> Path:
    root = factor_root(run_id, derived_dir).resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents:
        raise ValueError("因子文件路径越界")
    if not path.is_file():
        raise FileNotFoundError(f"因子文件不存在: {relative_path}")
    return path


def build_factor_panel(
    raw_run_id: str,
    fund_id: str = "",
    store: RawRunStore | None = None,
    derived_dir: Path | None = None,
    rebuild: bool = False,
    symbols: list[str] | None = None,
) -> dict:
    """Build the factor panel and its coverage/duckdb companions."""
    store = store or RawRunStore()
    manifest = store.load_run(raw_run_id)
    if not fund_id:
        latest = latest_fundamental_for_run(raw_run_id)
        if not latest:
            raise FileNotFoundError(
                f"快照 {raw_run_id} 尚未采集基本面，请先运行 collect_fundamentals.py"
            )
        fund_id = latest["fund_id"]
    fund_manifest = load_fundamental_manifest(fund_id)
    fund_dir = Path(fund_manifest.get("manifest_path", "")).parent
    if not fund_dir.exists():
        fund_dir = DATA_DIR / "fundamentals" / fund_id

    target = factor_root(raw_run_id, derived_dir)
    manifest_path = target / "factor_manifest.json"
    if manifest_path.exists() and not rebuild:
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    staging = target.parent / f".factors-{uuid.uuid4().hex[:8]}.tmp"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        daily_path = store.file_for_view(raw_run_id, "daily")
        daily = pd.read_csv(
            daily_path,
            dtype={"symbol": str},
            parse_dates=["date"],
            usecols=[
                "date",
                "symbol",
                "name",
                "industry",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover",
            ],
        )
        daily["symbol"] = daily["symbol"].astype(str).str.zfill(6)
        if symbols:
            wanted = {str(item).zfill(6) for item in symbols}
            daily = daily[daily["symbol"].isin(wanted)].copy()
        daily = daily.sort_values(["symbol", "date"])

        profiles = pd.read_csv(
            fund_dir / "profiles.csv",
            dtype={"symbol": str},
        )
        profiles["symbol"] = profiles["symbol"].astype(str).str.zfill(6)
        profile_map = profiles.drop_duplicates("symbol").set_index("symbol").to_dict("index")
        industry_by_symbol = {
            code: str(item.get("industry") or "")
            for code, item in profile_map.items()
        }

        raw_financials = pd.read_csv(
            fund_dir / "financials.csv",
            dtype={"symbol": str},
        )
        missing_ann_date_rows = int(raw_financials["ann_date"].isna().sum())
        financials = prepare_financials(
            raw_financials, industry_by_symbol=industry_by_symbol
        )
        if symbols:
            financials = financials[financials["symbol"].isin(wanted)]
        financial_map = {
            code: group for code, group in financials.groupby("symbol", sort=False)
        }

        share_capital = pd.read_csv(
            fund_dir / "share_capital.csv",
            dtype={"symbol": str},
            parse_dates=["end_date", "ann_date", "listing_date"],
        )
        share_capital["symbol"] = share_capital["symbol"].astype(str).str.zfill(6)
        share_capital["end_date"] = share_capital["end_date"].dt.strftime("%Y-%m-%d")
        share_capital["ann_date"] = share_capital["ann_date"].dt.strftime("%Y-%m-%d")
        capital_map = {
            code: group for code, group in share_capital.groupby("symbol", sort=False)
        }

        dividends = pd.read_csv(
            fund_dir / "dividends.csv",
            dtype={"symbol": str},
        )
        dividends["symbol"] = dividends["symbol"].astype(str).str.zfill(6)
        dividends["cash_per_share"] = pd.to_numeric(
            dividends["cash_per_share"], errors="coerce"
        )
        dividends = dividends[
            dividends["status"].astype(str).str.contains("实施", na=False)
        ]
        dividend_map = {
            code: group for code, group in dividends.groupby("symbol", sort=False)
        }

        prices = pd.read_csv(
            fund_dir / "prices_unadjusted.csv",
            dtype={"symbol": str},
            parse_dates=["date"],
            usecols=["date", "symbol", "close_raw", "source", "fetched_at"],
        )
        prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
        prices["date"] = prices["date"].dt.strftime("%Y-%m-%d")
        if symbols:
            prices = prices[prices["symbol"].isin(wanted)]
        price_map = {code: group for code, group in prices.groupby("symbol", sort=False)}

        index_path = fund_dir / "index_daily.csv"
        if not index_path.exists():
            raise FileNotFoundError("基本面快照缺少 index_daily.csv")
        index_daily = pd.read_csv(index_path, parse_dates=["date"])
        index_daily["mkt_ret"] = pd.to_numeric(
            index_daily["close_raw"], errors="coerce"
        ).pct_change(fill_method=None)
        index_returns = index_daily[["date", "mkt_ret"]].dropna()

        panel_root = staging / "factor_panel"
        panel_root.mkdir(parents=True, exist_ok=True)
        schema = _final_schema()
        writers: dict[int, pq.ParquetWriter] = {}
        try:
            for code, group in daily.groupby("symbol", sort=False):
                frame = _build_symbol_frame(
                    group,
                    profile_map.get(code, {}),
                    financial_map.get(code, pd.DataFrame()),
                    capital_map.get(code, pd.DataFrame()),
                    dividend_map.get(code, pd.DataFrame()),
                    price_map.get(code, pd.DataFrame()),
                    index_returns,
                )
                _write_factor_rows(frame, writers, schema, panel_root)
        finally:
            for writer in writers.values():
                writer.close()

        panel_glob = str((panel_root / "year=*" / "*.parquet").resolve())
        connection = duckdb.connect()
        try:
            coverage = _coverage(connection, panel_glob)
        finally:
            connection.close()
        coverage_by_symbol_path = staging / "factor_coverage_by_symbol.csv"
        by_symbol = coverage.pop("by_symbol")
        by_symbol.to_csv(
            coverage_by_symbol_path,
            index=False,
            encoding="utf-8-sig",
        )

        financials_path = staging / "financials_quarterly.parquet"
        profiles_path = staging / "profiles.parquet"
        capital_path = staging / "share_capital.parquet"
        dividend_path = staging / "dividends.parquet"
        prices_path = staging / "prices_unadjusted.parquet"
        index_path = staging / "index_daily.parquet"
        _write_parquet(financials, financials_path)
        _write_parquet(profiles, profiles_path)
        _write_parquet(share_capital, capital_path)
        _write_parquet(dividends, dividend_path)
        _write_parquet(prices, prices_path)
        _write_parquet(index_daily, index_path)

        dictionary = pd.DataFrame(
            [
                {
                    "field": name,
                    "group": group,
                    "group_label": FACTOR_GROUPS[group],
                    "label": label,
                    "formula": formula,
                    "meaning": FACTOR_GUIDANCE[name][0],
                    "usage": FACTOR_GUIDANCE[name][1],
                    "unit": unit,
                    "as_of_rule": as_of,
                    "source": "QuantGuard factor panel",
                }
                for name, group, label, formula, unit, as_of in FACTOR_DEFINITIONS
            ]
        )
        dictionary_path = staging / "factor_dictionary.csv"
        _write_table_csv(dictionary, dictionary_path)

        coverage_path = staging / "factor_coverage.json"
        _json_write(coverage_path, coverage)

        payload = {
            "schema_version": FACTOR_VERSION,
            "raw_run_id": raw_run_id,
            "fund_id": fund_id,
            "generated_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
            "request": {
                "symbols": coverage["summary"]["symbols"],
                "factor_fields": len(FACTOR_FIELDS),
                "panel_fields": len(PANEL_FIELDS),
            },
            "summary": coverage["summary"],
            "by_year": coverage["by_year"],
            "by_factor": coverage["by_factor"],
            "by_base_field": coverage["by_base_field"],
            "quality": {
                "requested_factor_count": len(FACTOR_FIELDS),
                "financial_rows_without_ann_date_excluded": missing_ann_date_rows,
                "financial_template_rule": "申万一级行业属于银行或非银金融",
            },
            "source_summary": {
                "unadjusted_price_rows_by_source": (
                    prices["source"].dropna().astype(str).value_counts().to_dict()
                    if "source" in prices.columns
                    else {}
                ),
            },
            "files": {},
            "duckdb": {},
            "limitations": [
                "财务因子使用公告日 <= 交易日的最近一期报告，避免未来函数。",
                "TTM由累计报表差分并结合上年同期计算；银行、保险等模板的毛利率可能为空。",
                "总市值与流通市值使用不复权收盘价乘对应生效日股本，收益率使用前复权价格。",
                "股票池仍是抓取时点流动性样本，存在幸存者偏差。",
                "分红因子仅使用已实施且除息日已到的税前现金分红。",
            ],
        }
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                relative = path.relative_to(staging).as_posix()
                payload["files"][relative] = {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
        payload["duckdb"] = _write_duckdb(staging, panel_glob, payload)
        payload["files"][payload["duckdb"]["path"]] = {
            "path": payload["duckdb"]["path"],
            "bytes": payload["duckdb"]["bytes"],
            "sha256": payload["duckdb"]["sha256"],
        }
        _json_write(staging / "factor_manifest.json", payload)

        if target.exists():
            if not rebuild:
                raise FileExistsError(f"因子面板已存在: {target}")
            previous = target.parent / f".factors-previous-{uuid.uuid4().hex[:8]}"
            target.rename(previous)
            try:
                staging.rename(target)
            except Exception:
                previous.rename(target)
                raise
            else:
                shutil.rmtree(previous, ignore_errors=True)
        else:
            staging.rename(target)
        result = json.loads((target / "factor_manifest.json").read_text(encoding="utf-8"))
        result["manifest_path"] = str(target / "factor_manifest.json")
        return result
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def factor_summary_for_api(run_id: str, derived_dir: Path | None = None) -> dict:
    manifest = load_factor_manifest(run_id, derived_dir=derived_dir)
    return {
        "raw_run_id": manifest["raw_run_id"],
        "fund_id": manifest["fund_id"],
        "generated_at": manifest["generated_at"],
        "summary": manifest["summary"],
        "by_year": manifest["by_year"],
        "by_factor": manifest["by_factor"],
        "by_base_field": manifest.get("by_base_field") or {},
        "quality": manifest.get("quality") or {},
        "source_summary": manifest.get("source_summary") or {},
        "duckdb": manifest.get("duckdb") or {},
        "files": manifest.get("files") or {},
    }


def factor_sample(
    run_id: str,
    limit: int = 20,
    query: str = "",
    derived_dir: Path | None = None,
) -> dict:
    """Return a bounded latest cross-section for visual research inspection."""
    limit = max(1, min(int(limit), 50))
    root = factor_root(run_id, derived_dir)
    database = root / "factors.duckdb"
    if not database.exists():
        raise FileNotFoundError(f"未找到因子数据库: {run_id}")
    columns = ["date", "symbol", "name", "industry"] + FACTOR_FIELDS + [
        "report_date",
        "ann_date",
        "factor_ready",
        "missing_groups",
    ]
    where = ["date = (SELECT MAX(date) FROM factor_panel)"]
    params: list[Any] = []
    keyword = str(query or "").strip()
    if keyword:
        where.append("(symbol LIKE ? OR name LIKE ? OR industry LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like])
    params.append(limit)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        result = connection.execute(
            f"SELECT {', '.join(columns)} FROM factor_panel " 
            f"WHERE {' AND '.join(where)} " 
            "ORDER BY factor_ready DESC, symbol LIMIT ?",
            params,
        )
        names = [item[0] for item in result.description]
        raw_rows = result.fetchall()
    finally:
        connection.close()
    rows = []
    for raw in raw_rows:
        row = {}
        for name, value in zip(names, raw):
            if value is None or (not isinstance(value, str) and pd.isna(value)):
                row[name] = None
            elif hasattr(value, "item"):
                row[name] = value.item()
            else:
                row[name] = value
        rows.append(row)
    return {
        "raw_run_id": run_id,
        "latest_date": rows[0]["date"] if rows else None,
        "rows": rows,
        "columns": columns,
    }
