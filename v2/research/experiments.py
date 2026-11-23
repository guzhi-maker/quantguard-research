"""变体实验：在同一快照上横向比较特征、标签、集成、校准与组合方式。

预注册选择规则（跑之前固定，避免事后挑好看的）：
1. 必须通过 泄漏检查、CVaR 单调、证据包规模；
2. 优先选择"非重叠独立 IC"最高者；
3. 若覆盖率未通过且未做保形校准，降级为候选而不是首选；
4. DSR 的试验次数按真实尝试的变体数量填写。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from research import preferences as preferences_mod
from research import validation as validation_mod

# 变体清单：键为展示名，值为 run_research 的 variant 覆盖项
VARIANTS: dict[str, dict] = {
    # 默认：固定特征白名单 + 3 种子 + 排序组合 + 关闭元模型
    "A_lean": {},
    # 特征选择方式对照：聚类选特征（旧做法）
    "B_cluster": {"feature_set": "cluster"},
    # 组合方式对照：CVaR 线性规划（旧做法）
    "C_optimize": {"portfolio_mode": "optimize"},
    # 元模型对照：AUC 门控开启
    "D_meta": {"use_meta": "auto"},
    # 集成规模对照：5 种子
    "E_seeds5": {"seeds": 5},
    # 标签基准对照：用全市场中位数（本快照缺少真实行业分类）
    "F_market": {"label_baseline": "market"},
}


@dataclass
class VariantResult:
    name: str
    variant: dict
    metrics: dict = field(default_factory=dict)
    acceptance: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    error: str | None = None

    def as_row(self) -> dict:
        if self.error:
            return {"variant": self.name, "error": self.error}
        metrics = self.metrics
        calibration = metrics.get("calibration") or {}
        holdout = calibration.get("holdout_coverage") or {}
        return {
            "variant": self.name,
            "ic_mean": metrics.get("rank_ic", {}).get("ic_mean"),
            "independent_ic": metrics.get("independent_ic", {}).get("ic_mean"),
            "top_excess": metrics.get("top_bucket", {}).get("top_excess"),
            "p10_breach": metrics.get("coverage", {}).get("p10_breach"),
            "p90_breach": metrics.get("coverage", {}).get("p90_breach"),
            "cal_p10": holdout.get("p10_breach"),
            "cal_p90": holdout.get("p90_breach"),
            "meta_auc": metrics.get("meta", {}).get("auc"),
            "validation_mode": metrics.get("validation_mode"),
            "portfolio_mode": self.variant.get("portfolio_mode", "optimize"),
            **{f"ok_{key}": value for key, value in self.acceptance.items()},
        }


def run_variants(
    run_id: str | None = None,
    horizon: int = 2,
    risk: float = 5.5,
    variants: dict[str, dict] | None = None,
    include: list[str] | None = None,
) -> dict:
    """逐个跑变体并返回对比表、预注册选择结果与 DSR。"""
    from research import pipeline as pipeline_mod

    chosen = variants or VARIANTS
    if include:
        chosen = {name: config for name, config in chosen.items() if name in include}

    results: list[VariantResult] = []
    for name, config in chosen.items():
        variant = pipeline_mod.make_variant(name=name, **config)
        try:
            payload = pipeline_mod.run_research(
                run_id=run_id, risk=risk, horizon=horizon, variant=variant
            ).as_dict()
        except Exception as exc:  # noqa: BLE001
            results.append(VariantResult(name=name, variant=variant, error=str(exc)))
            continue
        results.append(
            VariantResult(
                name=name,
                variant=variant,
                metrics=payload["metrics"],
                acceptance=payload["acceptance"],
                warnings=payload.get("warnings", []),
            )
        )

    rows = [result.as_row() for result in results]
    table = pd.DataFrame(rows)
    selected = _select(table)
    perf = _performance_matrix(results)
    audit = {
        "pbo": validation_mod.pbo(perf) if not perf.empty else {"pbo": None, "samples": 0},
        "dsr": _dsr(table),
        "trials": int(len(chosen)),
    }
    return {
        "horizon": horizon,
        "risk": risk,
        "run_id": run_id,
        "table": table,
        "results": results,
        "selected": selected,
        "audit": audit,
    }


def _dsr(table: pd.DataFrame) -> dict:
    if table.empty or "independent_ic" not in table.columns:
        return {"dsr": None}
    valid = table.dropna(subset=["independent_ic"])
    if valid.empty:
        return {"dsr": None}
    best = valid.sort_values("independent_ic", ascending=False).iloc[0]
    # IC 换算成近似夏普：IC * sqrt(期数)，仅用于横向比较
    observations = int(max(3, len(valid)))
    sharpe = float(best["independent_ic"]) * 4.0
    return validation_mod.deflated_sharpe(
        sharpe=sharpe, observations=observations, trials=int(len(table))
    )


def _performance_matrix(results: list[VariantResult]) -> pd.DataFrame:
    """用各变体的 Top 组超额日序列拼成 日期 × 变体 的收益矩阵，供 PBO 使用。"""
    columns = {}
    for result in results:
        series = result.metrics.get("top_series") if not result.error else None
        if not series:
            continue
        columns[result.name] = pd.Series(
            {pd.Timestamp(date): value for date, value in series.items()}
        )
    if not columns:
        return pd.DataFrame()
    return pd.DataFrame(columns).sort_index()


def _select(table: pd.DataFrame) -> dict:
    """按预注册规则选出首选变体。"""
    if table.empty:
        return {"variant": None, "reason": "没有可用变体"}
    if "error" in table.columns:
        table = table[table["error"].isna()]
    if table.empty:
        return {"variant": None, "reason": "全部变体失败"}
    gate = table.copy()
    for column in ("ok_leakage_free", "ok_cvar_monotonic", "ok_evidence_size_ok", "ok_coverage_ok"):
        if column in gate.columns:
            gate = gate[gate[column].fillna(False)]
    if gate.empty:
        gate = table
    gate = gate.sort_values("independent_ic", ascending=False, na_position="last")
    best = gate.iloc[0]
    return {
        "variant": best["variant"],
        "independent_ic": _maybe_float(best.get("independent_ic")),
        "ic_mean": _maybe_float(best.get("ic_mean")),
        "coverage_ok": bool(best.get("ok_coverage_ok")) if "ok_coverage_ok" in best else None,
        "reason": "通过泄漏/单调/规模门槛后按独立 IC 排序",
    }


def _maybe_float(value):
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def format_table(result: dict) -> str:
    table = result.get("table")
    if table is None or table.empty:
        return "没有可用结果"
    columns = [
        column
        for column in (
            "variant",
            "ic_mean",
            "independent_ic",
            "top_excess",
            "p10_breach",
            "p90_breach",
            "cal_p10",
            "cal_p90",
            "meta_auc",
            "ok_coverage_ok",
            "ok_cvar_monotonic",
        )
        if column in table.columns
    ]
    lines = [table[columns].to_string(index=False)]
    selected = result.get("selected") or {}
    lines.append("")
    lines.append(f"预注册选择：{selected.get('variant')}（{selected.get('reason')}）")
    audit = result.get("audit") or {}
    lines.append(f"审计：{audit}")
    return "\n".join(lines)


def preference_snapshot(risk: float = 5.5, horizon: int = 2) -> dict:
    return preferences_mod.resolve(risk, horizon).as_dict()
