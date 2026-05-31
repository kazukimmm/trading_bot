"""Performance analytics and plots for backtest results."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from utils.logger import get_logger

_logger = get_logger(__name__)
_TRADING_DAYS = 252
_RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass(frozen=True)
class PerformanceReport:
    """Summary statistics for an equity curve.

    Attributes:
        total_return: Cumulative return over the full period (fraction).
        cagr: Compound annual growth rate (fraction).
        max_drawdown: Worst peak-to-trough decline (fraction, positive number).
        sharpe_ratio: Annualized Sharpe ratio (excess over 0 risk-free).
        monthly_win_rate: Fraction of months with a positive return.
        benchmark_total_return: Benchmark cumulative return (fraction).
        benchmark_cagr: Benchmark CAGR (fraction).
        benchmark_max_drawdown: Benchmark worst drawdown (fraction).
    """

    total_return: float
    cagr: float
    max_drawdown: float
    sharpe_ratio: float
    monthly_win_rate: float
    benchmark_total_return: float
    benchmark_cagr: float
    benchmark_max_drawdown: float

    @property
    def vs_spy_alpha(self) -> float:
        """Annualized excess return over SPY buy & hold (CAGR difference)."""
        return self.cagr - self.benchmark_cagr

    def as_text(self) -> str:
        """Render the report as a human-readable multi-line string."""
        return (
            "===== Backtest Performance =====\n"
            f"Total return       : {self.total_return:>8.2%}   "
            f"(SPY B&H: {self.benchmark_total_return:.2%})\n"
            f"CAGR               : {self.cagr:>8.2%}   "
            f"(SPY B&H: {self.benchmark_cagr:.2%})\n"
            f"Max drawdown       : {self.max_drawdown:>8.2%}   "
            f"(SPY B&H: {self.benchmark_max_drawdown:.2%})\n"
            f"Sharpe ratio       : {self.sharpe_ratio:>8.2f}\n"
            f"Monthly win rate   : {self.monthly_win_rate:>8.2%}\n"
            f"Alpha vs SPY (CAGR): {self.vs_spy_alpha:>8.2%}\n"
            "================================"
        )

    def to_api_dict(self) -> dict[str, float]:
        """Return the summary in the percentage shape the dashboard API uses."""
        return {
            "cagr": round(self.cagr * 100, 2),
            "max_drawdown": round(self.max_drawdown * 100, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "win_rate": round(self.monthly_win_rate * 100, 2),
            "total_return": round(self.total_return * 100, 2),
            "vs_spy_alpha": round(self.vs_spy_alpha * 100, 2),
        }


def _max_drawdown(equity: pd.Series) -> float:
    """Return the maximum peak-to-trough drawdown as a positive fraction."""
    running_peak = equity.cummax()
    drawdown = 1.0 - equity / running_peak
    return float(drawdown.max())


def _cagr(equity: pd.Series) -> float:
    """Return the compound annual growth rate of an equity curve."""
    if len(equity) < 2:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    growth = float(equity.iloc[-1]) / float(equity.iloc[0])
    return growth ** (1.0 / years) - 1.0


def _sharpe(equity: pd.Series) -> float:
    """Return the annualized Sharpe ratio (risk-free rate assumed 0)."""
    daily_returns = equity.pct_change().dropna()
    std = float(daily_returns.std())
    if std == 0:
        return 0.0
    return float(daily_returns.mean()) / std * np.sqrt(_TRADING_DAYS)


def _monthly_win_rate(equity: pd.Series) -> float:
    """Return the fraction of calendar months with a positive return."""
    monthly = equity.resample("ME").last().pct_change().dropna()
    if monthly.empty:
        return 0.0
    return float((monthly > 0).mean())


def _monthly_returns_table(equity: pd.Series) -> pd.DataFrame:
    """Return a year x month matrix of monthly returns (in percent).

    Args:
        equity: Daily equity curve.

    Returns:
        DataFrame indexed by year, columns 1..12, values in percent (NaN where
        a month has no data).
    """
    monthly = equity.resample("ME").last().pct_change().dropna() * 100.0
    frame = pd.DataFrame(
        {
            "year": monthly.index.year,
            "month": monthly.index.month,
            "ret": monthly.values,
        }
    )
    return frame.pivot(index="year", columns="month", values="ret")


def compute_metrics(result: pd.DataFrame) -> PerformanceReport:
    """Compute the full performance report from a backtest result.

    Args:
        result: DataFrame with ``portfolio_value`` and ``benchmark_value``.

    Returns:
        A populated :class:`PerformanceReport`.
    """
    equity = result["portfolio_value"]
    benchmark = result["benchmark_value"]

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    bench_total = float(benchmark.iloc[-1] / benchmark.iloc[0] - 1.0)

    return PerformanceReport(
        total_return=total_return,
        cagr=_cagr(equity),
        max_drawdown=_max_drawdown(equity),
        sharpe_ratio=_sharpe(equity),
        monthly_win_rate=_monthly_win_rate(equity),
        benchmark_total_return=bench_total,
        benchmark_cagr=_cagr(benchmark),
        benchmark_max_drawdown=_max_drawdown(benchmark),
    )


def save_plots(result: pd.DataFrame, output_dir: Path | None = None) -> list[Path]:
    """Generate and save the three standard backtest charts.

    Produces ``portfolio_growth.png`` (strategy vs SPY), ``drawdown.png``, and
    ``monthly_returns.png`` (a year x month heatmap). Matplotlib is imported
    lazily so headless test environments need not load it.

    Args:
        result: Backtest result DataFrame.
        output_dir: Destination directory (defaults to ``backtesting/results``).

    Returns:
        List of saved file paths.
    """
    import matplotlib

    matplotlib.use("Agg")  # headless backend
    import matplotlib.pyplot as plt

    out = output_dir or _RESULTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    equity = result["portfolio_value"]
    benchmark = result["benchmark_value"]
    drawdown = 1.0 - equity / equity.cummax()

    # 1. Portfolio growth vs SPY (normalized to 100).
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(
        equity.index, equity / equity.iloc[0] * 100, label="Strategy", color="#2dd4bf"
    )
    ax.plot(
        benchmark.index,
        benchmark / benchmark.iloc[0] * 100,
        label="SPY Buy & Hold",
        color="#f59e0b",
    )
    ax.set_title("Portfolio Growth (Strategy vs SPY)")
    ax.set_ylabel("Growth of 100")
    ax.legend()
    ax.grid(True, alpha=0.3)
    path = out / "portfolio_growth.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    saved.append(path)

    # 2. Drawdown.
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.fill_between(
        drawdown.index, -drawdown.values * 100, 0, color="#ef4444", alpha=0.5
    )
    ax.set_title("Drawdown")
    ax.set_ylabel("Drawdown (%)")
    ax.grid(True, alpha=0.3)
    path = out / "drawdown.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    saved.append(path)

    # 3. Monthly returns heatmap (year x month).
    table = _monthly_returns_table(equity)
    fig, ax = plt.subplots(figsize=(11, max(3, 0.45 * len(table) + 1)))
    data = table.reindex(columns=range(1, 13))
    vmax = float(np.nanmax(np.abs(data.values))) if data.size else 1.0
    im = ax.imshow(data.values, cmap="RdYlGn", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(12))
    ax.set_xticklabels(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
    ax.set_yticks(range(len(data.index)))
    ax.set_yticklabels([str(y) for y in data.index])
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            value = data.values[i, j]
            if not np.isnan(value):
                ax.text(
                    j, i, f"{value:.1f}", ha="center", va="center", fontsize=7, color="black"
                )
    ax.set_title("Monthly Returns (%)")
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    path = out / "monthly_returns.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    saved.append(path)

    return saved


def market_regime(benchmark_lookback_return: float) -> str:
    """Classify the market regime from the benchmark's lookback return.

    Args:
        benchmark_lookback_return: Benchmark trailing return (fraction).

    Returns:
        ``"BULL"`` (> +2%), ``"BEAR"`` (< -2%), else ``"NEUTRAL"``.
    """
    if benchmark_lookback_return > 0.02:
        return "BULL"
    if benchmark_lookback_return < -0.02:
        return "BEAR"
    return "NEUTRAL"


def export_results(
    result: pd.DataFrame,
    report: PerformanceReport,
    output_dir: Path | None = None,
    extra: dict | None = None,
) -> Path:
    """Write the backtest summary + equity history to a JSON cache.

    The dashboard reads this file so it never re-runs the backtest on request.

    Args:
        result: Backtest result DataFrame.
        report: Computed performance report.
        output_dir: Destination directory (defaults to ``backtesting/results``).
        extra: Optional extra fields merged into the JSON (e.g. holdings).

    Returns:
        Path to the written JSON file.
    """
    out = output_dir or _RESULTS_DIR
    out.mkdir(parents=True, exist_ok=True)

    equity = result["portfolio_value"]
    history = [
        {"date": idx.strftime("%Y-%m-%d"), "value": round(float(val), 2)}
        for idx, val in equity.items()
    ]

    payload: dict = {
        "generated_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": report.to_api_dict(),
        "report_full": {k: round(v, 6) for k, v in asdict(report).items()},
        "history": history,
    }
    if extra:
        payload.update(extra)

    path = out / "backtest_results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    _logger.info("Wrote backtest cache: %s (%d history points)", path, len(history))
    return path
