"""可视化工具：净值、回撤、月度热力图、IC、分层收益与参数敏感性，300 DPI PNG。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 100


def _prepare_path(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def plot_equity_curve(
    nav: pd.Series,
    benchmark_nav: Optional[pd.Series] = None,
    save_path: str | Path = "equity_curve.png",
    title: str = "策略净值曲线",
    benchmark_name: str = "沪深300",
) -> str:
    """绘制策略 vs 基准净值曲线。"""
    path = _prepare_path(save_path)
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(nav.index, nav.values, linewidth=1.6, color="#1f77b4", label="策略")
    if benchmark_nav is not None:
        bench = benchmark_nav.reindex(nav.index).ffill()
        ax.plot(bench.index, bench.values, linewidth=1.2, color="#d62728", label=benchmark_name)
    ax.set_title(title)
    ax.set_xlabel("日期")
    ax.set_ylabel("净值")
    ax.legend(loc="best")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return str(path)


def plot_drawdown(nav: pd.Series, save_path: str | Path = "drawdown.png") -> str:
    """绘制回撤曲线。"""
    path = _prepare_path(save_path)
    dd = nav / nav.cummax() - 1.0
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.fill_between(dd.index, dd.values, 0, color="#d62728", alpha=0.45)
    ax.set_title("回撤曲线")
    ax.set_xlabel("日期")
    ax.set_ylabel("回撤")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return str(path)


def plot_monthly_returns_heatmap(
    returns: pd.Series, save_path: str | Path = "monthly_returns.png"
) -> str:
    """绘制月度收益热力图，行=年份，列=月份。"""
    path = _prepare_path(save_path)
    monthly = (1.0 + returns).resample("ME").prod() - 1.0
    table = pd.DataFrame(
        {
            "year": monthly.index.year,
            "month": monthly.index.month,
            "ret": monthly.values,
        }
    ).pivot(index="year", columns="month", values="ret")

    fig, ax = plt.subplots(figsize=(11, max(4, len(table) * 0.45)))
    im = ax.imshow(table.values * 100, cmap="RdYlGn", aspect="auto", vmin=-10, vmax=10)
    ax.set_xticks(range(len(table.columns)), [f"{m}月" for m in table.columns])
    ax.set_yticks(range(len(table.index)), [str(y) for y in table.index])
    for i in range(table.shape[0]):
        for j in range(table.shape[1]):
            value = table.values[i, j]
            if value == value:
                ax.text(j, i, f"{value * 100:.1f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, label="月收益 %")
    ax.set_title("月度收益热力图")
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return str(path)


def plot_ic_series(
    ic_series: dict[str, pd.Series],
    save_path: str | Path = "ic_series.png",
    title: str = "因子 Rank IC 序列",
) -> str:
    """绘制多个因子的 Rank IC 时间序列。"""
    path = _prepare_path(save_path)
    fig, ax = plt.subplots(figsize=(11, 5))
    for key, series in list(ic_series.items())[:12]:
        ax.plot(series.index, series.values, linewidth=0.9, label=key)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(title)
    ax.set_xlabel("日期")
    ax.set_ylabel("Rank IC")
    ax.legend(loc="best", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return str(path)


def plot_quantile_returns(
    quantile_returns: dict[str, pd.DataFrame],
    save_path: str | Path = "quantile_returns.png",
    title: str = "因子分层收益",
) -> str:
    """绘制最多 4 个因子的分层未来收益曲线（20 日滚动均值）。"""
    path = _prepare_path(save_path)
    keys = list(quantile_returns.keys())[:4]
    fig, axes = plt.subplots(1, max(1, len(keys)), figsize=(max(8, 4 * len(keys)), 4))
    if len(keys) == 1:
        axes = [axes]
    for ax, key in zip(axes, keys):
        table = quantile_returns[key]
        for col in table.columns:
            smoothed = table[col].rolling(20, min_periods=1).mean()
            ax.plot(smoothed.index, smoothed.values, label=f"Q{int(col) + 1}")
        ax.set_title(key, fontsize=10)
        ax.set_xlabel("日期")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    if keys:
        fig.suptitle(title)
        fig.tight_layout()
    else:
        fig.text(0.5, 0.5, "无有效分层数据", ha="center", va="center")
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return str(path)


def plot_parameter_heatmap(
    table: pd.DataFrame,
    save_path: str | Path = "parameter_heatmap.png",
    title: str = "参数敏感性热力图",
    value_label: str = "年化收益",
    is_pct: bool = True,
) -> str:
    """绘制参数网格热力图，用于识别参数敏感区间。"""
    path = _prepare_path(save_path)
    fig, ax = plt.subplots(figsize=(max(6, table.shape[1] * 1.6), max(4, table.shape[0] * 1.2)))
    values = table.values * 100 if is_pct else table.values
    im = ax.imshow(values, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(table.columns)), [str(c) for c in table.columns])
    ax.set_yticks(range(len(table.index)), [str(i) for i in table.index])
    ax.set_xlabel(table.columns.name or "参数1")
    ax.set_ylabel(table.index.name or "参数2")
    for i in range(table.shape[0]):
        for j in range(table.shape[1]):
            value = values[i, j]
            if value == value:
                ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=9)
    fig.colorbar(im, ax=ax, label=value_label)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return str(path)
