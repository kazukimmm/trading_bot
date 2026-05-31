"""CLI entry point to run the dual-momentum backtest, plot, and cache results.

Usage:
    python run_backtest.py            # 2010-01-01 .. today, $1000 start
    python run_backtest.py --no-plots # skip chart generation
"""

from __future__ import annotations

import argparse

from backtesting.backtest_engine import BacktestConfig, BacktestEngine
from backtesting.performance_metrics import (
    compute_metrics,
    export_results,
    market_regime,
    save_plots,
)
from core.risk_manager import adjust_position_sizes
from core.strategy import compute_lookback_return, generate_signals
from utils.logger import get_logger

_logger = get_logger(__name__)


def _build_holdings(closes, config: BacktestConfig, total_value: float) -> list[dict]:
    """Compute the strategy's current target holdings from latest prices.

    Args:
        closes: Aligned close-price DataFrame.
        config: Backtest configuration.
        total_value: Portfolio value to allocate across holdings.

    Returns:
        List of ``{symbol, weight, value, return_pct}`` dicts.
    """
    prices_dict = {t: closes[t].dropna() for t in config.watchlist}
    signals = generate_signals(
        prices_dict,
        watchlist=config.watchlist,
        benchmark=config.benchmark,
        safe_asset=config.safe_asset,
        lookback_months=config.lookback_months,
        top_n=config.top_n,
        require_positive_momentum=config.require_positive_momentum,
    )
    allocations = adjust_position_sizes(
        signals,
        total_value,
        max_position_size=config.max_position_size,
        exempt_symbols=frozenset({config.safe_asset}),
    )
    holdings: list[dict] = []
    for symbol, weight in signals.items():
        # Trailing 1-month return as a recent-performance proxy per holding.
        try:
            ret = compute_lookback_return(closes[symbol].dropna(), 1)
        except Exception:  # noqa: BLE001 - non-critical display value
            ret = 0.0
        holdings.append(
            {
                "symbol": symbol,
                "weight": round(weight, 3),
                "value": round(allocations.get(symbol, 0.0), 2),
                "return_pct": round(ret * 100, 2),
            }
        )
    return holdings


def main() -> None:
    """Run the backtest from the command line."""
    parser = argparse.ArgumentParser(description="Dual-momentum strategy backtest.")
    parser.add_argument("--start", default="2010-01-01", help="ISO start date.")
    parser.add_argument(
        "--capital", type=float, default=1000.0, help="Initial capital (USD)."
    )
    parser.add_argument("--no-plots", action="store_true", help="Skip chart output.")
    args = parser.parse_args()

    config = BacktestConfig(initial_capital=args.capital, start=args.start)
    engine = BacktestEngine(config)

    _logger.info("Loading prices and running backtest from %s ...", args.start)
    closes = engine.load_prices()
    result = engine.run(closes=closes)
    report = compute_metrics(result)
    print(report.as_text())

    # Current regime + target holdings for the dashboard cache.
    bench_return = compute_lookback_return(
        closes[config.benchmark].dropna(), config.lookback_months
    )
    regime = market_regime(bench_return)
    total_value = float(result["portfolio_value"].iloc[-1])
    holdings = _build_holdings(closes, config, total_value)

    export_results(
        result,
        report,
        extra={
            "market_regime": regime,
            "holdings": holdings,
            "as_of": str(result.index[-1].date()),
        },
    )

    if not args.no_plots:
        paths = save_plots(result)
        _logger.info("Saved charts: %s", ", ".join(str(p) for p in paths))


if __name__ == "__main__":
    main()
