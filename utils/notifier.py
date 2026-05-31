"""Slack notifications via incoming webhook.

Failures to notify are logged but never raised to the caller, so a Slack outage
cannot break the trading loop. A missing webhook URL disables notifications.
"""

from __future__ import annotations

import json

import requests

from utils.logger import get_logger

_logger = get_logger(__name__)


class SlackNotifier:
    """Sends formatted messages to a Slack incoming webhook."""

    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        """Initialize the notifier.

        Args:
            webhook_url: Slack incoming-webhook URL. Empty disables sending.
            timeout: HTTP timeout in seconds.
        """
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.enabled = bool(webhook_url)

    def _send(self, text: str) -> None:
        """Post raw text to Slack, swallowing/logging any failure.

        Args:
            text: Message body (Slack mrkdwn supported).
        """
        if not self.enabled:
            _logger.debug("Slack disabled; message suppressed: %s", text)
            return
        try:
            response = requests.post(
                self.webhook_url,
                data=json.dumps({"text": text}),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            _logger.error("Failed to send Slack notification: %s", exc)

    def notify_rebalance(self, allocations: dict[str, float], mode: str) -> None:
        """Notify that a rebalance executed.

        Args:
            allocations: Mapping of ticker -> dollar amount ordered.
            mode: ``"risk_on"`` or ``"risk_off"``.
        """
        lines = "\n".join(
            f"  • {ticker}: ${amount:,.2f}" for ticker, amount in allocations.items()
        )
        self._send(f"🔄 *Rebalance executed* (mode: {mode})\n{lines}")

    def notify_drawdown(self, current_value: float, peak_value: float) -> None:
        """Notify that the drawdown limit triggered a full de-risk.

        Args:
            current_value: Current portfolio equity.
            peak_value: Running peak equity.
        """
        drop = 1.0 - current_value / peak_value if peak_value else 0.0
        self._send(
            "⚠️ *Drawdown limit hit — closing all positions*\n"
            f"  • Equity: ${current_value:,.2f}\n"
            f"  • Peak: ${peak_value:,.2f}\n"
            f"  • Drawdown: {drop:.2%}"
        )

    def notify_error(self, context: str, error: Exception) -> None:
        """Notify that an error occurred.

        Args:
            context: Short description of what was being attempted.
            error: The exception that was raised.
        """
        self._send(f"❌ *Error during {context}*\n  • {type(error).__name__}: {error}")

    def notify_strategy_advice(self, trigger_reason: str, urgency: str) -> None:
        """Notify that an AI strategy-improvement advice was generated.

        Args:
            trigger_reason: Why the review fired (e.g. 3 down months).
            urgency: ``"low"`` / ``"medium"`` / ``"high"``.
        """
        icon = "⚠️" if urgency in {"medium", "high"} else "💡"
        self._send(
            f"{icon} *戦略改善提案が生成されました*\n"
            f"  • きっかけ: {trigger_reason}\n"
            f"  • 緊急度: {urgency}\n"
            "  ダッシュボードで詳細を確認してください（自動変更はしません）。"
        )

    def notify_monthly_summary(self, summary: str) -> None:
        """Send a monthly performance summary.

        Args:
            summary: Pre-formatted summary text.
        """
        self._send(f"📊 *Monthly summary*\n{summary}")
