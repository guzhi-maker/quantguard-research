"""可视化工具：净值曲线、回撤曲线、月度收益热力图，300 DPI PNG。"""

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
