"""结构化记忆：SQLite 存储策略、回测结果与因子库（阶段2基础）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd


class StrategyStore:
    def __init__(self, db_path: str | Path = "data/memory.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy (
                    strategy_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    factor_combo TEXT,
                    params_json TEXT,
                    rebalance_freq TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_result (
                    backtest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id INTEGER,
                    data_start TEXT,
                    data_end TEXT,
                    metrics_json TEXT,
                    report_path TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (strategy_id) REFERENCES strategy(strategy_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS factor_library (
                    factor_id TEXT PRIMARY KEY,
                    factor_name TEXT,
                    factor_type TEXT,
                    ic_mean REAL,
                    ic_ir REAL,
                    half_life_days REAL,
                    is_active INTEGER DEFAULT 1
                )
                """
            )

    def save_run(
        self,
        strategy_name: str,
        factor_combo: list[str],
        params: dict,
        data_start: str,
        data_end: str,
        metrics: dict,
        report_path: Optional[str] = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO strategy (strategy_name, factor_combo, params_json, rebalance_freq) "
                "VALUES (?, ?, ?, ?)",
                (
                    strategy_name,
                    json.dumps(factor_combo, ensure_ascii=False),
                    json.dumps(params, ensure_ascii=False, default=str),
                    params.get("rebalance_freq"),
                ),
            )
            strategy_id = cur.lastrowid
            conn.execute(
                "INSERT INTO backtest_result "
                "(strategy_id, data_start, data_end, metrics_json, report_path) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    strategy_id,
                    data_start,
                    data_end,
                    json.dumps(metrics, ensure_ascii=False, default=str),
                    report_path,
                ),
            )
            return strategy_id

    def list_runs(self) -> pd.DataFrame:
        with self._connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT s.strategy_id, s.strategy_name, s.factor_combo, s.rebalance_freq,
                       b.backtest_id, b.data_start, b.data_end, b.metrics_json, b.report_path,
                       b.created_at
                FROM strategy s
                LEFT JOIN backtest_result b ON s.strategy_id = b.strategy_id
                ORDER BY b.created_at DESC
                """,
                conn,
            )
        return df

    def query_strategies(
        self, min_annual_return: Optional[float] = None, max_drawdown: Optional[float] = None
    ) -> pd.DataFrame:
        df = self.list_runs()
        if df.empty:
            return df

        def _metric(row, key):
            try:
                return json.loads(row["metrics_json"] or "{}").get(key)
            except (json.JSONDecodeError, AttributeError):
                return None

        df["annual_return"] = df.apply(lambda r: _metric(r, "annual_return"), axis=1)
        df["max_drawdown"] = df.apply(lambda r: _metric(r, "max_drawdown"), axis=1)
        if min_annual_return is not None:
            df = df[df["annual_return"] >= min_annual_return]
        if max_drawdown is not None:
            df = df[df["max_drawdown"] >= -abs(max_drawdown)]
        return df
