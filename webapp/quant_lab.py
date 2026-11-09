"""Personal-account cross-sectional strategy research for the web workbench."""

from __future__ import annotations

import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Optional

import numpy as np
import pandas as pd

from config.settings import CostConfig
from tools.performance_tool import compute_annual_turnover, compute_metrics
from webapp import demo_data, market_client
from webapp.ml_research import MODEL_ALIASES, MODEL_LABELS, run_ml_pipeline
from webapp.ml_research import FEATURE_DEFINITIONS, FEATURE_NAMES

UniverseLoader = Callable[[int], list[dict]]
HistoryLoader = Callable[[str, int], list[dict]]

MODEL_CONFIGS = {
    "balanced": {
        "label": "趋势均衡",
        "weights": {"momentum_20": 0.35, "momentum_60": 0.25, "low_vol": 0.20, "liquidity": 0.20},
    },
    "momentum": {
        "label": "强动量",
        "weights": {"momentum_20": 0.50, "momentum_60": 0.30, "low_vol": 0.05, "liquidity": 0.15},
    },
    "defensive": {
        "label": "低波防御",
        "weights": {"momentum_20": 0.15, "momentum_60": 0.20, "low_vol": 0.45, "liquidity": 0.20},
    },
}

REBALANCE_CONFIGS = {"weekly": "W", "monthly": "ME"}

_DATASET_CACHE: dict[str, dict] = {}
_DATASET_ORDER: list[str] = []
_DATASET_LOCK = threading.Lock()
_MAX_DATASET_SNAPSHOTS = 3




def _dataset_column(key: str, label: str, kind: str) -> dict:
    return {"key": key, "label": label, "kind": kind}


def _json_safe(value):
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def _frame_records(frame: pd.DataFrame) -> list[dict]:
    columns = [str(column) for column in frame.columns]
    return [
        {column: _json_safe(value) for column, value in zip(columns, row)}
        for row in frame.itertuples(index=False, name=None)
    ]


def _long_market_panel(matrix: pd.DataFrame, value_name: str) -> pd.DataFrame:
    frame = matrix.rename_axis(index="date", columns="symbol").reset_index()
    frame = frame.melt(id_vars="date", var_name="symbol", value_name=value_name)
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame["symbol"] = frame["symbol"].astype(str)
    return frame


def _register_dataset_snapshot(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    volume: pd.DataFrame,
    labeled: pd.DataFrame,
    metadata: dict[str, dict],
    params: dict,
    experiment_id: str,
) -> tuple[str, dict]:
    raw = _long_market_panel(close, "close")
    raw = raw.merge(_long_market_panel(amount, "amount"), on=["date", "symbol"], how="left")
    raw = raw.merge(_long_market_panel(volume, "volume"), on=["date", "symbol"], how="left")
    raw["name"] = raw["symbol"].map(lambda symbol: str(metadata.get(symbol, {}).get("name") or symbol))
    raw["industry"] = raw["symbol"].map(lambda symbol: str(metadata.get(symbol, {}).get("industry") or "未分类"))
    raw = raw[["date", "symbol", "name", "industry", "close", "amount", "volume"]]
    raw = raw.sort_values(
        ["date", "symbol"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)

    model = labeled.copy()
    model["date"] = pd.to_datetime(model["date"]).dt.strftime("%Y-%m-%d")
    model["label_end_date"] = pd.to_datetime(model["label_end_date"]).dt.strftime("%Y-%m-%d")
    model["symbol"] = model["symbol"].astype(str)
    model["name"] = model["symbol"].map(lambda symbol: str(metadata.get(symbol, {}).get("name") or symbol))
    model_columns = ["date", "symbol", "name", "industry", *FEATURE_NAMES, "future_excess", "label_end_date"]
    model = model[[column for column in model_columns if column in model.columns]]
    model = model.sort_values(
        ["date", "symbol"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)

    raw_columns = [
        _dataset_column("date", "日期", "date"),
        _dataset_column("symbol", "代码", "text"),
        _dataset_column("name", "股票", "text"),
        _dataset_column("industry", "行业", "text"),
        _dataset_column("close", "收盘价", "decimal"),
        _dataset_column("amount", "成交额", "decimal"),
        _dataset_column("volume", "成交量", "integer"),
    ]
    feature_labels = {name: label for name, label, _formula, _category in FEATURE_DEFINITIONS}
    model_display_columns = [
        _dataset_column("date", "信号日", "date"),
        _dataset_column("symbol", "代码", "text"),
        _dataset_column("name", "股票", "text"),
        _dataset_column("industry", "行业", "text"),
        *[_dataset_column(name, feature_labels[name], "decimal") for name in FEATURE_NAMES],
        _dataset_column("future_excess", "未来超额", "percent"),
        _dataset_column("label_end_date", "标签结束日", "date"),
    ]
    model_display_columns = [column for column in model_display_columns if column["key"] in model.columns]

    snapshot_id = f"DATA-{uuid.uuid4().hex[:12].upper()}"
    snapshot = {
        "id": snapshot_id,
        "experiment_id": experiment_id,
        "data_mode": params["data_mode"],
        "market_data_as_of": close.index[-1].strftime("%Y-%m-%d"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "views": {
            "raw": {
                "title": "原始行情面板",
                "frame": raw,
                "columns": raw_columns,
            },
            "model": {
                "title": "建模样本矩阵",
                "frame": model,
                "columns": model_display_columns,
            },
        },
    }
    with _DATASET_LOCK:
        _DATASET_CACHE[snapshot_id] = snapshot
        _DATASET_ORDER.append(snapshot_id)
        while len(_DATASET_ORDER) > _MAX_DATASET_SNAPSHOTS:
            expired = _DATASET_ORDER.pop(0)
            _DATASET_CACHE.pop(expired, None)
    summary = {
        "dataset_id": snapshot_id,
        "raw_rows": int(len(raw)),
        "model_rows": int(len(model)),
    }
    return snapshot_id, summary


def get_research_dataset(
    dataset_id: str,
    view: str = "raw",
    offset: int = 0,
    limit: int = 50,
    query: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    if view not in {"raw", "model"}:
        raise ValueError("dataset view 只能是 raw 或 model")
    if offset < 0:
        raise ValueError("offset 必须是非负整数")
    if not 1 <= limit <= 500:
        raise ValueError("limit 必须在 1 到 500 之间")
    query = str(query or "").strip()
    date_from = str(date_from or "").strip()
    date_to = str(date_to or "").strip()
    if len(query) > 50:
        raise ValueError("数据筛选词不能超过 50 个字符")
    parsed_dates = []
    for value, label in ((date_from, "开始日期"), (date_to, "结束日期")):
        if not value:
            parsed_dates.append(None)
            continue
        try:
            parsed_dates.append(pd.Timestamp(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}格式必须为 YYYY-MM-DD") from exc
    start_date, end_date = parsed_dates
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    with _DATASET_LOCK:
        snapshot = _DATASET_CACHE.get(str(dataset_id))
        if snapshot is None:
            raise KeyError("数据底稿不存在或服务已重启，请重新运行研究")
        if str(dataset_id) in _DATASET_ORDER:
            _DATASET_ORDER.remove(str(dataset_id))
            _DATASET_ORDER.append(str(dataset_id))
        selected = snapshot["views"][view]
        frame = selected["frame"]
        source_total = int(len(frame))
        if query:
            searchable = (
                frame["symbol"].astype(str)
                + " " + frame["name"].astype(str)
                + " " + frame["industry"].astype(str)
            ).str.casefold()
            frame = frame.loc[searchable.str.contains(query.casefold(), regex=False)]
        if start_date is not None:
            frame = frame.loc[pd.to_datetime(frame["date"]) >= start_date]
        if end_date is not None:
            frame = frame.loc[pd.to_datetime(frame["date"]) <= end_date]
        total = int(len(frame))
        if offset > 0 and offset >= total:
            raise ValueError(f"offset 超出数据范围: 共 {total} 行")
        page = frame.iloc[offset : offset + limit]
        rows = _frame_records(page)
        columns = [dict(column) for column in selected["columns"]]
        return {
            "dataset_id": snapshot["id"],
            "experiment_id": snapshot["experiment_id"],
            "data_mode": snapshot["data_mode"],
            "market_data_as_of": snapshot["market_data_as_of"],
            "generated_at": snapshot["generated_at"],
            "view": view,
            "title": selected["title"],
            "columns": columns,
            "rows": rows,
            "filters": {
                "query": query,
                "date_from": date_from,
                "date_to": date_to,
            },
            "total": total,
            "offset": offset,
            "limit": limit,
            "raw_rows": int(len(snapshot["views"]["raw"]["frame"])),
            "model_rows": int(len(snapshot["views"]["model"]["frame"])),
            "source_total": source_total,
        }


def get_research_dataset_csv(dataset_id: str, view: str = "raw") -> dict:
    if view not in {"raw", "model"}:
        raise ValueError("dataset view 只能是 raw 或 model")
    with _DATASET_LOCK:
        snapshot = _DATASET_CACHE.get(str(dataset_id))
        if snapshot is None:
            raise KeyError("数据底稿不存在或服务已重启，请重新运行研究")
        frame = snapshot["views"][view]["frame"]
        label = "原始行情" if view == "raw" else "建模样本"
        filename = f"{snapshot['experiment_id']}_{view}.csv"
        content = frame.to_csv(index=False)
        return {"filename": filename, "label": label, "content": content, "rows": int(len(frame))}


def run_research(
    payload: Optional[dict] = None,
    universe_loader: Optional[UniverseLoader] = None,
    history_loader: Optional[HistoryLoader] = None,
) -> dict:
    """Run the data-audit, ML, cost-aware backtest and target-portfolio pipeline."""
    params = _parameters(payload)
    if params["data_mode"] == "demo":
        universe_loader = demo_data.get_liquid_universe
        history_loader = demo_data.get_history
    else:
        universe_loader = universe_loader or market_client.get_liquid_universe
        history_loader = history_loader or market_client.get_history
    try:
        items = universe_loader(params["universe_size"])
    except market_client.MarketDataError:
        raise
    except Exception as exc:
        raise ValueError(f"市场扫描失败: {exc}") from exc
    if len(items) < params["top_n"]:
        raise ValueError("市场扫描返回的股票数量不足")

    close, amount, volume, metadata, quality = _load_panels_v2(
        items, params["days"], history_loader
    )
    modeling = run_ml_pipeline(close, amount, volume, metadata, params)
    features = modeling["features"]
    score_panel = modeling["score_panel"]
    gross_nav, net_nav, benchmark_nav, weights, rebalances, cost_totals = _simulate_v2(
        close, amount, features, score_panel, metadata, params
    )
    training = modeling["training"]
    dev_end = pd.Timestamp(training["development_end"])
    holdout_start = pd.Timestamp(training["holdout_start"])
    full_start = net_nav.index[0]
    full_end = net_nav.index[-1]
    performance = {
        "full": _period_metrics(
            gross_nav, net_nav, benchmark_nav, rebalances, full_start, full_end, params["capital"]
        ),
        "development": _period_metrics(
            gross_nav, net_nav, benchmark_nav, rebalances, full_start, min(dev_end, full_end), params["capital"]
        ),
        "holdout": _period_metrics(
            gross_nav, net_nav, benchmark_nav, rebalances, max(holdout_start, full_start), full_end, params["capital"]
        ),
    }
    full_metrics = performance["full"]
    selected_row = next(
        (row for row in training["model_comparison"] if row.get("selected")),
        training["model_comparison"][0],
    )
    current_score = modeling["current_score"]
    latest_date = modeling["latest_date"]
    score_frame = pd.DataFrame(
        np.nan, index=[latest_date], columns=close.columns
    )
    score_frame.loc[latest_date, current_score.index] = current_score.to_numpy()
    analysis_factors = {
        "score": score_frame,
        "momentum_20": features["momentum_20"].loc[[latest_date]],
        "momentum_60": features["momentum_60"].loc[[latest_date]],
        "volatility_20": features["raw_volatility_20"].loc[[latest_date]],
        "average_amount": features["average_amount"].loc[[latest_date]],
    }
    holdings, ranking, capacity, industry_exposure, execution = _current_portfolio_v2(
        close, analysis_factors, metadata, params
    )
    regime = _market_regime(
        close,
        {
            **analysis_factors,
            "momentum_20": features["momentum_20"],
            "volatility_20": features["raw_volatility_20"],
        },
    )
    exploration = _exploration_summary(
        features, modeling["labeled"], close, modeling["feature_catalog"]
    )
    risk_actions = _risk_actions(
        holdings, capacity, industry_exposure, params
    )
    metrics_payload = {
        "annual_return": full_metrics["net_annual_return"],
        "gross_annual_return": full_metrics["gross_annual_return"],
        "net_annual_return": full_metrics["net_annual_return"],
        "annual_volatility": full_metrics["annual_volatility"],
        "sharpe_ratio": full_metrics["sharpe_ratio"],
        "max_drawdown": full_metrics["max_drawdown"],
        "cumulative_return": full_metrics["cumulative_return"],
        "annual_turnover": full_metrics["annual_turnover"],
        "benchmark_annual_return": full_metrics["benchmark_annual_return"],
        "excess_annual_return": full_metrics["net_excess_annual_return"],
        "net_excess_annual_return": full_metrics["net_excess_annual_return"],
        "info_ratio": full_metrics["info_ratio"],
        "annual_cost_drag": full_metrics["annual_cost_drag"],
    }
    latest_weights = weights.iloc[-1]
    accumulation = _accumulation_plan(
        params["capital"],
        params["monthly_contribution"],
        params["horizon_years"],
        metrics_payload["annual_return"],
    )
    validation = selected_row.get("validation") or {}
    holdout_prediction = selected_row.get("holdout") or {}
    summary = {
        **metrics_payload,
        "invested_weight": _finite(float(latest_weights.sum())),
        "latest_cash_weight": _finite(max(0.0, 1.0 - float(latest_weights.sum()))),
        "capacity_utilization": capacity["utilization"],
        "portfolio_capacity": capacity["portfolio_capacity"],
        "model": training["selected_model"],
        "model_label": MODEL_LABELS[training["selected_model"]],
        "rebalance_label": "周频" if params["rebalance"] == "weekly" else "月频",
        "ic_mean": validation.get("rank_ic_mean"),
        "ic_ir": validation.get("icir"),
        "ic_win_rate": validation.get("ic_win_rate"),
        "validation_ic": validation.get("rank_ic_mean"),
        "holdout_ic": holdout_prediction.get("rank_ic_mean"),
        "holdout_r2": holdout_prediction.get("r2_oos"),
        "top_bucket_capture": holdout_prediction.get("top_bucket_capture"),
        "holdout_net_excess": performance["holdout"].get(
            "net_excess_annual_return"
        ),
        "holdout_net_return": performance["holdout"].get(
            "net_annual_return"
        ),
        "data_mode": params["data_mode"],
        "label_horizon": params["label_horizon"],
        "capital": _finite(params["capital"], 2),
        "risk_action_count": len(risk_actions),
        "primary_risk_action": risk_actions[0]["title"] if risk_actions else None,
    }
    scanned_amount = sum(float(item.get("amount") or 0.0) for item in items)
    cost_keys = (
        "commission",
        "minimum_commission_extra",
        "transfer_fee",
        "stamp_tax",
        "slippage",
        "impact",
        "total",
    )
    cost_breakdown = {
        key: {
            "currency": _finite(cost_totals.get(key, 0.0), 2),
            "pct_of_capital": _finite(
                cost_totals.get(key, 0.0) / params["capital"], 6
            ),
        }
        for key in cost_keys
    }
    # Keep the breakdown additive: the minimum-commission surcharge is shown
    # separately, so the commission row contains only the proportional part.
    proportional_commission = max(
        0.0,
        cost_totals.get("commission", 0.0)
        - cost_totals.get("minimum_commission_extra", 0.0),
    )
    cost_breakdown["commission"] = {
        "currency": _finite(proportional_commission, 2),
        "pct_of_capital": _finite(
            proportional_commission / params["capital"], 6
        ),
    }
    cost_breakdown["buy_notional"] = {
        "currency": _finite(cost_totals.get("buy_notional", 0.0), 2),
        "pct_of_capital": _finite(
            cost_totals.get("buy_notional", 0.0) / params["capital"], 6
        ),
    }
    cost_breakdown["sell_notional"] = {
        "currency": _finite(cost_totals.get("sell_notional", 0.0), 2),
        "pct_of_capital": _finite(
            cost_totals.get("sell_notional", 0.0) / params["capital"], 6
        ),
    }
    model_comparison = []
    for row in training["model_comparison"]:
        holdout_metrics = row.get("holdout") or {}
        validation_metrics = row.get("validation") or {}
        model_comparison.append(
            {
                **row,
                "validation_ic": validation_metrics.get("rank_ic_mean"),
                "holdout_ic": holdout_metrics.get("rank_ic_mean"),
                "holdout_r2": holdout_metrics.get("r2_oos"),
                "top_bucket_capture": holdout_metrics.get("top_bucket_capture"),
            }
        )
    if params["data_mode"] == "demo":
        source = "QuantGuard 内置确定性演示数据"
    else:
        live_sources = sorted(
            {
                str(item.get("source"))
                for item in items
                if item.get("source")
            }
            | set(quality.get("history_sources") or [])
        )
        source = " + ".join(live_sources) if live_sources else "东方财富公开行情"
    experiment_id = datetime.now().strftime("QG-%Y%m%d-%H%M%S")
    dataset_id, dataset_summary = _register_dataset_snapshot(
        close, amount, volume, modeling["labeled"], metadata, params, experiment_id
    )
    report_limitations = [
        "当前股票池按当前流动性筛选，存在幸存者偏差。",
        "行业分类不是严格历史时点快照，标签窗口重叠会降低样本独立性。",
        "小资金整手约束、冲击成本和真实成交之间仍可能有差异。",
    ]
    report_limitations.append(
        "演示数据只验证研究流程可复现，不代表真实市场规律。"
        if params["data_mode"] == "demo"
        else "公开行情接口可能存在延迟；腾讯或新浪历史成交额按成交量 × 收盘价估算，不等同于交易所披露成交额。"
    )
    return {
        "summary": summary,
        "metrics": metrics_payload,
        "performance": performance,
        "training": training,
        "feature_catalog": modeling["feature_catalog"],
        "exploration": exploration,
        "data_quality": {
            **quality,
            "labeled_rows": int(len(modeling["labeled"])),
            "latest_prediction_date": modeling["latest_date"].strftime("%Y-%m-%d"),
            "valid_feature_rows": int(len(modeling["feature_rows"])),
            "feature_missing_pct": {
                item["feature"]: item["missing_pct"]
                for item in exploration["feature_missing_top"]
            },
        },
        "cost_breakdown": cost_breakdown,
        "capacity": capacity,
        "risk": {
            "industry_exposure": industry_exposure,
            "capacity": capacity,
            "execution": execution,
            "actions": risk_actions,
            "stress": capacity.get("stress", []),
        },
        "execution": execution,
        "holdings": holdings,
        "factors": ranking[: min(15, len(ranking))],
        "industry_exposure": industry_exposure,
        "factor_validation": {
            "ic_mean": validation.get("rank_ic_mean"),
            "ic_ir": validation.get("icir"),
            "ic_win_rate": validation.get("ic_win_rate"),
            "samples": validation.get("dates", 0),
            "horizon_days": params["label_horizon"],
            "status": validation.get("status", "样本不足"),
        },
        "regime": regime,
        "model_comparison": model_comparison,
        "accumulation": accumulation,
        "nav": _curve(net_nav, benchmark_nav),
        "rebalances": rebalances,
        "report_context": {
            "research_question": (
                f"机器学习模型能否预测未来 {params['label_horizon']} 个交易日的横截面相对收益，"
                "并在计入 A 股交易约束后形成正的样本外净超额？"
            ),
            "label_definition": (
                "future_excess = close[t+h] / close[t] - 1 - 当日股票池等权未来收益",
            ),
            "time_split": {
                "development": [
                    training["development_start"],
                    training["development_end"],
                ],
                "holdout": [
                    training["holdout_start"],
                    training["holdout_end"],
                ],
            },
            "leakage_controls": training["leakage_checks"],
            "limitations": report_limitations,
            "disclaimer": "仅用于课程研究和个人研究，不构成投资建议或收益承诺。",
        },
        "meta": {
            "experiment_id": experiment_id,
            "dataset_id": dataset_id,
            "dataset_rows": dataset_summary,
            "data_mode": params["data_mode"],
            "demo_version": demo_data.DEMO_VERSION if params["data_mode"] == "demo" else None,
            "capital": _finite(params["capital"], 2),
            "universe_size_requested": params["universe_size"],
            "universe_size_loaded": int(close.shape[1]),
            "history_days": int(close.shape[0]),
            "top_n": params["top_n"],
            "model": params["model"],
            "selected_model": training["selected_model"],
            "model_label": MODEL_LABELS[training["selected_model"]],
            "label_horizon": params["label_horizon"],
            "rebalance": params["rebalance"],
            "exit_days": params["exit_days"],
            "participation_rate": params["participation_rate"],
            "industry_cap": params["industry_cap"],
            "monthly_contribution": params["monthly_contribution"],
            "horizon_years": params["horizon_years"],
            "scanned_amount": _finite(scanned_amount, 2),
            "source": source,
            "market_data_as_of": close.index[-1].strftime("%Y-%m-%d"),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "parameter_summary": {
                key: params[key] for key in (
                    "capital", "universe_size", "top_n", "days", "model",
                    "factor_variant", "data_mode", "label_horizon", "rebalance",
                    "exit_days", "participation_rate", "industry_cap",
                    "monthly_contribution", "horizon_years"
                )
            },
            "caveat": "研究结果受股票池、历史窗口、标签重叠和交易假设影响，不构成收益承诺或投资建议。",
        },
    }


def _number(payload: dict, key: str, default: float) -> float:
    try:
        value = float(payload.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是数字") from exc
    if not math.isfinite(value):
        raise ValueError(f"{key} 必须是有限数字")
    return value


def _parameters(payload: Optional[dict]) -> dict:
    payload = payload or {}
    capital = _number(payload, "capital", 50_000.0)
    universe_size = int(_number(payload, "universe_size", 120))
    top_n = int(_number(payload, "top_n", 8))
    days = int(_number(payload, "days", 740))
    model = str(payload.get("model") or "auto")
    data_mode = str(payload.get("data_mode") or "live").lower()
    label_horizon = int(_number(payload, "label_horizon", 20))
    rebalance = str(payload.get("rebalance") or "monthly")
    exit_days = _number(payload, "exit_days", 5.0)
    participation_rate = _number(payload, "participation_rate", 0.05)
    industry_cap = _number(payload, "industry_cap", 0.22)
    monthly_contribution = _number(payload, "monthly_contribution", 2_000.0)
    horizon_years = int(_number(payload, "horizon_years", 3))

    if not 0.0 <= monthly_contribution <= 100_000.0:
        raise ValueError("每月投入必须在 0 元到 10 万元之间")
    if not 1 <= horizon_years <= 10:
        raise ValueError("积累年限必须在 1 到 10 年之间")
    if not 1_000 <= capital <= 500_000_000_000:
        raise ValueError("资金规模必须在 1000 元到 5000 亿元之间")
    if not 10 <= universe_size <= 200:
        raise ValueError("股票池数量必须在 10 到 200 之间")
    if not 3 <= top_n <= min(20, universe_size):
        raise ValueError("目标持仓数必须为 3 到 20，且不超过股票池数量")
    if not 180 <= days <= 1000:
        raise ValueError("历史窗口必须在 180 到 1000 个交易日之间")
    if model not in MODEL_ALIASES:
        raise ValueError(f"未知模型 {model}")
    if data_mode not in {"live", "demo"}:
        raise ValueError("data_mode 只能是 live 或 demo")
    if label_horizon not in {5, 10, 20}:
        raise ValueError("标签期限只能是 5、10 或 20 个交易日")
    if rebalance not in REBALANCE_CONFIGS:
        raise ValueError(f"未知调仓频率 {rebalance}")
    if not 1 <= exit_days <= 20:
        raise ValueError("清仓天数必须在 1 到 20 之间")
    if not 0.01 <= participation_rate <= 0.30:
        raise ValueError("ADV 参与率必须在 1% 到 30% 之间")
    if not 0.10 <= industry_cap <= 0.50:
        raise ValueError("行业上限必须在 10% 到 50% 之间")
    return {
        "capital": capital,
        "universe_size": universe_size,
        "top_n": top_n,
        "days": days,
        "model": model,
        "factor_variant": model if model in MODEL_CONFIGS else "balanced",
        "data_mode": data_mode,
        "label_horizon": label_horizon,
        "rebalance": rebalance,
        "exit_days": exit_days,
        "participation_rate": participation_rate,
        "industry_cap": industry_cap,
        "monthly_contribution": monthly_contribution,
        "horizon_years": horizon_years,
    }


def _cross_sectional_zscore(row: pd.Series) -> pd.Series:
    values = row.dropna()
    if len(values) < 3:
        return pd.Series(np.nan, index=row.index)
    std = float(values.std(ddof=0))
    if not math.isfinite(std) or std <= 1e-12:
        return pd.Series(0.0, index=row.index)
    mean = float(values.mean())
    clipped = values.clip(mean - 3.0 * std, mean + 3.0 * std)
    return ((clipped - mean) / std).reindex(row.index)


def _load_panels_v2(
    items: list[dict],
    days: int,
    history_loader: HistoryLoader,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, dict], dict]:
    metadata = {str(item["symbol"]): item for item in items}
    histories: dict[str, list[dict]] = {}
    failures: list[dict] = []
    workers = min(12, max(1, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(history_loader, str(item["symbol"]), days): str(item["symbol"])
            for item in items
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except Exception as exc:
                failures.append({"symbol": symbol, "error": str(exc)})

    records = []
    history_sources: set[str] = set()
    invalid_price_records = 0
    malformed_records = 0
    for symbol, history in histories.items():
        for row in history or []:
            if row.get("source"):
                history_sources.add(str(row["source"]))
            try:
                close = float(row["close"])
                amount = float(row.get("amount") or 0.0)
                volume = float(row.get("volume") or 0.0)
                date = pd.Timestamp(row["date"])
            except (KeyError, TypeError, ValueError):
                malformed_records += 1
                continue
            if close <= 0 or not math.isfinite(close):
                invalid_price_records += 1
                continue
            records.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "close": close,
                    "amount": amount if amount > 0 else np.nan,
                    "volume": volume if volume > 0 else np.nan,
                }
            )
    if not records:
        raise ValueError("未能获取股票池历史行情")

    raw = pd.DataFrame(records)
    duplicate_count = int(raw.duplicated(["date", "symbol"]).sum())
    panel = raw.drop_duplicates(["date", "symbol"], keep="last")
    close = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    amount = panel.pivot(index="date", columns="symbol", values="amount").sort_index()
    volume = panel.pivot(index="date", columns="symbol", values="volume").sort_index()
    enough = close.notna().sum() >= min(120, max(60, days // 3))
    excluded = [str(symbol) for symbol in close.columns[~enough]]
    close = close.loc[:, enough]
    amount = amount.reindex(columns=close.columns)
    volume = volume.reindex(columns=close.columns)
    if len(close.columns) < 3 or len(close) < 140:
        raise ValueError("有效历史行情不足，无法运行横截面机器学习研究")
    missing_cells_before_fill = int(close.isna().sum().sum())
    close = close.ffill(limit=5)
    amount = amount.ffill(limit=5)
    volume = volume.ffill(limit=5)
    pooled_returns = close.pct_change(fill_method=None)
    quality = {
        "requested_symbols": len(items),
        "loaded_symbols": int(close.shape[1]),
        "trading_days": int(close.shape[0]),
        "date_start": close.index[0].strftime("%Y-%m-%d"),
        "date_end": close.index[-1].strftime("%Y-%m-%d"),
        "duplicate_records": duplicate_count,
        "invalid_price_records": invalid_price_records,
        "malformed_records": malformed_records,
        "forward_filled_cells": missing_cells_before_fill,
        "extreme_return_records": int(
            (pooled_returns.abs() > 0.20).sum().sum()
        ),
        "failed_symbols": failures[:20],
        "history_sources": sorted(history_sources),
        "excluded_short_history": excluded[:20],
        "excluded_short_history_count": len(excluded),
        "missing_close_pct": _finite(float(close.isna().mean().mean()), 6),
        "missing_amount_pct": _finite(float(amount.isna().mean().mean()), 6),
        "missing_volume_pct": _finite(float(volume.isna().mean().mean()), 6),
        "industry_counts": pd.Series(
            [str(metadata.get(symbol, {}).get("industry") or "未分类") for symbol in close.columns]
        ).value_counts().to_dict(),
        "cleaning_rules": [
            "删除重复的 date + symbol，保留最后一条",
            "价格必须为正数，成交额和成交量缺失不伪造",
            "单只股票历史不足时整只排除",
            "时间序列缺口最多前向填充 5 个交易日",
            "特征在当日截面做 Z-Score 并截断到 [-3, 3]",
        ],
    }
    return close, amount, volume, metadata, quality


def _distribution(values: pd.Series) -> dict:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"samples": 0}
    return {
        "samples": int(len(clean)),
        "mean": _finite(float(clean.mean()), 6),
        "std": _finite(float(clean.std(ddof=1)), 6),
        "p05": _finite(float(clean.quantile(0.05)), 6),
        "median": _finite(float(clean.median()), 6),
        "p95": _finite(float(clean.quantile(0.95)), 6),
        "positive_share": _finite(float((clean > 0).mean()), 6),
        "extreme_share": _finite(float((clean.abs() > 0.20).mean()), 6),
    }


def _exploration_summary(
    features: dict[str, pd.DataFrame],
    labeled: pd.DataFrame,
    close: pd.DataFrame,
    catalog: list[dict],
) -> dict:
    missing = []
    for item in catalog:
        name = item["name"]
        missing.append(
            {
                "feature": name,
                "label": item["label"],
                "missing_pct": _finite(float(features[name].isna().mean().mean()), 6),
            }
        )
    missing.sort(key=lambda row: row["missing_pct"] or 0.0, reverse=True)

    latest = features[catalog[0]["name"]].index.max()
    latest_features = pd.DataFrame(
        {item["name"]: features[item["name"]].loc[latest] for item in catalog}
    )
    correlations = latest_features.corr(method="spearman")
    pairs = []
    names = list(correlations.columns)
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            value = correlations.loc[left, right]
            if pd.notna(value) and math.isfinite(float(value)):
                pairs.append(
                    {
                        "left": left,
                        "right": right,
                        "correlation": _finite(float(value), 4),
                    }
                )
    pairs.sort(key=lambda row: abs(row["correlation"] or 0.0), reverse=True)

    market_groups = []
    if "market_return_20" in labeled.columns:
        for label, mask in (
            ("上涨市场", labeled["market_return_20"] > 0),
            ("下跌市场", labeled["market_return_20"] <= 0),
        ):
            group = labeled.loc[mask, "future_excess"]
            market_groups.append(
                {
                    "label": label,
                    "samples": int(group.notna().sum()),
                    "mean_future_excess": _finite(float(group.mean()), 6)
                    if group.notna().any()
                    else None,
                }
            )

    daily_return = close.pct_change(fill_method=None).mean(axis=1)
    return {
        "label_distribution": _distribution(labeled["future_excess"]),
        "daily_pool_return": _distribution(daily_return),
        "feature_missing_top": missing[:10],
        "feature_correlations": pairs[:12],
        "market_regime_groups": market_groups,
        "correlation_date": latest.strftime("%Y-%m-%d"),
    }


def _risk_actions(
    holdings: list[dict],
    capacity: dict,
    exposure: list[dict],
    params: dict,
) -> list[dict]:
    actions = []
    if not holdings:
        return [
            {
                "severity": "warning",
                "title": "当前资金无法形成整手组合",
                "action": "提高研究资金或降低单票价格约束后重新计算。",
            }
        ]
    total = sum(float(row.get("weight") or 0.0) for row in holdings)
    if total < 0.70:
        actions.append(
            {
                "severity": "info",
                "title": f"整手取整后仓位为 {total:.1%}",
                "action": "未使用资金保留为现金，不用碎股或虚假成交抬高回测仓位。",
            }
        )
    utilization = capacity.get("utilization")
    if utilization is not None and float(utilization) > 0.8:
        actions.append(
            {
                "severity": "critical" if float(utilization) > 1.0 else "warning",
                "title": f"容量占用 {float(utilization):.1%}",
                "action": "降低资金、提高 ADV 参与率或延长退出窗口，并复核冲击成本。",
            }
        )
    if exposure:
        top = exposure[0]
        actions.append(
            {
                "severity": "warning"
                if float(top["weight"]) > float(params["industry_cap"]) + 1e-9
                else "info",
                "title": f"最大行业暴露 {top['industry']} {float(top['weight']):.1%}",
                "action": f"行业上限为 {params['industry_cap']:.0%}；超出时优先降低同行业低排名持仓。",
            }
        )
    return actions


def _load_panels_legacy(
    items: list[dict],
    days: int,
    history_loader: HistoryLoader,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict]]:
    metadata = {str(item["symbol"]): item for item in items}
    histories: dict[str, list[dict]] = {}
    workers = min(12, max(1, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(history_loader, str(item["symbol"]), days): str(item["symbol"])
            for item in items
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except market_client.MarketDataError:
                continue
            except Exception:
                continue

    records = []
    for symbol, history in histories.items():
        for row in history or []:
            try:
                close = float(row["close"])
                amount = float(row.get("amount") or 0.0)
                date = pd.Timestamp(row["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if close <= 0 or not math.isfinite(close):
                continue
            records.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "close": close,
                    "amount": amount if amount > 0 else np.nan,
                }
            )
    if not records:
        raise ValueError("未能获取股票池历史行情")

    panel = pd.DataFrame(records).drop_duplicates(["date", "symbol"], keep="last")
    close = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    amount = panel.pivot(index="date", columns="symbol", values="amount").sort_index()
    enough = close.notna().sum() >= min(80, max(40, days // 3))
    close = close.loc[:, enough]
    amount = amount.reindex(columns=close.columns)
    if len(close.columns) < 3 or len(close) < 80:
        raise ValueError("有效历史行情不足，无法运行横截面研究")
    close = close.ffill(limit=5)
    amount = amount.ffill(limit=5)
    return close, amount, metadata


def _factor_matrices(close: pd.DataFrame, amount: pd.DataFrame, model: str) -> dict[str, pd.DataFrame]:
    returns = close.pct_change(fill_method=None)
    momentum_20 = close / close.shift(20) - 1.0
    momentum_60 = close / close.shift(60) - 1.0
    volatility_20 = returns.rolling(20, min_periods=15).std(ddof=1) * np.sqrt(252)
    average_amount = amount.rolling(20, min_periods=10).mean()
    liquidity = np.log1p(average_amount.clip(lower=1.0))

    standardized = {
        "momentum_20": momentum_20.apply(_cross_sectional_zscore, axis=1),
        "momentum_60": momentum_60.apply(_cross_sectional_zscore, axis=1),
        "low_vol": (-volatility_20).apply(_cross_sectional_zscore, axis=1),
        "liquidity": liquidity.apply(_cross_sectional_zscore, axis=1),
    }
    config = MODEL_CONFIGS[model]["weights"]
    score = sum(standardized[name] * weight for name, weight in config.items())
    score = score.where(momentum_60.notna() & volatility_20.notna())
    return {
        "score": score,
        "momentum_20": momentum_20,
        "momentum_60": momentum_60,
        "volatility_20": volatility_20,
        "average_amount": average_amount,
    }


def _rebalance_dates(dates: pd.DatetimeIndex, frequency: str) -> list[pd.Timestamp]:
    if frequency == "W":
        groups = pd.Series(dates, index=dates).groupby(dates.to_period("W-FRI")).last()
    else:
        groups = pd.Series(dates, index=dates).groupby(dates.to_period("M")).last()
    usable = [pd.Timestamp(value) for value in groups.values if dates.get_loc(value) >= 60]
    if usable and usable[-1] == dates[-1]:
        usable = usable[:-1]
    return usable


def _capacity_target(
    score_row: pd.Series,
    vol_row: pd.Series,
    adv_row: pd.Series,
    capital: float,
    top_n: int,
    max_single_weight: float,
    participation_rate: float,
    exit_days: float,
    metadata: Optional[dict[str, dict]] = None,
    industry_cap: float = 0.22,
    prices: Optional[pd.Series] = None,
    lot_size: int = 100,
) -> pd.Series:
    candidates = score_row.dropna().index.intersection(vol_row.dropna().index)
    candidates = candidates.intersection(adv_row.dropna().index)
    if len(candidates) == 0:
        return pd.Series(dtype=float)
    liquidity_caps = (adv_row.reindex(candidates) * participation_rate * exit_days / capital).clip(
        lower=0.0, upper=max_single_weight
    )
    if prices is not None:
        price_row = pd.to_numeric(prices.reindex(candidates), errors="coerce")
        min_lot_weight = price_row * lot_size / capital
        affordable = price_row * lot_size <= capital * max_single_weight + 1e-9
        candidates = candidates[
            affordable.fillna(False) & (liquidity_caps >= min_lot_weight).fillna(False)
        ]
    if len(candidates) == 0:
        return pd.Series(dtype=float)
    selected = score_row.reindex(candidates).sort_values(ascending=False).head(top_n).index
    inverse_vol = 1.0 / vol_row.reindex(selected).clip(lower=0.03)
    caps = liquidity_caps.reindex(selected).where(lambda values: values > 0.0001).dropna()
    selected = selected.intersection(caps.index)
    if selected.empty:
        return pd.Series(dtype=float)

    metadata = metadata or {}
    industries = {
        symbol: str(metadata.get(symbol, {}).get("industry") or "未分类")
        for symbol in selected
    }
    group_cap = max(max_single_weight, float(industry_cap))
    raw = inverse_vol.reindex(selected)
    remaining = list(selected)
    budget = 1.0
    allocated: dict[str, float] = {}
    group_used: dict[str, float] = {}

    for _ in range(len(selected) * 5 + 10):
        if not remaining or budget <= 1e-10:
            break
        denominator = float(raw.reindex(remaining).sum())
        if denominator <= 0:
            break
        proposed = raw.reindex(remaining) / denominator * budget

        by_group: dict[str, list[str]] = {}
        for symbol in remaining:
            by_group.setdefault(industries[symbol], []).append(symbol)

        capped_groups: list[str] = []
        for group, members in by_group.items():
            group_sum = float(proposed.reindex(members).sum())
            available = max(0.0, group_cap - group_used.get(group, 0.0))
            if group_sum > available + 1e-12:
                scale = available / group_sum if group_sum > 0 else 0.0
                for symbol in members:
                    proposed[symbol] *= scale
                capped_groups.append(group)

        single_violations = [
            symbol for symbol in remaining if float(proposed[symbol]) > float(caps[symbol]) + 1e-12
        ]
        if single_violations:
            for symbol in single_violations:
                weight = min(float(caps[symbol]), float(proposed[symbol]))
                if weight <= 0:
                    remaining.remove(symbol)
                    continue
                allocated[symbol] = weight
                budget -= weight
                group = industries[symbol]
                group_used[group] = group_used.get(group, 0.0) + weight
                remaining.remove(symbol)
            continue

        if capped_groups:
            for group in capped_groups:
                for symbol in [item for item in remaining if industries[item] == group]:
                    weight = float(proposed[symbol])
                    if weight > 0:
                        allocated[symbol] = weight
                        budget -= weight
                        group_used[group] = group_used.get(group, 0.0) + weight
                    remaining.remove(symbol)
            continue

        for symbol in remaining:
            allocated[symbol] = float(proposed[symbol])
        budget = 0.0
        break
    return pd.Series(allocated, dtype=float).sort_values(ascending=False)


def _round_target_to_lots(
    target: pd.Series,
    prices: pd.Series,
    capital: float,
    lot_size: int = 100,
) -> pd.Series:
    """Convert target weights to whole A-share lots and leave unaffordable cash idle."""
    actual: dict[str, float] = {}
    for symbol, weight in target.items():
        try:
            price = float(prices.get(symbol, 0.0))
        except (TypeError, ValueError):
            continue
        if price <= 0 or weight <= 0:
            continue
        lots = math.floor(float(weight) * capital / (price * lot_size) + 1e-9)
        if lots < 1:
            continue
        actual[symbol] = lots * lot_size * price / capital
    return pd.Series(actual, dtype=float).sort_values(ascending=False)


def _impact_bps(
    weight_delta: float,
    capital: float,
    average_amount: float,
    execution_days: float = 1.0,
) -> float:
    if average_amount <= 0:
        return 100.0
    execution_days = max(float(execution_days), 1.0)
    participation = abs(weight_delta) * capital / (average_amount * execution_days)
    return float(min(100.0, 2.0 + 10.0 * math.sqrt(max(0.0, min(participation, 1.0)))))


def _transaction_cost_details(
    delta: pd.Series,
    average_amount: pd.Series,
    capital: float,
    execution_days: float,
    costs: CostConfig,
) -> dict[str, float]:
    values = {
        "commission": 0.0,
        "minimum_commission_extra": 0.0,
        "transfer_fee": 0.0,
        "stamp_tax": 0.0,
        "slippage": 0.0,
        "impact": 0.0,
        "buy_notional": 0.0,
        "sell_notional": 0.0,
        "total": 0.0,
    }
    if capital <= 0:
        return values
    for symbol, value in delta.items():
        try:
            weight_delta = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(weight_delta) or abs(weight_delta) < 1e-12:
            continue
        notional = abs(weight_delta) * capital
        proportional = notional * costs.commission_rate
        commission = max(costs.min_commission, proportional)
        transfer = notional * costs.transfer_fee_rate
        slippage = notional * costs.slippage_rate
        stamp = notional * costs.stamp_tax_rate if weight_delta < 0 else 0.0
        try:
            amount = float(average_amount.get(symbol, 0.0))
        except (TypeError, ValueError):
            amount = 0.0
        impact = (
            notional * _impact_bps(weight_delta, capital, amount, execution_days) / 10000.0
            if math.isfinite(amount) and amount > 0
            else notional * 0.01
        )
        values["commission"] += commission
        values["minimum_commission_extra"] += commission - proportional
        values["transfer_fee"] += transfer
        values["stamp_tax"] += stamp
        values["slippage"] += slippage
        values["impact"] += impact
        values["total"] += commission + transfer + stamp + slippage + impact
        if weight_delta > 0:
            values["buy_notional"] += notional
        else:
            values["sell_notional"] += notional
    return {key: round(value, 6) for key, value in values.items()}


def _transaction_cost(
    delta: pd.Series,
    average_amount: pd.Series,
    capital: float,
    execution_days: float,
    costs: CostConfig,
) -> tuple[float, float]:
    """Return total cost ratio and the part caused by minimum commissions."""
    if capital <= 0:
        return 0.0, 0.0
    total_cost = 0.0
    minimum_extra = 0.0
    for symbol, value in delta.items():
        try:
            weight_delta = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(weight_delta) or abs(weight_delta) < 1e-12:
            continue
        notional = abs(weight_delta) * capital
        proportional_commission = notional * costs.commission_rate
        commission = max(costs.min_commission, proportional_commission)
        minimum_extra += commission - proportional_commission
        total_cost += commission + notional * (
            costs.transfer_fee_rate + costs.slippage_rate
        )
        if weight_delta < 0:
            total_cost += notional * costs.stamp_tax_rate
        try:
            amount = float(average_amount.get(symbol, 0.0))
        except (TypeError, ValueError):
            amount = 0.0
        if math.isfinite(amount) and amount > 0:
            total_cost += notional * _impact_bps(
                weight_delta, capital, amount, execution_days
            ) / 10000.0
    return total_cost / capital, minimum_extra / capital


def _simulate_v2(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    features: dict[str, pd.DataFrame],
    score_panel: pd.DataFrame,
    metadata: dict[str, dict],
    params: dict,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.DataFrame, list[dict], dict]:
    if score_panel.empty:
        raise ValueError("没有可用的模型预测分数")
    returns = close.pct_change(fill_method=None).fillna(0.0)
    start_date = pd.Timestamp(score_panel.index.min())
    dates = close.index[close.index >= start_date]
    if len(dates) < 2:
        raise ValueError("样本外净值区间不足")
    if params["rebalance"] == "weekly":
        grouped = pd.Series(dates, index=dates).groupby(dates.to_period("W-FRI")).last()
    else:
        grouped = pd.Series(dates, index=dates).groupby(dates.to_period("M")).last()
    available_scores = set(pd.Timestamp(value) for value in score_panel.index)
    rebalance_dates = [
        pd.Timestamp(value)
        for value in grouped.values
        if pd.Timestamp(value) in available_scores
    ]
    if not rebalance_dates:
        raise ValueError("预测区间内没有可用调仓日")

    weights = pd.DataFrame(np.nan, index=dates, columns=close.columns)
    last_weights = pd.Series(0.0, index=close.columns)
    cost_series = pd.Series(0.0, index=dates)
    records = []
    cost_totals = {
        key: 0.0
        for key in (
            "commission",
            "minimum_commission_extra",
            "transfer_fee",
            "stamp_tax",
            "slippage",
            "impact",
            "buy_notional",
            "sell_notional",
            "total",
        )
    }
    costs = CostConfig()
    for date in rebalance_dates:
        target = _capacity_target(
            score_panel.loc[date],
            features["raw_volatility_20"].loc[date],
            features["average_amount"].loc[date],
            params["capital"],
            params["top_n"],
            0.10,
            params["participation_rate"],
            params["exit_days"],
            metadata=metadata,
            industry_cap=params["industry_cap"],
            prices=close.loc[date],
        )
        target = _round_target_to_lots(
            target, close.loc[date], params["capital"]
        ).reindex(close.columns).fillna(0.0)
        delta = target - last_weights
        details = _transaction_cost_details(
            delta,
            features["average_amount"].loc[date],
            params["capital"],
            params["exit_days"],
            costs,
        )
        total_currency = details["total"]
        cost_series.loc[date] = total_currency / params["capital"]
        for key in cost_totals:
            cost_totals[key] += details.get(key, 0.0)
        weights.loc[date] = target
        last_weights = target
        records.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "turnover": round(float(delta.abs().sum()), 6),
                "cost_bps": round(total_currency / params["capital"] * 10000.0, 2),
                "cost_currency": round(total_currency, 2),
                "commission": round(details["commission"], 2),
                "minimum_commission_extra": round(details["minimum_commission_extra"], 2),
                "transfer_fee": round(details["transfer_fee"], 2),
                "stamp_tax": round(details["stamp_tax"], 2),
                "slippage": round(details["slippage"], 2),
                "impact": round(details["impact"], 2),
                "invested_weight": round(float(target.sum()), 6),
                "cash_weight": round(1.0 - float(target.sum()), 6),
            }
        )

    weights = weights.ffill().fillna(0.0)
    gross_return = (weights.shift(1).fillna(0.0) * returns.reindex(dates)).sum(axis=1)
    net_return = gross_return - cost_series
    gross_nav = (1.0 + gross_return).cumprod()
    net_nav = (1.0 + net_return).cumprod()
    benchmark_return = returns.reindex(dates).mean(axis=1).fillna(0.0)
    benchmark_nav = (1.0 + benchmark_return).cumprod()
    for series in (gross_nav, net_nav, benchmark_nav):
        if not series.empty:
            series.iloc[0] = 1.0
    return gross_nav, net_nav, benchmark_nav, weights, records, cost_totals


def _simulate(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    factors: dict[str, pd.DataFrame],
    metadata: dict[str, dict],
    params: dict,
) -> tuple[pd.Series, pd.Series, pd.DataFrame, list[dict]]:
    returns = close.pct_change(fill_method=None).fillna(0.0)
    dates = close.index
    rebalance_dates = _rebalance_dates(dates, REBALANCE_CONFIGS[params["rebalance"]])
    if not rebalance_dates:
        raise ValueError("历史区间内没有可用的调仓日")

    weights = pd.DataFrame(np.nan, index=dates, columns=close.columns)
    last_weights = pd.Series(0.0, index=close.columns)
    cost_series = pd.Series(0.0, index=dates)
    records = []
    costs = CostConfig()

    for date in rebalance_dates:
        target = _capacity_target(
            factors["score"].loc[date],
            factors["volatility_20"].loc[date],
            factors["average_amount"].loc[date],
            params["capital"],
            params["top_n"],
            0.10,
            params["participation_rate"],
            params["exit_days"],
            metadata=metadata,
            industry_cap=params["industry_cap"],
            prices=close.loc[date],
        )
        target = _round_target_to_lots(target, close.loc[date], params["capital"]).reindex(
            close.columns
        ).fillna(0.0)
        delta = target - last_weights
        turnover = float(delta.abs().sum())
        adv_row = factors["average_amount"].loc[date]
        total_cost, minimum_commission_cost = _transaction_cost(
            delta, adv_row, params["capital"], params["exit_days"], costs
        )
        cost_series.loc[date] = total_cost
        weights.loc[date] = target
        last_weights = target
        records.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "turnover": round(turnover, 6),
                "cost_bps": round(total_cost * 10000.0, 2),
                "cost_currency": round(total_cost * params["capital"], 2),
                "minimum_commission_cost": round(
                    minimum_commission_cost * params["capital"], 2
                ),
                "invested_weight": round(float(target.sum()), 6),
                "cash_weight": round(1.0 - float(target.sum()), 6),
            }
        )

    weights = weights.ffill().fillna(0.0)
    portfolio_return = (weights.shift(1).fillna(0.0) * returns).sum(axis=1) - cost_series
    portfolio_return = portfolio_return.clip(lower=-0.30, upper=0.30)
    nav = (1.0 + portfolio_return).cumprod()
    if not nav.empty:
        nav.iloc[0] = 1.0

    benchmark_return = returns.mean(axis=1).fillna(0.0)
    benchmark_nav = (1.0 + benchmark_return).cumprod()
    if not benchmark_nav.empty:
        benchmark_nav.iloc[0] = 1.0
    return nav, benchmark_nav, weights, records


def _finite(value: float, digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _annual_cost_drag(rebalances: list[dict], nav: pd.Series) -> float:
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / 365.25)
    return sum(row["cost_bps"] for row in rebalances) / 10000.0 / years


def _period_metrics(
    gross_nav: pd.Series,
    net_nav: pd.Series,
    benchmark_nav: pd.Series,
    rebalances: list[dict],
    start: pd.Timestamp,
    end: pd.Timestamp,
    capital: float,
) -> dict:
    gross = gross_nav.loc[(gross_nav.index >= start) & (gross_nav.index <= end)]
    net = net_nav.loc[(net_nav.index >= start) & (net_nav.index <= end)]
    bench = benchmark_nav.loc[(benchmark_nav.index >= start) & (benchmark_nav.index <= end)]
    if len(net) < 2:
        return {
            "gross_annual_return": None,
            "annual_cost_drag": None,
            "net_annual_return": None,
            "benchmark_annual_return": None,
            "net_excess_annual_return": None,
            "max_drawdown": None,
            "annual_volatility": None,
            "sharpe_ratio": None,
            "info_ratio": None,
            "annual_turnover": None,
            "cumulative_return": None,
            "cost_currency": 0.0,
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        }
    gross = gross / gross.iloc[0]
    net = net / net.iloc[0]
    bench = bench / bench.iloc[0]
    period_records = [
        row for row in rebalances if start <= pd.Timestamp(row["date"]) <= end
    ]
    turnover = compute_annual_turnover(
        pd.Series([row["turnover"] for row in period_records], dtype=float),
        pd.DatetimeIndex([row["date"] for row in period_records]),
    ) if period_records else 0.0
    metrics = compute_metrics(net, benchmark_nav=bench, annual_turnover=turnover)
    gross_metrics = compute_metrics(gross)
    years = max((net.index[-1] - net.index[0]).days / 365.25, 1 / 365.25)
    cost_currency = sum(float(row.get("cost_currency", 0.0)) for row in period_records)
    cost_drag = sum(float(row.get("cost_bps", 0.0)) for row in period_records) / 10000.0 / years
    benchmark_annual = metrics.get("benchmark_annual_return", np.nan)
    net_annual = metrics.get("annual_return", np.nan)
    return {
        "gross_annual_return": _finite(gross_metrics.get("annual_return", np.nan)),
        "annual_cost_drag": _finite(cost_drag),
        "net_annual_return": _finite(net_annual),
        "benchmark_annual_return": _finite(benchmark_annual),
        "net_excess_annual_return": _finite(
            net_annual - benchmark_annual if math.isfinite(net_annual) and math.isfinite(benchmark_annual) else None
        ),
        "max_drawdown": _finite(metrics.get("max_drawdown", np.nan)),
        "annual_volatility": _finite(metrics.get("annual_volatility", np.nan)),
        "sharpe_ratio": _finite(metrics.get("sharpe_ratio", np.nan), 4),
        "info_ratio": _finite(metrics.get("info_ratio", np.nan), 4),
        "annual_turnover": _finite(turnover, 4),
        "cumulative_return": _finite(metrics.get("cumulative_return", np.nan)),
        "cost_currency": _finite(cost_currency, 2),
        "start": net.index[0].strftime("%Y-%m-%d"),
        "end": net.index[-1].strftime("%Y-%m-%d"),
    }


def _portfolio_capacity(
    weights: pd.Series,
    factors: dict[str, pd.DataFrame],
    latest: pd.Timestamp,
    params: dict,
) -> float:
    values = []
    adv_row = factors["average_amount"].loc[latest]
    for symbol, weight in weights.items():
        weight = float(weight)
        if weight <= 0 or pd.isna(weight):
            continue
        average_amount = float(adv_row.get(symbol, 0.0))
        if not math.isfinite(average_amount) or average_amount <= 0:
            continue
        values.append(
            average_amount * params["participation_rate"] * params["exit_days"] / weight
        )
    return min(values, default=0.0)


def _factor_diagnostics(close: pd.DataFrame, factors: dict[str, pd.DataFrame]) -> dict:
    forward_20d = close.shift(-20) / close - 1.0
    sample_dates = close.index[60:-20:20]
    ics = []
    for date in sample_dates:
        scores = factors["score"].loc[date].dropna()
        forward = forward_20d.loc[date].dropna()
        common = scores.index.intersection(forward.index)
        if len(common) < 8:
            continue
        correlation = scores.reindex(common).corr(forward.reindex(common), method="spearman")
        if pd.notna(correlation) and math.isfinite(float(correlation)):
            ics.append(float(correlation))

    if not ics:
        return {
            "ic_mean": None,
            "ic_ir": None,
            "ic_win_rate": None,
            "samples": 0,
            "horizon_days": 20,
            "status": "样本不足",
        }
    values = np.asarray(ics, dtype=float)
    mean = float(values.mean())
    std = float(values.std(ddof=1)) if len(values) > 1 else np.nan
    ic_ir = mean / std * math.sqrt(12.0) if std and math.isfinite(std) and std > 0 else None
    if mean >= 0.02 and ic_ir is not None and ic_ir >= 0.25:
        status = "方向有效"
    elif mean > 0:
        status = "方向偏弱"
    else:
        status = "反向或不稳定"
    return {
        "ic_mean": _finite(mean, 4),
        "ic_ir": _finite(ic_ir, 3),
        "ic_win_rate": _finite(float((values > 0).mean()), 4),
        "samples": len(values),
        "horizon_days": 20,
        "status": status,
    }


def _market_regime(close: pd.DataFrame, factors: dict[str, pd.DataFrame]) -> dict:
    returns = close.pct_change(fill_method=None)
    equal_returns = returns.mean(axis=1).fillna(0.0)
    equal_nav = (1.0 + equal_returns).cumprod()
    index_return_20d = (
        float(equal_nav.iloc[-1] / equal_nav.iloc[-21] - 1.0)
        if len(equal_nav) > 21 and equal_nav.iloc[-21] > 0
        else None
    )
    ma60 = close.rolling(60, min_periods=40).mean()
    breadth = (close.iloc[-1] > ma60.iloc[-1]).mean() if len(close) >= 60 else np.nan
    positive_breadth = (factors["momentum_20"].iloc[-1] > 0).mean()
    volatility = factors["volatility_20"].iloc[-1].median()
    dispersion = factors["momentum_20"].iloc[-1].std()

    if pd.notna(breadth) and breadth >= 0.62 and index_return_20d is not None and index_return_20d > 0.02:
        key, label, note = "risk_on", "风险偏好扩张", "广度与价格趋势同步，仍需用换手和成本约束控制追涨。"
    elif pd.notna(breadth) and breadth <= 0.38 and index_return_20d is not None and index_return_20d < -0.02:
        key, label, note = "risk_off", "风险偏好收缩", "市场广度偏弱，应先检查容量、现金缓冲与清仓节奏。"
    else:
        key, label, note = "neutral", "中性震荡", "趋势分化，优先观察横截面离散度和因子稳定性。"
    return {
        "key": key,
        "label": label,
        "note": note,
        "breadth_above_ma60": _finite(breadth, 4),
        "index_return_20d": _finite(index_return_20d, 6),
        "positive_momentum_breadth": _finite(positive_breadth, 4),
        "median_volatility_20": _finite(volatility, 6),
        "momentum_dispersion": _finite(dispersion, 6),
    }
def _current_portfolio(
    close: pd.DataFrame,
    factors: dict[str, pd.DataFrame],
    metadata: dict[str, dict],
    params: dict,
) -> tuple[list[dict], list[dict], dict]:
    latest = close.index[-1]
    target = _capacity_target(
        factors["score"].loc[latest],
        factors["volatility_20"].loc[latest],
        factors["average_amount"].loc[latest],
        params["capital"],
        params["top_n"],
        0.10,
        params["participation_rate"],
        params["exit_days"],
    )
    rank = factors["score"].loc[latest].dropna().sort_values(ascending=False)
    holdings = []
    for rank_index, symbol in enumerate(rank.index, start=1):
        if symbol not in target.index:
            continue
        weight = float(target[symbol])
        average_amount = float(factors["average_amount"].loc[latest, symbol])
        position_value = weight * params["capital"]
        price = float(close.loc[latest, symbol])
        shares = max(0, int(round(position_value / price / 100.0)) * 100)
        lots = shares // 100
        days_to_exit = (
            position_value / (average_amount * params["participation_rate"])
            if average_amount > 0 and weight > 0
            else None
        )
        info = metadata.get(symbol, {})
        holdings.append(
            {
                "rank": rank_index,
                "symbol": symbol,
                "name": info.get("name") or symbol,
                "industry": info.get("industry") or "未分类",
                "weight": _finite(weight),
                "position_value": _finite(position_value, 2),
                "price": _finite(price, 3),
                "shares": shares,
                "lots": lots,
                "score": _finite(float(rank[symbol]), 4),
                "momentum_20": _finite(float(factors["momentum_20"].loc[latest, symbol])),
                "momentum_60": _finite(float(factors["momentum_60"].loc[latest, symbol])),
                "volatility_20": _finite(float(factors["volatility_20"].loc[latest, symbol])),
                "average_amount": _finite(average_amount, 2),
                "days_to_exit": _finite(days_to_exit, 2) if days_to_exit is not None else None,
                "impact_bps": _finite(2.0 + 10.0 * math.sqrt(params["participation_rate"]), 2),
            }
        )

    ranking = []
    for rank_index, symbol in enumerate(rank.index, start=1):
        info = metadata.get(symbol, {})
        ranking.append(
            {
                "rank": rank_index,
                "symbol": symbol,
                "name": info.get("name") or symbol,
                "industry": info.get("industry") or "未分类",
                "score": _finite(float(rank[symbol]), 4),
                "momentum_20": _finite(float(factors["momentum_20"].loc[latest, symbol])),
                "momentum_60": _finite(float(factors["momentum_60"].loc[latest, symbol])),
                "volatility_20": _finite(float(factors["volatility_20"].loc[latest, symbol])),
                "average_amount": _finite(float(factors["average_amount"].loc[latest, symbol]), 2),
                "selected": symbol in target.index,
            }
        )

    capacity_values = []
    for symbol, weight in target.items():
        if weight <= 0:
            continue
        average_amount = float(factors["average_amount"].loc[latest, symbol])
        capacity_values.append(
            average_amount * params["participation_rate"] * params["exit_days"] / weight
        )
    portfolio_capacity = min(capacity_values, default=0.0)
    utilization = params["capital"] / portfolio_capacity if portfolio_capacity > 0 else 999.0
    capacity = {
        "portfolio_capacity": _finite(portfolio_capacity, 2),
        "utilization": _finite(utilization, 4),
        "headroom": _finite(max(0.0, portfolio_capacity - params["capital"]), 2),
        "cash_weight": _finite(max(0.0, 1.0 - float(target.sum())), 6),
        "max_days_to_exit": _finite(max((row["days_to_exit"] or 0.0 for row in holdings), default=0.0), 2),
    }
    return holdings, ranking, capacity


def _current_portfolio_v2(
    close: pd.DataFrame,
    factors: dict[str, pd.DataFrame],
    metadata: dict[str, dict],
    params: dict,
) -> tuple[list[dict], list[dict], dict, list[dict], dict]:
    latest = close.index[-1]
    target_args = (
        factors["score"].loc[latest],
        factors["volatility_20"].loc[latest],
        factors["average_amount"].loc[latest],
    )
    target = _capacity_target(
        *target_args,
        params["capital"],
        params["top_n"],
        0.10,
        params["participation_rate"],
        params["exit_days"],
        metadata=metadata,
        industry_cap=params["industry_cap"],
        prices=close.loc[latest],
    )
    requested_notional = float(target.sum()) * params["capital"]
    target = _round_target_to_lots(target, close.loc[latest], params["capital"])
    rank = factors["score"].loc[latest].dropna().sort_values(ascending=False)
    holdings = []
    for rank_index, symbol in enumerate(rank.index, start=1):
        if symbol not in target.index:
            continue
        weight = float(target[symbol])
        average_amount = float(factors["average_amount"].loc[latest, symbol])
        price = float(close.loc[latest, symbol])
        lots = max(0, int(math.floor(weight * params["capital"] / (price * 100.0) + 1e-6)))
        shares = lots * 100
        position_value = shares * price
        weight = position_value / params["capital"]
        days_to_exit = (
            position_value / (average_amount * params["participation_rate"])
            if average_amount > 0 and weight > 0
            else None
        )
        info = metadata.get(symbol, {})
        holdings.append(
            {
                "rank": rank_index,
                "symbol": symbol,
                "name": info.get("name") or symbol,
                "industry": info.get("industry") or "未分类",
                "weight": _finite(weight),
                "position_value": _finite(position_value, 2),
                "price": _finite(price, 3),
                "shares": shares,
                "lots": lots,
                "min_lot_value": _finite(price * 100.0, 2),
                "score": _finite(float(rank[symbol]), 4),
                "momentum_20": _finite(float(factors["momentum_20"].loc[latest, symbol])),
                "momentum_60": _finite(float(factors["momentum_60"].loc[latest, symbol])),
                "volatility_20": _finite(float(factors["volatility_20"].loc[latest, symbol])),
                "average_amount": _finite(average_amount, 2),
                "days_to_exit": _finite(days_to_exit, 2) if days_to_exit is not None else None,
                "impact_bps": _finite(
                    _impact_bps(weight, params["capital"], average_amount, params["exit_days"]),
                    2,
                ),
            }
        )

    ranking = []
    for rank_index, symbol in enumerate(rank.index, start=1):
        info = metadata.get(symbol, {})
        ranking.append(
            {
                "rank": rank_index,
                "symbol": symbol,
                "name": info.get("name") or symbol,
                "industry": info.get("industry") or "未分类",
                "score": _finite(float(rank[symbol]), 4),
                "momentum_20": _finite(float(factors["momentum_20"].loc[latest, symbol])),
                "momentum_60": _finite(float(factors["momentum_60"].loc[latest, symbol])),
                "volatility_20": _finite(float(factors["volatility_20"].loc[latest, symbol])),
                "average_amount": _finite(float(factors["average_amount"].loc[latest, symbol]), 2),
                "selected": symbol in target.index,
            }
        )

    portfolio_capacity = _portfolio_capacity(target, factors, latest, params)
    utilization = params["capital"] / portfolio_capacity if portfolio_capacity > 0 else 999.0
    daily_notional = float(target.sum()) * params["capital"] / params["exit_days"]
    largest_order = max(
        (float(row["position_value"]) / params["exit_days"] for row in holdings if row["position_value"]),
        default=0.0,
    )
    build_delta = target.reindex(close.columns).fillna(0.0)
    build_cost_rate, minimum_commission_rate = _transaction_cost(
        build_delta,
        factors["average_amount"].loc[latest],
        params["capital"],
        params["exit_days"],
        CostConfig(),
    )
    execution = {
        "target_notional": _finite(float(target.sum()) * params["capital"], 2),
        "requested_notional": _finite(requested_notional, 2),
        "lot_shortfall": _finite(max(0.0, requested_notional - float(target.sum()) * params["capital"]), 2),
        "lot_size": 100,
        "min_lot_value": _finite(
            min(
                (float(close.loc[latest, symbol]) * 100.0 for symbol in target.index),
                default=0.0,
            ),
            2,
        ),
        "daily_notional": _finite(daily_notional, 2),
        "largest_daily_order": _finite(largest_order, 2),
        "exit_days": _finite(params["exit_days"], 2),
        "participation_rate": _finite(params["participation_rate"], 4),
        "order_count": len(holdings),
        "build_cost": _finite(build_cost_rate * params["capital"], 2),
        "build_cost_bps": _finite(build_cost_rate * 10000.0, 2),
        "minimum_commission_cost": _finite(
            minimum_commission_rate * params["capital"], 2
        ),
    }

    exposure_map: dict[str, dict] = {}
    for symbol, weight in target.items():
        industry = str(metadata.get(symbol, {}).get("industry") or "未分类")
        if industry not in exposure_map:
            exposure_map[industry] = {"industry": industry, "weight": 0.0, "positions": 0}
        exposure_map[industry]["weight"] += float(weight)
        exposure_map[industry]["positions"] += 1
    industry_exposure = [
        {
            "industry": row["industry"],
            "weight": _finite(row["weight"]),
            "positions": row["positions"],
            "limit": _finite(params["industry_cap"]),
        }
        for row in sorted(exposure_map.values(), key=lambda item: item["weight"], reverse=True)
    ]

    stress = []
    for multiple in (1, 2, 5, 10):
        stressed_capital = params["capital"] * multiple
        stressed_target = _capacity_target(
            *target_args,
            stressed_capital,
            params["top_n"],
            0.10,
            params["participation_rate"],
            params["exit_days"],
            metadata=metadata,
            industry_cap=params["industry_cap"],
            prices=close.loc[latest],
        )
        stressed_capacity = _portfolio_capacity(stressed_target, factors, latest, params)
        stress.append(
            {
                "multiple": multiple,
                "capital": _finite(stressed_capital, 2),
                "invested_weight": _finite(float(stressed_target.sum()), 6),
                "utilization": _finite(
                    stressed_capital / stressed_capacity if stressed_capacity > 0 else None,
                    4,
                ),
                "capacity": _finite(stressed_capacity, 2),
            }
        )

    capacity = {
        "portfolio_capacity": _finite(portfolio_capacity, 2),
        "utilization": _finite(utilization, 4),
        "headroom": _finite(max(0.0, portfolio_capacity - params["capital"]), 2),
        "cash_weight": _finite(max(0.0, 1.0 - float(target.sum())), 6),
        "max_days_to_exit": _finite(
            max((row["days_to_exit"] or 0.0 for row in holdings), default=0.0),
            2,
        ),
        "industry_cap": _finite(params["industry_cap"], 4),
        "stress": stress,
    }
    return holdings, ranking, capacity, industry_exposure, execution


def _curve(nav: pd.Series, benchmark_nav: pd.Series) -> list[dict]:
    sample = max(1, len(nav) // 120)
    rows = []
    for index in range(0, len(nav), sample):
        date = nav.index[index]
        rows.append(
            {
                "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "value": _finite(float(nav.iloc[index])),
                "benchmark": _finite(float(benchmark_nav.reindex(nav.index).iloc[index])),
            }
        )
    if nav.index[-1] != (pd.Timestamp(rows[-1]["date"]) if rows else None):
        rows.append(
            {
                "date": pd.Timestamp(nav.index[-1]).strftime("%Y-%m-%d"),
                "value": _finite(float(nav.iloc[-1])),
                "benchmark": _finite(float(benchmark_nav.reindex(nav.index).iloc[-1])),
            }
        )
    return rows


def _model_comparison(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    metadata: dict[str, dict],
    params: dict,
) -> list[dict]:
    rows = []
    latest = close.index[-1]
    for model, config in MODEL_CONFIGS.items():
        factors = _factor_matrices(close, amount, model)
        nav, benchmark_nav, weights, rebalances = _simulate(
            close, amount, factors, metadata, {**params, "model": model}
        )
        annual_turnover = compute_annual_turnover(
            pd.Series([row["turnover"] for row in rebalances], dtype=float),
            pd.DatetimeIndex([row["date"] for row in rebalances]),
        )
        metrics = compute_metrics(nav, benchmark_nav=benchmark_nav, annual_turnover=annual_turnover)
        latest_weights = weights.iloc[-1]
        capacity = _portfolio_capacity(latest_weights, factors, latest, params)
        rows.append(
            {
                "model": model,
                "label": config["label"],
                "selected": model == params["model"],
                "annual_return": _finite(metrics.get("annual_return", np.nan)),
                "annual_volatility": _finite(metrics.get("annual_volatility", np.nan)),
                "sharpe_ratio": _finite(metrics.get("sharpe_ratio", np.nan), 4),
                "max_drawdown": _finite(metrics.get("max_drawdown", np.nan)),
                "annual_turnover": _finite(annual_turnover, 4),
                "annual_cost_drag": _finite(_annual_cost_drag(rebalances, nav)),
                "invested_weight": _finite(float(latest_weights.sum())),
                "capacity_utilization": _finite(
                    params["capital"] / capacity if capacity > 0 else None,
                    4,
                ),
            }
        )
    return rows


def _accumulation_plan(
    capital: float,
    monthly_contribution: float,
    horizon_years: int,
    annual_return: Optional[float],
) -> dict:
    """Project personal contributions without treating backtest returns as a promise."""
    months = horizon_years * 12
    total_contributed = capital + monthly_contribution * months
    projected = None
    conservative_annual_return = 0.08
    conservative_rate = (1.0 + conservative_annual_return) ** (1.0 / 12.0) - 1.0
    conservative = capital * (1.0 + conservative_annual_return) ** horizon_years
    for _ in range(months):
        conservative = conservative * (1.0 + conservative_rate) + monthly_contribution
    if annual_return is not None and math.isfinite(float(annual_return)):
        annual = float(annual_return)
        if annual <= -0.999:
            projected = monthly_contribution * max(0, months - 1)
        else:
            monthly_rate = (1.0 + annual) ** (1.0 / 12.0) - 1.0
            projected = capital * (1.0 + annual) ** horizon_years
            for _ in range(months):
                projected = projected * (1.0 + monthly_rate) + monthly_contribution
    return {
        "starting_capital": _finite(capital, 2),
        "monthly_contribution": _finite(monthly_contribution, 2),
        "horizon_years": horizon_years,
        "months": months,
        "total_contributed": _finite(total_contributed, 2),
        "zero_return_end_value": _finite(total_contributed, 2),
        "conservative_annual_return": conservative_annual_return,
        "conservative_end_value": _finite(conservative, 2),
        "backtest_annual_return": _finite(annual_return, 6),
        "projected_end_value": _finite(projected, 2),
        "projected_gain": _finite(
            projected - total_contributed if projected is not None else None, 2
        ),
        "note": "0%、8% 与回测收益外推均为情景估算，不代表未来收益，也不承诺本金安全。",
    }


def _run_research_legacy(
    payload: Optional[dict] = None,
    universe_loader: Optional[UniverseLoader] = None,
    history_loader: Optional[HistoryLoader] = None,
) -> dict:
    """Run a live-universe, multi-factor, capacity-aware research backtest."""
    params = _parameters(payload)
    universe_loader = universe_loader or market_client.get_liquid_universe
    history_loader = history_loader or market_client.get_history
    try:
        items = universe_loader(params["universe_size"])
    except market_client.MarketDataError:
        raise
    except Exception as exc:
        raise ValueError(f"市场扫描失败: {exc}") from exc
    if len(items) < params["top_n"]:
        raise ValueError("市场扫描返回的股票数量不足")

    close, amount, metadata = _load_panels(items, params["days"], history_loader)
    factors = _factor_matrices(close, amount, params["model"])
    nav, benchmark_nav, weights, rebalances = _simulate(
        close, amount, factors, metadata, params
    )
    annual_turnover = compute_annual_turnover(
        pd.Series([row["turnover"] for row in rebalances], dtype=float),
        pd.DatetimeIndex([row["date"] for row in rebalances]),
    )
    metrics = compute_metrics(
        nav,
        benchmark_nav=benchmark_nav,
        annual_turnover=annual_turnover,
    )
    holdings, ranking, capacity, industry_exposure, execution = _current_portfolio_v2(
        close, factors, metadata, params
    )
    factor_validation = _factor_diagnostics(close, factors)
    regime = _market_regime(close, factors)
    model_comparison = _model_comparison(close, amount, metadata, params)

    annual_cost = _annual_cost_drag(rebalances, nav)
    metrics_payload = {
        "annual_return": _finite(metrics.get("annual_return", np.nan)),
        "annual_volatility": _finite(metrics.get("annual_volatility", np.nan)),
        "sharpe_ratio": _finite(metrics.get("sharpe_ratio", np.nan), 4),
        "max_drawdown": _finite(metrics.get("max_drawdown", np.nan)),
        "cumulative_return": _finite(metrics.get("cumulative_return", np.nan)),
        "annual_turnover": _finite(annual_turnover, 4),
        "benchmark_annual_return": _finite(metrics.get("benchmark_annual_return", np.nan)),
        "excess_annual_return": _finite(metrics.get("excess_annual_return", np.nan)),
        "info_ratio": _finite(metrics.get("info_ratio", np.nan), 4),
        "annual_cost_drag": _finite(annual_cost),
        "calmar_ratio": _finite(metrics.get("calmar_ratio", np.nan), 4),
    }
    latest_weights = weights.iloc[-1]
    accumulation = _accumulation_plan(
        params["capital"],
        params["monthly_contribution"],
        params["horizon_years"],
        metrics_payload["annual_return"],
    )
    summary = {
        **metrics_payload,
        "invested_weight": _finite(float(latest_weights.sum())),
        "latest_cash_weight": _finite(max(0.0, 1.0 - float(latest_weights.sum()))),
        "capacity_utilization": capacity["utilization"],
        "portfolio_capacity": capacity["portfolio_capacity"],
        "model_label": MODEL_CONFIGS[params["model"]]["label"],
        "rebalance_label": "周频" if params["rebalance"] == "weekly" else "月频",
        "ic_mean": factor_validation["ic_mean"],
        "ic_ir": factor_validation["ic_ir"],
        "ic_win_rate": factor_validation["ic_win_rate"],
    }
    scanned_amount = sum(float(item.get("amount") or 0.0) for item in items)
    scanned_market_cap = sum(float(item.get("market_cap") or 0.0) for item in items)

    return {
        "summary": summary,
        "metrics": metrics_payload,
        "capacity": capacity,
        "execution": execution,
        "holdings": holdings,
        "factors": ranking[: min(15, len(ranking))],
        "industry_exposure": industry_exposure,
        "factor_validation": factor_validation,
        "regime": regime,
        "model_comparison": model_comparison,
        "accumulation": accumulation,
        "nav": _curve(nav, benchmark_nav),
        "rebalances": rebalances,
        "meta": {
            "capital": params["capital"],
            "universe_size_requested": params["universe_size"],
            "universe_size_loaded": int(close.shape[1]),
            "history_days": int(close.shape[0]),
            "top_n": params["top_n"],
            "model": params["model"],
            "rebalance": params["rebalance"],
            "exit_days": params["exit_days"],
            "participation_rate": params["participation_rate"],
            "industry_cap": params["industry_cap"],
            "monthly_contribution": params["monthly_contribution"],
            "horizon_years": params["horizon_years"],
            "scanned_amount": _finite(scanned_amount, 2),
            "scanned_market_cap": _finite(scanned_market_cap, 2),
            "source": "东方财富公开行情",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "caveat": "股票池按当前成交额筛选，存在幸存者偏差；容量模型为研究近似，不构成投资建议。",
        },
    }
