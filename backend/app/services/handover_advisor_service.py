"""离职交接建议顾问(specs/001 T042 · 宪法原则 V:LLM 产出为建议,非最终处置)。

可插拔:
  · `RuleBasedHandoverAdvisor`(默认)—— 零外部依赖、确定性:一律 MANUAL_REVIEW、置信度 0,
    高关键级资产给更显式的复核理由。无 key/无网络时永远可用。
  · `LLMHandoverAdvisor` —— 当 `HANDOVER_LLM_ENABLED` 且解析出可用的 OpenAI 兼容 LLM 配置时启用。
    **批量一次**问询,产出每个资产的 {推荐动作, 置信度, 理由}。任何异常/非法输出 → 降级(返回空,
    端点回落 MANUAL_REVIEW),绝不阻断交接分析。

无论哪种顾问,产出的都只是 HandoverItem 的**建议字段**(recommended_action/risk_reason/confidence);
item 状态仍为 PROPOSED,审批闸门不被绕过(原则 V:任何处置须人工审批后执行)。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import resolve_llm, settings
from app.models.control_plane import HandoverAction
from app.services.llm_http import LLMConfigError, validate_llm_base_url

logger = logging.getLogger(__name__)

# 顾问可建议的动作全集(按枚举值索引,用于校验 LLM 输出)
_ACTION_BY_VALUE = {action.value: action for action in HandoverAction}


@dataclass(slots=True)
class AssetContext:
    asset_id: int
    name: str
    asset_type: str
    criticality: str
    status: str
    description: str | None = None


@dataclass(slots=True)
class Recommendation:
    recommended_action: HandoverAction
    confidence: float  # 0..1
    rationale: str


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in advisor response")
    return json.loads(text[start : end + 1])


def _clamp_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def _coerce_action(value: Any) -> HandoverAction | None:
    if isinstance(value, str):
        return _ACTION_BY_VALUE.get(value.strip().lower())
    return None


class HandoverAdvisor(Protocol):
    mode: str

    async def recommend(self, assets: list[AssetContext]) -> dict[int, Recommendation]:
        """返回 {asset_id: Recommendation}。未覆盖到的资产由端点回落 MANUAL_REVIEW。"""
        ...


_RULE_DEFAULT_REASON = "规则版顾问:默认 MANUAL_REVIEW,由审批人结合资产关键级与活动情况人工判定处置。"


class RuleBasedHandoverAdvisor:
    """确定性兜底:一律 MANUAL_REVIEW、置信度 0(表示无模型判断,必须人工复核)。"""

    mode = "rule_based"

    async def recommend(self, assets: list[AssetContext]) -> dict[int, Recommendation]:
        result: dict[int, Recommendation] = {}
        for asset in assets:
            reason = _RULE_DEFAULT_REASON
            if asset.criticality.lower() in {"high", "critical"}:
                reason = f"高关键级资产(criticality={asset.criticality}),建议优先人工复核交接处置。"
            result[asset.asset_id] = Recommendation(
                recommended_action=HandoverAction.MANUAL_REVIEW,
                confidence=0.0,
                rationale=reason,
            )
        return result


class LLMHandoverAdvisor:
    """OpenAI 兼容 LLM 顾问。批量问询,异常即禁用并降级(返回空 → 端点回落规则版)。"""

    mode = "llm"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
        max_assets: int = 50,
    ):
        self.api_key = api_key
        # 纵深防御:拒绝指向私网/环回/非 http(s) 的 base_url(见 llm_http）。
        self.base_url = validate_llm_base_url(base_url).rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_assets = max_assets
        self._enabled = True

    def is_available(self) -> bool:
        return bool(self._enabled and self.api_key and self.base_url and self.model)

    async def recommend(self, assets: list[AssetContext]) -> dict[int, Recommendation]:
        if not self.is_available() or not assets:
            return {}
        # 超过上限的资产不进 prompt(控制长度),端点对未覆盖项回落 MANUAL_REVIEW
        batch = assets[: self.max_assets]
        try:
            data = await self._call(self._build_prompt(batch))
        except Exception as exc:  # 网络/超时/HTTP/解析任意失败 → 禁用并降级,绝不阻断分析
            logger.warning("handover LLM advisor failed, falling back to rule-based: %s", exc)
            self._enabled = False
            return {}
        return self._parse(data, batch)

    def _build_prompt(self, assets: list[AssetContext]) -> str:
        items = [
            {
                "asset_id": a.asset_id,
                "name": a.name,
                "asset_type": a.asset_type,
                "criticality": a.criticality,
                "status": a.status,
                "description": a.description or "",
            }
            for a in assets
        ]
        actions = ", ".join(sorted(_ACTION_BY_VALUE))
        return (
            "你是企业 AI 资产离职交接顾问。针对每个资产给出交接处置**建议**(最终需人工审批)。\n"
            f"recommended_action 必须取自:{actions}。\n"
            "confidence 为 0~1 小数,表示你对该建议的把握;rationale 用一句中文说明依据。\n"
            '只返回 JSON,形如 {"recommendations":[{"asset_id":1,'
            '"recommended_action":"transfer_owner","confidence":0.8,"rationale":"..."}]}。\n'
            f"资产清单:\n{json.dumps(items, ensure_ascii=False)}"
        )

    async def _call(self, prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
            "messages": [
                {"role": "system", "content": "You are a careful enterprise handover advisor. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        message = body["choices"][0]["message"]["content"]
        return _extract_json(message)

    def _parse(self, data: dict[str, Any], assets: list[AssetContext]) -> dict[int, Recommendation]:
        valid_ids = {a.asset_id for a in assets}
        out: dict[int, Recommendation] = {}
        for row in data.get("recommendations") or []:
            if not isinstance(row, dict):
                continue
            # LLM 常把 JSON 数字给成 float(1.0),也可能给字符串("1"),都要能收
            raw_id = row.get("asset_id")
            if not isinstance(raw_id, (int, float, str)) or isinstance(raw_id, bool):
                continue
            try:
                asset_id = int(float(raw_id))
            except (TypeError, ValueError):
                continue
            if asset_id not in valid_ids:
                continue  # 忽略幻觉出的资产 id
            # 非法/未知动作一律退回 MANUAL_REVIEW(LLM 输出不可信,防越权动作)
            action = _coerce_action(row.get("recommended_action")) or HandoverAction.MANUAL_REVIEW
            rationale = (str(row.get("rationale") or "").strip() or "LLM 顾问未提供理由")[:2000]
            out[asset_id] = Recommendation(
                recommended_action=action,
                confidence=_clamp_confidence(row.get("confidence")),
                rationale=rationale,
            )
        return out


def build_handover_advisor() -> HandoverAdvisor:
    """工厂:开启且解析出完整 LLM 配置时用 LLM 顾问,否则规则版兜底(key 闸控降级)。

    返回实例的 ``.mode`` 即"当前激活的顾问"标识(``"llm"`` / ``"rule_based"``),
    端点据此构造共享的 ``ai_assist`` 降级指示(见 :func:`build_ai_assist`)。
    """
    if not settings.HANDOVER_LLM_ENABLED:
        return RuleBasedHandoverAdvisor()
    # HANDOVER_LLM_* 优先,缺失回落规范 DUCKDOCK_LLM_*(L2-PROVIDER-UNIFY）。
    api_key, base_url, model = resolve_llm("HANDOVER_LLM")
    if not (api_key and base_url and model):
        return RuleBasedHandoverAdvisor()
    try:
        return LLMHandoverAdvisor(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=settings.HANDOVER_LLM_TIMEOUT_SECONDS,
        )
    except LLMConfigError as exc:  # base_url 非法 → 不阻断,回落规则版兜底
        logger.warning("handover LLM advisor disabled (invalid base_url): %s", exc)
        return RuleBasedHandoverAdvisor()


def build_ai_assist(advisor: HandoverAdvisor, *, llm_produced: bool) -> dict[str, Any]:
    """共享降级指示:让"这是启发式而非 AI"在 API 响应里可见,杜绝静默冒充。

    契约(全消费点一致):``{"mode": "llm"|"baseline", "degraded": bool, "reason": str|None}``。
    - LLM 顾问真正产出了建议 → ``mode="llm"``、``degraded=False``、``reason=None``。
    - 规则版兜底(无 key 解析出 → 工厂直接给规则版),或激活的 LLM 顾问在调用时失败被禁用、
      静默回落 MANUAL_REVIEW → ``mode="baseline"``、``degraded=True`` 并给出原因。

    ``llm_produced`` 表示激活的 LLM 顾问本次确实返回了建议(端点据 recommendations 是否非空判定)。
    规则版 ``advisor.mode != "llm"``,``llm_produced`` 取值无关紧要。
    """
    if advisor.mode == "llm" and llm_produced:
        return {"mode": "llm", "degraded": False, "reason": None}
    if advisor.mode == "llm":
        # 激活的是 LLM 顾问,但本次没产出任何建议(调用失败被禁用 → 逐项回落 MANUAL_REVIEW）。
        return {
            "mode": "baseline",
            "degraded": True,
            "reason": "llm advisor produced no recommendations; fell back to rule-based MANUAL_REVIEW",
        }
    return {
        "mode": "baseline",
        "degraded": True,
        "reason": "no LLM key resolved; using rule-based handover advisor",
    }
