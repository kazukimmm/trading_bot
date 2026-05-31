"""戦略改善提案（AI診断）エンジン。

直近の成績が悪化したとき（3ヶ月連続マイナス／勝率低下／ドローダウン接近）に、
Claude API を呼び出して「保守的（資産保全優先）」の観点から改善案を生成します。

設計方針:
    * 自動で設定を変更することは絶対にしない。提案を出すだけで、最終判断は人間。
    * ``ANTHROPIC_API_KEY`` が未設定、または ``anthropic`` 未インストール、API 失敗時は
      ルールベースの簡易フォールバック提案を返す（ダッシュボードは常に動く）。
    * 生成した提案は ``logs/strategy_advice.json`` に保存し、ダッシュボードで表示。
      「確認した」操作で dismissed フラグを立て、次の診断まで非表示にする。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from utils.logger import get_logger

_logger = get_logger(__name__)

_ADVICE_FILE = Path(__file__).resolve().parent.parent / "logs" / "strategy_advice.json"
# 既定モデル（環境変数 ANTHROPIC_MODEL で上書き可能）。
_DEFAULT_MODEL = "claude-sonnet-4-5"
_VALID_URGENCY = {"low", "medium", "high"}


def _save_advice(advice: dict[str, Any], path: Path = _ADVICE_FILE) -> None:
    """生成した提案を JSON に保存する（dismissed=False で初期化）。"""
    payload = dict(advice)
    payload.setdefault("triggered_at", date.today().isoformat())
    payload["dismissed"] = False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _logger.info("Saved strategy advice (urgency=%s).", payload.get("urgency"))


def load_advice(path: Path = _ADVICE_FILE) -> dict[str, Any] | None:
    """保存済みの提案を読み込む（無ければ None）。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def dismiss_advice(path: Path = _ADVICE_FILE) -> bool:
    """保存済みの提案を「確認済み（非表示）」にする。

    Returns:
        更新できたら True、提案ファイルが無ければ False。
    """
    advice = load_advice(path)
    if advice is None:
        return False
    advice["dismissed"] = True
    path.write_text(json.dumps(advice, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


class StrategyAdvisor:
    """パフォーマンスを分析し、Claude API で改善提案を生成するクラス。"""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        """アドバイザーを初期化する。

        Args:
            api_key: Anthropic API キー（省略時は環境変数 ANTHROPIC_API_KEY）。
            model: 使用モデル（省略時は環境変数 ANTHROPIC_MODEL または既定値）。
        """
        self.api_key = (api_key or os.getenv("ANTHROPIC_API_KEY", "")).strip()
        self.model = (model or os.getenv("ANTHROPIC_MODEL", _DEFAULT_MODEL)).strip()

    # --- トリガー判定 ------------------------------------------------------

    def should_trigger_review(
        self,
        monthly_returns: list[float],
        consecutive_loss_threshold: int = 3,
        current_drawdown: float | None = None,
    ) -> bool:
        """診断を起動すべきかを判定する。

        以下のいずれかに該当したら True：
            1. 直近 ``consecutive_loss_threshold`` ヶ月連続でマイナスリターン
            2. 直近6ヶ月の勝率が40%以下
            3. 現在のドローダウンが10%以上（制限15%に接近）

        Args:
            monthly_returns: 月次リターン（％）の時系列（古い順）。
            consecutive_loss_threshold: 連続マイナスの判定月数。
            current_drawdown: 現在のドローダウン（フラクション。0.10 = 10%）。

        Returns:
            診断を起動すべきなら True。
        """
        n = consecutive_loss_threshold
        if len(monthly_returns) >= n and all(r < 0 for r in monthly_returns[-n:]):
            return True
        if len(monthly_returns) >= 6:
            last6 = monthly_returns[-6:]
            win_rate = sum(1 for r in last6 if r > 0) / 6.0
            if win_rate <= 0.40:
                return True
        if current_drawdown is not None and current_drawdown >= 0.10:
            return True
        return False

    def _trigger_reason(
        self,
        monthly_returns: list[float],
        consecutive_loss_threshold: int,
        current_drawdown: float | None,
    ) -> str:
        """起動理由を日本語で返す（複数該当時は最初の1つ）。"""
        n = consecutive_loss_threshold
        if len(monthly_returns) >= n and all(r < 0 for r in monthly_returns[-n:]):
            return f"直近{n}ヶ月連続マイナスリターン"
        if len(monthly_returns) >= 6:
            last6 = monthly_returns[-6:]
            win_rate = sum(1 for r in last6 if r > 0) / 6.0
            if win_rate <= 0.40:
                return f"直近6ヶ月の勝率が{win_rate * 100:.0f}%に低下"
        if current_drawdown is not None and current_drawdown >= 0.10:
            return f"ドローダウンが{current_drawdown * 100:.0f}%に拡大（制限15%に接近）"
        return "成績悪化の兆候"

    @staticmethod
    def _healthy_reason(
        monthly_returns: list[float], current_drawdown: float | None
    ) -> str:
        """良好時の「現状診断」理由を日本語で返す。"""
        parts: list[str] = []
        if monthly_returns:
            recent = monthly_returns[-1]
            parts.append(f"直近月次リターン {recent:+.1f}%")
        if len(monthly_returns) >= 6:
            last6 = monthly_returns[-6:]
            win_rate = sum(1 for r in last6 if r > 0) / 6.0
            parts.append(f"直近6ヶ月の勝率 {win_rate * 100:.0f}%")
        if current_drawdown is not None:
            parts.append(f"ドローダウン {current_drawdown * 100:.0f}%")
        detail = "、".join(parts)
        return f"成績は安定しています（{detail}）" if detail else "成績は安定しています"

    # --- 提案生成 ----------------------------------------------------------

    def _build_prompt(
        self,
        performance_data: dict,
        decision_history: list,
        market_conditions: dict,
        *,
        triggered: bool = False,
    ) -> str:
        """Claude へ渡すプロンプトを組み立てる。"""
        assessment = (
            "成績が悪化しており、改善の余地がないか診断してください。"
            if triggered
            else "成績は概ね良好です。現状維持で問題ないかを確認し、"
            "あえて変更するなら何かを助言してください（無理に変える必要はありません）。"
        )
        return f"""あなたは保守的な投資戦略のアドバイザーです。
以下のデータを分析して、改善提案をJSON形式で返してください。

## 現在の状況
{assessment}

## 現在の戦略
デュアルモメンタム戦略（ルックバック9ヶ月・上位3銘柄保有・安全資産BIL退避）

## パフォーマンスデータ
{json.dumps(performance_data, ensure_ascii=False)}

## 直近の判断履歴
{json.dumps(decision_history, ensure_ascii=False)}

## 市場環境
{json.dumps(market_conditions, ensure_ascii=False)}

## 回答形式（JSONのみ・日本語で）
{{
  "trigger_reason": "診断のきっかけ",
  "diagnosis": "現状の問題点（2〜3文）",
  "suggestions": [
    {{
      "title": "改善案のタイトル",
      "detail": "具体的な変更内容と期待効果",
      "risk_level": "低/中/高"
    }}
  ],
  "keep_current": true/false,
  "urgency": "low/medium/high"
}}

重要: 大きな変更より小さな調整を優先してください。
      資産保全を最優先にしてください。
      JSON以外のテキストは出力しないでください。"""

    def generate_advice(
        self,
        performance_data: dict,
        decision_history: list,
        market_conditions: dict,
        *,
        consecutive_loss_threshold: int = 3,
    ) -> dict[str, Any]:
        """改善提案を生成する（API 失敗時はフォールバック）。

        Args:
            performance_data: 成績データ（``monthly_returns``/``current_drawdown``
                などを含む dict）。
            decision_history: 直近の判断履歴リスト。
            market_conditions: 市場環境（regime など）。
            consecutive_loss_threshold: 連続マイナス判定月数。

        Returns:
            ``trigger_reason``/``diagnosis``/``suggestions``/``keep_current``/
            ``urgency`` を持つ dict。
        """
        monthly_returns = list(performance_data.get("monthly_returns", []))
        current_dd = performance_data.get("current_drawdown")
        triggered = self.should_trigger_review(
            monthly_returns, consecutive_loss_threshold, current_dd
        )
        reason = (
            self._trigger_reason(monthly_returns, consecutive_loss_threshold, current_dd)
            if triggered
            else self._healthy_reason(monthly_returns, current_dd)
        )

        if self.api_key:
            prompt = self._build_prompt(
                performance_data, decision_history, market_conditions, triggered=triggered
            )
            try:
                advice = self._call_claude(prompt)
                advice.setdefault("trigger_reason", reason)
                return self._normalize(advice)
            except Exception as exc:  # noqa: BLE001 - any API failure → fallback
                _logger.warning("Claude advice failed (%s); using fallback.", exc)
        else:
            _logger.info("ANTHROPIC_API_KEY unset; using rule-based fallback advice.")

        return self._normalize(self._fallback_advice(reason, current_dd, triggered))

    def _call_claude(self, prompt: str) -> dict[str, Any]:
        """Claude API を呼び出し、返ってきた JSON を dict にして返す。"""
        from anthropic import Anthropic  # 遅延 import（未インストールでも他機能は動く）

        client = Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        ).strip()
        # 念のためコードフェンスを除去。
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"): text.rfind("}") + 1]
        return json.loads(text)

    @staticmethod
    def _fallback_advice(
        reason: str, current_dd: float | None, triggered: bool = True
    ) -> dict[str, Any]:
        """API 不使用時のルールベース提案（保守的・小さな調整中心）。

        ``triggered`` が False（成績良好）の場合は「現状維持を推奨」する
        前向きな診断を返し、ダッシュボードに常時表示できるようにする。
        """
        if not triggered:
            return {
                "trigger_reason": reason,
                "diagnosis": (
                    "現在の戦略は想定どおり機能しています。連続マイナスや勝率低下、"
                    "ドローダウンの拡大といった注意サインは見られません。"
                    "資産保全のルール（15%で退避）も有効に働いています。"
                ),
                "suggestions": [
                    {
                        "title": "現状維持を推奨",
                        "detail": "成績が安定している局面では、設定を変えないことが"
                        "最善の選択です。短期の値動きに反応して変更すると、"
                        "かえって成績を損なうリスクがあります。",
                        "risk_level": "低",
                    },
                    {
                        "title": "（任意）watchlist の分散状況を点検",
                        "detail": "余裕があるときに、保有候補の偏り（同一セクター集中など）"
                        "がないかを確認しておくと、急変時の耐性が高まります。"
                        "変更は必須ではありません。",
                        "risk_level": "低",
                    },
                ],
                "keep_current": True,
                "urgency": "low",
            }

        urgency = "medium" if (current_dd is not None and current_dd >= 0.10) else "low"
        return {
            "trigger_reason": reason,
            "diagnosis": (
                "成績が一時的に悪化しています。これは戦略の欠陥ではなく、相場環境が "
                "戦略と噛み合っていない可能性があります（AI診断は未設定のため簡易診断です）。"
                "資産保全のルールは機能しているため、まずは小さな調整から検討してください。"
            ),
            "suggestions": [
                {
                    "title": "現状維持して様子を見る",
                    "detail": "ドローダウン制限（15%）と安全資産への退避は機能しています。"
                    "短期の不調で大きく変えると、回復局面を取り逃すリスクがあります。",
                    "risk_level": "低",
                },
                {
                    "title": "ルックバック期間の見直しを検討",
                    "detail": "相場の転換が速い局面では、9ヶ月→6ヶ月へ短縮すると反応が早く"
                    "なります。ただし売買頻度とコストが増える点に注意。",
                    "risk_level": "中",
                },
            ],
            "keep_current": urgency == "low",
            "urgency": urgency,
        }

    @staticmethod
    def _normalize(advice: dict[str, Any]) -> dict[str, Any]:
        """提案 dict のキー・型を安全に整える。"""
        suggestions = []
        for s in advice.get("suggestions", []) or []:
            if isinstance(s, dict):
                suggestions.append(
                    {
                        "title": str(s.get("title", "")),
                        "detail": str(s.get("detail", "")),
                        "risk_level": str(s.get("risk_level", "低")),
                    }
                )
        urgency = str(advice.get("urgency", "low")).lower()
        if urgency not in _VALID_URGENCY:
            urgency = "low"
        return {
            "trigger_reason": str(advice.get("trigger_reason", "")),
            "diagnosis": str(advice.get("diagnosis", "")),
            "suggestions": suggestions,
            "keep_current": bool(advice.get("keep_current", False)),
            "urgency": urgency,
        }

    # --- 永続化（保存）-----------------------------------------------------

    def run_and_save(
        self,
        performance_data: dict,
        decision_history: list,
        market_conditions: dict,
    ) -> dict[str, Any]:
        """提案を生成して ``logs/strategy_advice.json`` に保存し、返す。"""
        advice = self.generate_advice(performance_data, decision_history, market_conditions)
        _save_advice(advice)
        return advice
