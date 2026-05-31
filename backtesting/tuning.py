"""Parameter sweep for the dual-momentum strategy.

Goal: find the *least-losing* ("負けにくい") parameter set, not the highest
raw return. We therefore rank by risk-aware metrics:

    * Calmar ratio  = CAGR / max drawdown   (return earned per unit of pain)
    * Rolling 1-year win rate                (how often you're up over any year)
    * Worst calendar year                    (the bad-case outcome)

To guard against overfitting, every candidate is also scored on two disjoint
time halves; we prefer parameters that are *consistently* good, not lucky.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtesting.backtest_engine import BacktestConfig, BacktestEngine
from utils.logger import get_logger

_logger = get_logger(__name__)
_TRADING_DAYS = 252


@dataclass(frozen=True)
class Candidate:
    """One parameter combination and its full-period scores."""

    lookback_months: int
    top_n: int
    max_drawdown_threshold: float
    require_positive_momentum: bool
    cagr: float
    max_drawdown: float
    calmar: float
    sharpe: float
    rolling_year_win: float
    worst_year: float
    total_return: float


def _max_drawdown(equity: pd.Series) -> float:
    return float((1.0 - equity / equity.cummax()).max())


def _cagr(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return (float(equity.iloc[-1]) / float(equity.iloc[0])) ** (1.0 / years) - 1.0


def _sharpe(equity: pd.Series) -> float:
    r = equity.pct_change().dropna()
    s = float(r.std())
    return float(r.mean()) / s * np.sqrt(_TRADING_DAYS) if s else 0.0


def _rolling_year_win(equity: pd.Series) -> float:
    """Fraction of trading days whose trailing-252-day return is positive."""
    roll = equity / equity.shift(_TRADING_DAYS) - 1.0
    roll = roll.dropna()
    return float((roll > 0).mean()) if len(roll) else 0.0


def _worst_year(equity: pd.Series) -> float:
    """Worst calendar-year return (fraction)."""
    yearly = equity.resample("YE").last().pct_change().dropna()
    return float(yearly.min()) if len(yearly) else 0.0


def score_equity(equity: pd.Series) -> dict[str, float]:
    """Compute the risk-aware metric bundle for an equity curve."""
    mdd = _max_drawdown(equity)
    cagr = _cagr(equity)
    return {
        "cagr": cagr,
        "max_drawdown": mdd,
        "calmar": (cagr / mdd) if mdd > 0 else 0.0,
        "sharpe": _sharpe(equity),
        "rolling_year_win": _rolling_year_win(equity),
        "worst_year": _worst_year(equity),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0),
    }


def run_sweep(
    closes: pd.DataFrame,
    base: BacktestConfig,
    lookbacks: tuple[int, ...] = (3, 6, 9, 12),
    top_ns: tuple[int, ...] = (1, 2, 3),
    dd_thresholds: tuple[float, ...] = (0.10, 0.15, 0.20, 1.00),
    pos_momentum_options: tuple[bool, ...] = (False, True),
) -> pd.DataFrame:
    """Run the full grid and return a scored DataFrame.

    Args:
        closes: Pre-loaded aligned close prices (loaded once, reused).
        base: Base config supplying the fixed fields (universe, costs, start).
        lookbacks: Momentum lookback windows to test (months).
        top_ns: Number-of-holdings options.
        dd_thresholds: Drawdown limits (1.00 effectively disables the monitor).
        pos_momentum_options: Whether to apply the positive-momentum filter.

    Returns:
        DataFrame of one row per combination with the scored metrics.
    """
    rows: list[dict] = []
    combos = list(
        itertools.product(lookbacks, top_ns, dd_thresholds, pos_momentum_options)
    )
    _logger.info("Sweeping %d parameter combinations ...", len(combos))

    for lookback, top_n, dd, pos in combos:
        cfg = replace(
            base,
            lookback_months=lookback,
            top_n=top_n,
            max_drawdown_threshold=dd,
            require_positive_momentum=pos,
        )
        result = BacktestEngine(cfg).run(closes=closes)
        equity = result["portfolio_value"]

        metrics = score_equity(equity)
        # Split-half robustness: score each disjoint half independently.
        mid = equity.index[len(equity) // 2]
        first_half = score_equity(equity[equity.index < mid])
        second_half = score_equity(equity[equity.index >= mid])

        rows.append(
            {
                "lookback": lookback,
                "top_n": top_n,
                "dd": dd,
                "pos_mom": pos,
                **metrics,
                "calmar_h1": first_half["calmar"],
                "calmar_h2": second_half["calmar"],
                "cagr_h1": first_half["cagr"],
                "cagr_h2": second_half["cagr"],
            }
        )

    df = pd.DataFrame(rows)
    # "Hard to lose" composite: reward Calmar + rolling-year win, penalize a bad
    # worst-year, and require both halves to be positive (robustness gate).
    df["robust"] = (df["cagr_h1"] > 0) & (df["cagr_h2"] > 0)
    df["score"] = (
        df["calmar"].clip(lower=0)
        + 2.0 * df["rolling_year_win"]
        + 3.0 * df["worst_year"].clip(upper=0)  # negative worst-year hurts
        + df["robust"].astype(float) * 0.5
    )
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def load_closes(base: BacktestConfig) -> pd.DataFrame:
    """Load aligned close prices once for reuse across the sweep."""
    return BacktestEngine(base).load_prices()


def main() -> None:
    """Run the sweep from the command line and print the ranked table."""
    base = BacktestConfig()
    closes = load_closes(base)
    df = run_sweep(closes, base)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)

    show = df[
        [
            "lookback", "top_n", "dd", "pos_mom",
            "cagr", "max_drawdown", "calmar", "sharpe",
            "rolling_year_win", "worst_year", "robust", "score",
        ]
    ].copy()
    for col in ["cagr", "max_drawdown", "rolling_year_win", "worst_year"]:
        show[col] = (show[col] * 100).round(1)
    show["calmar"] = show["calmar"].round(2)
    show["sharpe"] = show["sharpe"].round(2)
    show["score"] = show["score"].round(3)

    print("\n===== Top 15 by 'hard to lose' score =====")
    print(show.head(15).to_string(index=False))
    print("\n===== Current defaults (lookback=12, top_n=3, dd=0.15, pos_mom=False) =====")
    cur = df[
        (df.lookback == 12) & (df.top_n == 3) & (df.dd == 0.15) & (~df.pos_mom)
    ]
    print(cur[["cagr", "max_drawdown", "calmar", "sharpe", "rolling_year_win", "worst_year"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
