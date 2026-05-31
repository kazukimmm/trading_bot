"""売買判断とその結果の記録（判断振り返り用）。

リバランスや退避のたびに「なぜその判断をしたか」を記録し、月次レビュー時に
「その後どうなったか」を追記します。これにより、後から判断の良し悪しを
振り返れるようにします。

永続化先について:
    元の仕様では Firestore を想定していますが、本プロジェクトは他の状態
    （logs/state.json, monthly_snapshots.json）と同様にローカル JSON
    （``logs/decision_history.json``）へ保存します。Firestore の認証情報や
    追加依存なしに、ローカルでも Railway でもそのまま動作します。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from utils.logger import get_logger

_logger = get_logger(__name__)

_HISTORY_FILE = Path(__file__).resolve().parent.parent / "logs" / "decision_history.json"

# 結果ラベルの取りうる値。
RESULT_SUCCESS = "成功"
RESULT_FAILURE = "失敗"
RESULT_ONGOING = "継続中"


class DecisionLogger:
    """売買判断とその結果を JSON ファイルに記録するクラス。

    注文執行時に :meth:`log_decision` で判断を記録し、月次レビュー時に
    :meth:`update_result` で結果を追記します。
    """

    def __init__(self, history_file: Path = _HISTORY_FILE) -> None:
        """ロガーを初期化する。

        Args:
            history_file: 判断履歴を保存する JSON ファイルのパス。
        """
        self.history_file = history_file

    # --- 内部ユーティリティ ------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        """履歴リストを読み込む（存在しない/壊れている場合は空リスト）。"""
        if not self.history_file.exists():
            return []
        try:
            data = json.loads(self.history_file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            _logger.warning("Corrupt decision history (%s); starting fresh.", exc)
            return []
        return data if isinstance(data, list) else []

    def _save(self, records: list[dict[str, Any]]) -> None:
        """履歴リストをファイルへ書き込む。"""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- 公開 API ---------------------------------------------------------

    def log_decision(
        self,
        date: str,
        symbol: str,
        action: str,
        reason: str,
        momentum_score: float,
        price_at_decision: float,
        market_regime: str,
    ) -> str:
        """新しい売買判断を記録する。

        Args:
            date: 判断日（``"YYYY-MM-DD"``）。
            symbol: 対象ティッカー。
            action: ``"buy"`` / ``"sell"`` / ``"hold"`` / ``"retreat"``。
            reason: 判断理由（自然言語）。
            momentum_score: 判断時のモメンタムスコア（％などの数値）。
            price_at_decision: 判断時の価格。
            market_regime: ``"BULL"`` / ``"NEUTRAL"`` / ``"BEAR"``。

        Returns:
            生成した判断 ID（後で :meth:`update_result` に渡す）。
        """
        decision_id = uuid.uuid4().hex
        record = {
            "id": decision_id,
            "date": date,
            "symbol": symbol,
            "action": action,
            "reason": reason,
            "momentum_score": round(float(momentum_score), 2),
            "price_at_decision": round(float(price_at_decision), 2),
            "price_now": None,
            "return_pct": None,
            "result_label": RESULT_ONGOING,
            "market_regime": market_regime,
        }
        records = self._load()
        records.append(record)
        self._save(records)
        _logger.info(
            "Logged decision %s: %s %s (%s)", decision_id[:8], action, symbol, market_regime
        )
        return decision_id

    def update_result(
        self,
        decision_id: str,
        price_now: float,
        return_pct: float,
        result_label: str,
    ) -> bool:
        """既存判断に結果を追記する（月次レビュー時に呼ぶ）。

        Args:
            decision_id: :meth:`log_decision` が返した ID。
            price_now: 現在価格。
            return_pct: 判断後のリターン（％）。
            result_label: ``"成功"`` / ``"失敗"`` / ``"継続中"``。

        Returns:
            該当 ID を更新できたら True、見つからなければ False。
        """
        records = self._load()
        for record in records:
            if record.get("id") == decision_id:
                record["price_now"] = round(float(price_now), 2)
                record["return_pct"] = round(float(return_pct), 2)
                record["result_label"] = result_label
                self._save(records)
                return True
        _logger.warning("update_result: decision %s not found.", decision_id)
        return False

    def get_recent_decisions(self, limit: int = 10) -> list[dict[str, Any]]:
        """直近の判断履歴を新しい順に返す。

        Args:
            limit: 取得する最大件数。

        Returns:
            判断レコードのリスト（新しい順）。
        """
        records = self._load()
        # date（YYYY-MM-DD）降順 → 同日内は登録順の逆。
        ordered = sorted(records, key=lambda r: r.get("date", ""), reverse=True)
        return ordered[:limit]

    def open_decisions(self) -> list[dict[str, Any]]:
        """結果未確定（継続中）の判断のみを返す（月次レビュー用）。"""
        return [r for r in self._load() if r.get("result_label") == RESULT_ONGOING]
