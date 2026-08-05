from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from app.core.config import resolve_llm, settings
from app.models.clinic import DIMENSIONS
from app.services.langfuse_service import langfuse_service
from app.services.llm_http import LLMConfigError, validate_llm_base_url

ASSETS_ROOT = Path(__file__).resolve().parents[1] / "clinic_assets"
RUBRICS_ROOT = ASSETS_ROOT / "rubrics"
PROMPTS_ROOT = ASSETS_ROOT / "judge_prompts"
logger = logging.getLogger(__name__)

DIMENSION_WEIGHTS = {dimension_id: weight for dimension_id, _, _, weight in DIMENSIONS}


@dataclass
class Evidence:
    reason: str
    skill: str | None = None
    snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "reason": self.reason,
            "snippet": self.snippet,
        }


@dataclass
class DimensionResult:
    score: float
    weight: int
    issues: list[str] = field(default_factory=list)
    details: str = ""
    trend: str = "stable"
    confidence: float | None = None
    reasoning_summary: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    # 每维分数来源:"deterministic"(纯启发式) | "llm"(LLM 评分已混入)。
    # 让 release-gate 与 UI 看得见某一维到底有没有经过 AI 评审。
    source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "weight": self.weight,
            "issues": self.issues,
            "details": self.details,
            "trend": self.trend,
            "confidence": None if self.confidence is None else round(self.confidence, 3),
            "reasoning_summary": self.reasoning_summary,
            "evidence": [item.to_dict() for item in self.evidence],
            "source": self.source,
        }


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B+"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C+"
    if score >= 50:
        return "C"
    return "D"


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in judge response")
    return json.loads(text[start : end + 1])


def _word_count(value: str | None) -> int:
    return len((value or "").split())


def _has_examples(markdown: str | None) -> bool:
    text = (markdown or "").lower()
    return "example" in text or "示例" in text or "usage" in text or "用法" in text


def _has_output_guidance(text: str | None) -> bool:
    content = (text or "").lower()
    return any(token in content for token in ["format", "output", "respond", "json", "markdown", "返回", "格式"])


def _has_constraints(text: str | None) -> bool:
    content = (text or "").lower()
    return any(token in content for token in ["do not", "never", "must", "should", "不要", "必须"])


def _header_count(markdown: str | None) -> int:
    return sum(1 for line in (markdown or "").splitlines() if line.lstrip().startswith("#"))


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_]+", "-", name.lower()).strip("-")


class ClinicJudgeProvider(Protocol):
    def is_available(self) -> bool: ...

    def evaluate_dimension(
        self,
        *,
        dimension_id: str,
        rubric: dict[str, Any],
        facts: dict[str, Any],
        sampled_skills: list[dict[str, Any]],
    ) -> dict[str, Any] | None: ...


class NoopClinicJudgeProvider:
    def is_available(self) -> bool:
        return False

    def evaluate_dimension(
        self,
        *,
        dimension_id: str,
        rubric: dict[str, Any],
        facts: dict[str, Any],
        sampled_skills: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        return None


class OpenAICompatibleClinicJudge:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
        prompt_version: str,
    ):
        self.api_key = api_key
        # 纵深防御:拒绝指向私网/环回/非 http(s) 的 base_url(见 llm_http）。
        self.base_url = validate_llm_base_url(base_url).rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.prompt_version = prompt_version
        self._enabled = True

    def is_available(self) -> bool:
        return bool(self._enabled and self.api_key and self.base_url and self.model)

    def disable(self) -> None:
        self._enabled = False

    def evaluate_dimension(
        self,
        *,
        dimension_id: str,
        rubric: dict[str, Any],
        facts: dict[str, Any],
        sampled_skills: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not self.is_available():
            return None

        template = (PROMPTS_ROOT / f"dimension_judge.{self.prompt_version}.md").read_text(encoding="utf-8")
        prompt = (
            template.replace("{{dimension_id}}", dimension_id)
            .replace("{{rubric_yaml}}", yaml.safe_dump(rubric, sort_keys=False, allow_unicode=True))
            .replace("{{namespace_summary}}", facts.get("summary", ""))
            .replace("{{facts_json}}", json.dumps(facts, ensure_ascii=False, indent=2))
            .replace("{{skills_json}}", json.dumps(sampled_skills, ensure_ascii=False, indent=2))
        )

        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
            "messages": [
                {"role": "system", "content": "You are a strict evaluation engine. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        message = data["choices"][0]["message"]["content"]
        return _extract_json(message)


class ClinicAssets:
    def __init__(self, rubric_version: str, prompt_version: str):
        self.rubric_version = rubric_version
        self.prompt_version = prompt_version

    def load_rubric(self, dimension_id: str) -> dict[str, Any]:
        path = RUBRICS_ROOT / f"{dimension_id}.{self.rubric_version}.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class ClinicFactsExtractor:
    def extract(self, skills_data: list[dict[str, Any]], scan_results: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        skills: list[dict[str, Any]] = []
        stale_count = 0
        production_count = 0
        examples_count = 0
        prompt_count = 0
        total_critical = 0
        total_high = 0

        for skill in skills_data[: settings.CLINIC_EVAL_MAX_SKILLS]:
            latest_metadata = skill.get("latest_metadata") or {}
            skill_md = skill.get("skill_md") or ""
            system_prompt = skill.get("system_prompt") or ""
            scan = scan_results.get(skill["name"], {})
            last_updated_str = skill.get("last_updated")
            age_days = None

            if last_updated_str:
                try:
                    updated_at = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
                    age_days = max(0, (now - updated_at).days)
                    if age_days >= 180:
                        stale_count += 1
                except (ValueError, TypeError):
                    age_days = None

            has_examples = _has_examples(skill_md)
            has_prompt = bool(system_prompt.strip())
            if has_examples:
                examples_count += 1
            if has_prompt:
                prompt_count += 1
            if skill.get("has_production"):
                production_count += 1

            total_critical += int(scan.get("critical_count", 0) or 0)
            total_high += int(scan.get("high_count", 0) or 0)

            skills.append(
                {
                    "name": skill["name"],
                    "description": skill.get("description"),
                    "version_count": skill.get("version_count", 0),
                    "has_production": bool(skill.get("has_production")),
                    "last_updated_days": age_days,
                    "skill_md_words": _word_count(skill_md),
                    "system_prompt_words": _word_count(system_prompt),
                    "header_count": _header_count(skill_md) + _header_count(system_prompt),
                    "has_examples": has_examples,
                    "has_output_guidance": _has_output_guidance(system_prompt),
                    "has_constraints": _has_constraints(system_prompt),
                    "has_role_definition": "you are" in system_prompt.lower() or "你的角色" in system_prompt,
                    "metadata_fields": sorted(latest_metadata.keys()),
                    "scan": {
                        "status": scan.get("status"),
                        "critical_count": int(scan.get("critical_count", 0) or 0),
                        "high_count": int(scan.get("high_count", 0) or 0),
                        "medium_count": int(scan.get("medium_count", 0) or 0),
                        "low_count": int(scan.get("low_count", 0) or 0),
                    },
                }
            )

        total = len(skills)
        return {
            "summary": (
                f"Namespace contains {total} tracked skills. "
                f"{production_count} have production versions, {examples_count} include examples, "
                f"{prompt_count} include system prompts. Total unresolved issues: "
                f"{total_critical} critical and {total_high} high."
            ),
            "namespace": {
                "skill_count": total,
                "production_ratio": round(production_count / total, 3) if total else 0.0,
                "examples_ratio": round(examples_count / total, 3) if total else 0.0,
                "prompt_ratio": round(prompt_count / total, 3) if total else 0.0,
                "stale_ratio": round(stale_count / total, 3) if total else 0.0,
                "critical_total": total_critical,
                "high_total": total_high,
            },
            "skills": skills,
        }


class ClinicService:
    def __init__(self) -> None:
        self.extractor = ClinicFactsExtractor()

    def _build_provider(self) -> ClinicJudgeProvider:
        provider = settings.CLINIC_LLM_PROVIDER.strip().lower()
        # CLINIC_LLM_* 优先，缺失回落规范 DUCKDOCK_LLM_*（L2-PROVIDER-UNIFY）。
        api_key, base_url, model = resolve_llm("CLINIC_LLM")
        if provider == "openai_compatible" and api_key and base_url and model:
            try:
                return OpenAICompatibleClinicJudge(
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout_seconds=settings.CLINIC_LLM_TIMEOUT_SECONDS,
                    prompt_version=settings.CLINIC_JUDGE_PROMPT_VERSION,
                )
            except LLMConfigError as exc:  # base_url 非法 → 回落确定性评审,不阻断
                logger.warning("Clinic LLM judge disabled (invalid base_url): %s", exc)
        return NoopClinicJudgeProvider()

    def _blend_with_llm(
        self,
        *,
        dimension_id: str,
        deterministic: DimensionResult,
        rubric: dict[str, Any],
        facts: dict[str, Any],
        sampled_skills: list[dict[str, Any]],
        provider: ClinicJudgeProvider,
        trace_span=None,
    ) -> DimensionResult:
        # L2-CLINIC-8DIM:LLM judge 覆盖全部 8 维(历史上只调整 4 维,其余永远停在启发式)。
        if settings.CLINIC_EVAL_MODE == "deterministic":
            return deterministic
        if not provider.is_available():
            return deterministic

        with langfuse_service.start_judge_generation(
            parent_span=trace_span,
            dimension_id=dimension_id,
            model=getattr(provider, "model", resolve_llm("CLINIC_LLM")[2]),
            prompt_version=settings.CLINIC_JUDGE_PROMPT_VERSION,
            rubric_version=settings.CLINIC_RUBRIC_VERSION,
            facts_summary=facts.get("summary", ""),
            sampled_skill_names=[skill.get("name", "") for skill in sampled_skills],
        ) as generation:
            try:
                judge = provider.evaluate_dimension(
                    dimension_id=dimension_id,
                    rubric=rubric,
                    facts=facts,
                    sampled_skills=sampled_skills,
                )
                if generation is not None:
                    generation.update(output=judge)
            except Exception as exc:
                if hasattr(provider, "disable"):
                    provider.disable()
                logger.warning("Clinic judge failed for dimension '%s': %s", dimension_id, exc)
                if generation is not None:
                    generation.update(
                        level="ERROR",
                        status_message=str(exc)[:500],
                        output={"fallback": "deterministic"},
                    )
                return deterministic

        if not judge:
            return deterministic

        llm_score = max(0.0, min(100.0, _safe_float(judge.get("score"), deterministic.score)))
        llm_confidence = max(0.0, min(1.0, _safe_float(judge.get("confidence"), 0.75)))
        score = deterministic.score * 0.45 + llm_score * 0.55
        confidence = ((deterministic.confidence or 0.85) * 0.45) + (llm_confidence * 0.55)

        evidence = [
            Evidence(
                skill=item.get("skill"),
                reason=item.get("reason", "Model-provided evidence"),
                snippet=item.get("snippet"),
            )
            for item in (judge.get("evidence") or [])[:4]
            if isinstance(item, dict) and item.get("reason")
        ]
        if not evidence:
            evidence = deterministic.evidence

        issues = list(dict.fromkeys([*(deterministic.issues or []), *((judge.get("issues") or [])[:4])]))

        return DimensionResult(
            score=score,
            weight=deterministic.weight,
            issues=issues[:6],
            details=deterministic.details,
            trend=deterministic.trend,
            confidence=confidence,
            reasoning_summary=judge.get("reasoning_summary") or deterministic.reasoning_summary or deterministic.details,
            evidence=evidence,
            source="llm",
        )

    def _dimension_sample(self, skills_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sample = []
        for skill in skills_data[: min(4, settings.CLINIC_EVAL_MAX_SKILLS)]:
            sample.append(
                {
                    "name": skill["name"],
                    "description": skill.get("description"),
                    "skill_md": (skill.get("skill_md") or "")[:700],
                    "system_prompt": (skill.get("system_prompt") or "")[:700],
                    "metadata": skill.get("latest_metadata") or {},
                    "has_production": bool(skill.get("has_production")),
                }
            )
        return sample

    def _eval_skill_completeness(self, skills: list[dict[str, Any]], scan_results: dict[str, Any]) -> DimensionResult:
        weight = DIMENSION_WEIGHTS["skill_completeness"]
        if not skills:
            return DimensionResult(
                score=0.0,
                weight=weight,
                issues=["Namespace has no skills"],
                details="No skills published to this namespace.",
                confidence=0.99,
            )

        total = len(skills)
        required_fields = ("name", "version", "description")
        complete_metadata = 0
        prompts = 0
        examples = 0
        production = 0
        evidence: list[Evidence] = []

        for skill in skills:
            metadata = skill.get("latest_metadata") or {}
            if all(metadata.get(field) for field in required_fields):
                complete_metadata += 1
            else:
                missing = [field for field in required_fields if not metadata.get(field)]
                evidence.append(Evidence(skill=skill["name"], reason=f"Missing metadata fields: {', '.join(missing)}"))
            if skill.get("system_prompt"):
                prompts += 1
            else:
                evidence.append(Evidence(skill=skill["name"], reason="Missing system_prompt.md"))
            if _has_examples(skill.get("skill_md")):
                examples += 1
            if skill.get("has_production"):
                production += 1

        metadata_rate = complete_metadata / total
        prompt_rate = prompts / total
        example_rate = examples / total
        production_rate = production / total
        score = (
            metadata_rate * 0.35
            + prompt_rate * 0.20
            + example_rate * 0.20
            + production_rate * 0.25
        ) * 100

        issues = []
        if metadata_rate < 0.9:
            issues.append(f"{total - complete_metadata} skill(s) are missing required metadata.")
        if prompt_rate < 0.8:
            issues.append(f"{total - prompts} skill(s) are missing system prompts.")
        if example_rate < 0.7:
            issues.append(f"{total - examples} skill(s) do not include usage examples.")
        if production_rate < 0.7:
            issues.append(f"Only {production} of {total} skill(s) have a production version.")

        return DimensionResult(
            score=score,
            weight=weight,
            issues=issues,
            details=f"{complete_metadata}/{total} skills have complete metadata, {prompts}/{total} have prompts, {examples}/{total} include examples.",
            confidence=0.97,
            reasoning_summary="Completeness is based on required metadata, prompt presence, examples, and production readiness.",
            evidence=evidence[:4],
        )

    def _eval_skill_quality(self, skills: list[dict[str, Any]]) -> DimensionResult:
        weight = DIMENSION_WEIGHTS["skill_quality"]
        if not skills:
            return DimensionResult(score=0.0, weight=weight, details="No skills to evaluate.", confidence=0.99)

        issues = []
        evidence: list[Evidence] = []
        scores = []
        for skill in skills:
            prompt = skill.get("system_prompt") or ""
            skill_md = skill.get("skill_md") or ""
            prompt_words = _word_count(prompt)
            length_score = 35.0 if prompt_words < 30 else 75.0 if prompt_words < 80 else 88.0 if prompt_words <= 400 else 72.0
            structure_score = 90.0 if _header_count(prompt) > 0 else 60.0
            example_score = 90.0 if _has_examples(skill_md) else 55.0
            output_guidance_score = 85.0 if _has_output_guidance(prompt) else 50.0
            skill_score = (length_score * 0.25 + structure_score * 0.25 + example_score * 0.25 + output_guidance_score * 0.25)
            scores.append(skill_score)
            if skill_score < 65:
                evidence.append(
                    Evidence(
                        skill=skill["name"],
                        reason="Prompt quality appears weak due to vague output constraints or limited examples.",
                        snippet=(prompt or skill_md)[:180] or None,
                    )
                )

        avg = sum(scores) / len(scores)
        if avg < 70:
            issues.append("Several skills need clearer instructions and stronger examples.")
        if any(_word_count(skill.get("system_prompt")) < 20 for skill in skills):
            issues.append("Some system prompts are too short to communicate reliable behavior.")

        return DimensionResult(
            score=avg,
            weight=weight,
            issues=issues,
            details=f"Static quality baseline computed from prompt structure, output guidance, and example coverage across {len(skills)} skills.",
            confidence=0.82,
            reasoning_summary="Static quality acts as the baseline before any LLM semantic review is applied.",
            evidence=evidence[:4],
        )

    def _eval_skill_coherence(self, skills: list[dict[str, Any]]) -> DimensionResult:
        weight = DIMENSION_WEIGHTS["skill_coherence"]
        if not skills:
            return DimensionResult(score=0.0, weight=weight, details="No skills to evaluate.", confidence=0.99)

        names = [skill["name"] for skill in skills]
        normalized = [_normalize_name(name) for name in names]
        duplicates = len(normalized) - len(set(normalized))
        has_dash = sum(1 for name in names if "-" in name)
        has_underscore = sum(1 for name in names if "_" in name)
        naming_score = 95.0 if min(has_dash, has_underscore) == 0 else 68.0
        duplicate_score = 100.0 if duplicates == 0 else max(30.0, 100.0 - duplicates * 25)

        category_buckets = set()
        for skill in skills:
            text = f"{skill.get('name', '')} {skill.get('description') or ''}".lower()
            for token in ["code", "data", "ppt", "doc", "write", "review", "test", "sync", "agent"]:
                if token in text:
                    category_buckets.add(token)
        diversity_score = min(100.0, 50.0 + len(category_buckets) * 8)
        score = (naming_score * 0.35 + duplicate_score * 0.4 + diversity_score * 0.25)

        issues = []
        evidence: list[Evidence] = []
        if duplicates:
            issues.append("Duplicate or near-duplicate skill names detected.")
        if min(has_dash, has_underscore) > 0:
            issues.append("Naming conventions are mixed across the namespace.")
            evidence.append(Evidence(reason="Both dash and underscore naming styles are in use.", snippet=", ".join(names[:5])))
        if len(category_buckets) < 3 and len(skills) > 5:
            issues.append("Skill taxonomy looks narrow relative to namespace size.")

        return DimensionResult(
            score=score,
            weight=weight,
            issues=issues,
            details=f"Coherence baseline uses naming consistency, duplicate detection, and taxonomy spread across {len(skills)} skills.",
            confidence=0.8,
            reasoning_summary="Coherence improves when skills have distinct scope and consistent naming.",
            evidence=evidence[:4],
        )

    def _eval_security_health(self, skills: list[dict[str, Any]], scan_results: dict[str, Any]) -> DimensionResult:
        weight = DIMENSION_WEIGHTS["security_health"]
        if not skills:
            return DimensionResult(score=0.0, weight=weight, details="No skills to evaluate.", confidence=0.99)

        total_versions = sum(int(skill.get("version_count", 0) or 0) for skill in skills)
        scanned = len(scan_results)
        passed = sum(1 for result in scan_results.values() if result.get("status") in {"passed", "warned"})
        failed = sum(1 for result in scan_results.values() if result.get("status") == "failed")
        critical_total = sum(int(result.get("critical_count", 0) or 0) for result in scan_results.values())
        high_total = sum(int(result.get("high_count", 0) or 0) for result in scan_results.values())

        coverage_score = (scanned / max(total_versions, 1)) * 100
        pass_score = (passed / max(scanned, 1)) * 100 if scanned else 0.0
        penalty = min(85.0, critical_total * 18 + high_total * 8 + failed * 10)
        score = max(0.0, coverage_score * 0.35 + pass_score * 0.65 - penalty)

        issues = []
        evidence: list[Evidence] = []
        if scanned == 0:
            issues.append("No versions have been scanned yet.")
        if failed:
            issues.append(f"{failed} version(s) failed the security scan.")
        if critical_total:
            issues.append(f"{critical_total} unresolved critical issue(s) remain.")
            evidence.append(Evidence(reason="Critical security findings must block a strong score."))
        if high_total:
            issues.append(f"{high_total} unresolved high severity issue(s) remain.")

        return DimensionResult(
            score=score,
            weight=weight,
            issues=issues,
            details=f"{scanned}/{total_versions} versions scanned, {passed} passing scans, {critical_total} critical and {high_total} high unresolved issues.",
            confidence=0.99,
            reasoning_summary="Security is derived from scan coverage, pass rate, and severity-weighted penalties.",
            evidence=evidence[:4],
        )

    def _eval_documentation(self, skills: list[dict[str, Any]]) -> DimensionResult:
        weight = DIMENSION_WEIGHTS["documentation"]
        if not skills:
            return DimensionResult(score=0.0, weight=weight, details="No skills to evaluate.", confidence=0.99)

        scores = []
        issues = []
        evidence: list[Evidence] = []
        for skill in skills:
            skill_md = skill.get("skill_md") or ""
            score = 35.0
            if _word_count(skill_md) >= 120:
                score += 20
            if _header_count(skill_md) >= 2:
                score += 15
            if _has_examples(skill_md):
                score += 20
            if "limitation" in skill_md.lower() or "constraints" in skill_md.lower() or "注意" in skill_md:
                score += 10
            scores.append(min(100.0, score))
            if score < 60:
                evidence.append(Evidence(skill=skill["name"], reason="Documentation is sparse or lacks examples.", snippet=skill_md[:180] or None))

        avg = sum(scores) / len(scores)
        if any(score < 60 for score in scores):
            issues.append("Some skills still have minimal or under-structured documentation.")

        return DimensionResult(
            score=avg,
            weight=weight,
            issues=issues,
            details=f"Documentation baseline uses structure, examples, and usage guidance across {len(skills)} skills.",
            confidence=0.94,
            reasoning_summary="Well-documented skills should include examples, structure, and usage guidance.",
            evidence=evidence[:4],
        )

    def _eval_version_currency(self, skills: list[dict[str, Any]]) -> DimensionResult:
        weight = DIMENSION_WEIGHTS["version_currency"]
        if not skills:
            return DimensionResult(score=0.0, weight=weight, details="No skills to evaluate.", confidence=0.99)

        now = datetime.now(timezone.utc)
        scores = []
        issues = []
        evidence: list[Evidence] = []
        for skill in skills:
            updated_at_str = skill.get("last_updated")
            if not updated_at_str:
                scores.append(25.0)
                issues.append(f"Skill '{skill['name']}' has no published version.")
                evidence.append(Evidence(skill=skill["name"], reason="No published version available for recency evaluation."))
                continue
            try:
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                age_days = max(0, (now - updated_at).days)
            except (TypeError, ValueError):
                scores.append(45.0)
                continue

            if age_days < 30:
                scores.append(100.0)
            elif age_days < 90:
                scores.append(82.0)
            elif age_days < 180:
                scores.append(60.0)
                evidence.append(Evidence(skill=skill["name"], reason=f"Version is aging ({age_days} days since update)."))
            else:
                scores.append(28.0)
                issues.append(f"Skill '{skill['name']}' is stale ({age_days} days since update).")
                evidence.append(Evidence(skill=skill["name"], reason=f"Skill has not been updated for {age_days} days."))

        return DimensionResult(
            score=sum(scores) / len(scores) if scores else 0.0,
            weight=weight,
            issues=issues,
            details=f"Version currency baseline uses publish recency across {len(skills)} skills.",
            confidence=0.96,
            reasoning_summary="Freshly maintained skills score better than stale or unpublished ones.",
            evidence=evidence[:4],
        )

    def _eval_style_consistency(self, skills: list[dict[str, Any]]) -> DimensionResult:
        weight = DIMENSION_WEIGHTS["style_consistency"]
        prompts = [skill.get("system_prompt") or "" for skill in skills if skill.get("system_prompt")]
        if not prompts:
            return DimensionResult(
                score=50.0,
                weight=weight,
                issues=["No system prompts found."],
                details="Style consistency cannot be fully evaluated without prompt files.",
                confidence=0.78,
                reasoning_summary="Style consistency defaults to a neutral baseline when prompt coverage is limited.",
            )

        uses_headers = [1 if _header_count(prompt) > 0 else 0 for prompt in prompts]
        header_consistency = sum(uses_headers) / len(uses_headers)
        lengths = [_word_count(prompt) for prompt in prompts]
        max_len = max(lengths)
        min_len = min(lengths)
        length_consistency = 1.0 if max_len == 0 else min(1.0, (min_len + 1) / (max_len + 1) * 3)
        score = (header_consistency * 0.5 + length_consistency * 0.5) * 100

        issues = []
        if header_consistency < 0.6:
            issues.append("Prompt structure varies significantly across skills.")
        if length_consistency < 0.4:
            issues.append("Prompt lengths vary sharply across skills.")

        return DimensionResult(
            score=score,
            weight=weight,
            issues=issues,
            details=f"Style baseline uses prompt structure and length consistency across {len(prompts)} prompts.",
            confidence=0.76,
            reasoning_summary="Style consistency measures how uniformly skills are authored across the namespace.",
        )

    def _eval_interaction_quality(self, skills: list[dict[str, Any]]) -> DimensionResult:
        weight = DIMENSION_WEIGHTS["interaction_quality"]
        if not skills:
            return DimensionResult(score=0.0, weight=weight, details="No skills to evaluate.", confidence=0.99)

        scores = []
        evidence: list[Evidence] = []
        for skill in skills:
            prompt = skill.get("system_prompt") or ""
            score = 45.0
            if "you are" in prompt.lower() or "你的角色" in prompt:
                score += 15
            if _has_constraints(prompt):
                score += 10
            if _has_output_guidance(prompt):
                score += 15
            if "context" in prompt.lower() or "conversation" in prompt.lower() or "上下文" in prompt:
                score += 10
            if "fallback" in prompt.lower() or "无法" in prompt or "如果" in prompt:
                score += 5
            scores.append(min(100.0, score))
            if score < 65:
                evidence.append(Evidence(skill=skill["name"], reason="Prompt lacks role, output, or fallback guidance.", snippet=prompt[:180] or None))

        avg = sum(scores) / len(scores)
        issues = ["Some prompts need clearer collaboration and output guidance."] if any(score < 65 for score in scores) else []
        return DimensionResult(
            score=avg,
            weight=weight,
            issues=issues,
            details=f"Interaction baseline uses role clarity, constraints, output guidance, and fallback behavior across {len(skills)} skills.",
            confidence=0.83,
            reasoning_summary="Interaction quality improves when prompts define role, output, context, and fallback behavior.",
            evidence=evidence[:4],
        )

    def _generate_recommendations(self, dimension_results: dict[str, DimensionResult]) -> list[dict[str, Any]]:
        priority_map = {
            "skill_completeness": ("high", "auto"),
            "skill_quality": ("high", "suggest"),
            "skill_coherence": ("medium", "suggest"),
            "security_health": ("critical", "auto"),
            "documentation": ("medium", "suggest"),
            "version_currency": ("low", "manual"),
            "style_consistency": ("low", "suggest"),
            "interaction_quality": ("medium", "suggest"),
        }
        recommendations = []
        for dimension_id, result in dimension_results.items():
            if result.score >= 80:
                continue
            priority, action = priority_map.get(dimension_id, ("low", "manual"))
            if result.score < 50 and dimension_id != "security_health":
                priority = "high"
            issues = result.issues or ([result.reasoning_summary] if result.reasoning_summary else [])
            for issue in issues[:2]:
                recommendations.append(
                    {
                        "dimension": dimension_id,
                        "priority": priority,
                        "title": f"Improve {dimension_id.replace('_', ' ').title()}",
                        "description": issue,
                        "action": action,
                        "auto_fixable": action == "auto",
                    }
                )

        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        recommendations.sort(key=lambda item: order.get(item["priority"], 4))
        return recommendations[:12]

    @staticmethod
    def _ai_assist_indicator(
        provider: ClinicJudgeProvider,
        dimension_results: dict[str, DimensionResult],
    ) -> dict[str, Any]:
        """共享降级指标:让 release-gate / API 看得见这次评测是 AI 还是启发式。

        mode="llm" 仅当真实 judge 可用且至少一维实际混入了 LLM 评分;否则
        mode="baseline" + degraded=True 并附原因(无 key 降级 Noop、deterministic
        模式、或运行中所有 LLM 调用都失败回落)。
        """
        llm_scored = any(result.source == "llm" for result in dimension_results.values())
        if provider.is_available() and llm_scored:
            return {"mode": "llm", "degraded": False, "reason": None}

        if not provider.is_available():
            reason = "No LLM key resolved; clinic judge ran in heuristic-only mode."
        elif settings.CLINIC_EVAL_MODE == "deterministic":
            reason = "CLINIC_EVAL_MODE=deterministic; LLM judge intentionally skipped."
        else:
            reason = "Clinic judge unavailable or every dimension call fell back to heuristics."
        return {"mode": "baseline", "degraded": True, "reason": reason}

    def evaluate(
        self,
        skills_data: list[dict[str, Any]],
        scan_results: dict[str, Any],
        prev_scores: dict[str, Any] | None = None,
        evaluation_id: int | None = None,
        namespace_id: int | None = None,
        namespace_name: str | None = None,
    ) -> dict[str, Any]:
        assets = ClinicAssets(
            rubric_version=settings.CLINIC_RUBRIC_VERSION,
            prompt_version=settings.CLINIC_JUDGE_PROMPT_VERSION,
        )
        provider = self._build_provider()
        facts = self.extractor.extract(skills_data, scan_results)
        sampled_skills = self._dimension_sample(skills_data)
        with langfuse_service.start_clinic_evaluation(
            evaluation_id=evaluation_id,
            namespace_id=namespace_id,
            namespace_name=namespace_name or "unassigned",
            skill_count=len(skills_data),
            mode=settings.CLINIC_EVAL_MODE,
            rubric_version=settings.CLINIC_RUBRIC_VERSION,
            prompt_version=settings.CLINIC_JUDGE_PROMPT_VERSION,
        ) as trace_span:
            deterministic_results = {
                "skill_completeness": self._eval_skill_completeness(skills_data, scan_results),
                "skill_quality": self._eval_skill_quality(skills_data),
                "skill_coherence": self._eval_skill_coherence(skills_data),
                "security_health": self._eval_security_health(skills_data, scan_results),
                "documentation": self._eval_documentation(skills_data),
                "version_currency": self._eval_version_currency(skills_data),
                "style_consistency": self._eval_style_consistency(skills_data),
                "interaction_quality": self._eval_interaction_quality(skills_data),
            }

            dimension_results: dict[str, DimensionResult] = {}
            for dimension_id, deterministic in deterministic_results.items():
                rubric = assets.load_rubric(dimension_id)
                dimension_results[dimension_id] = self._blend_with_llm(
                    dimension_id=dimension_id,
                    deterministic=deterministic,
                    rubric=rubric,
                    facts=facts,
                    sampled_skills=sampled_skills,
                    provider=provider,
                    trace_span=trace_span,
                )

            if prev_scores:
                for dimension_id, result in dimension_results.items():
                    previous = prev_scores.get(dimension_id, {}).get("score")
                    if previous is None:
                        continue
                    diff = result.score - float(previous)
                    result.trend = "up" if diff > 2 else "down" if diff < -2 else "stable"

            total_weight = sum(result.weight for result in dimension_results.values())
            overall_score = sum(result.score * result.weight for result in dimension_results.values()) / max(total_weight, 1)
            recommendations = self._generate_recommendations(dimension_results)
            ai_assist = self._ai_assist_indicator(provider, dimension_results)

            rounded_score = round(overall_score, 1)
            grade = _grade(overall_score)
            output = {
                "overall_score": rounded_score,
                "grade": grade,
                "dimension_scores": {dimension_id: result.to_dict() for dimension_id, result in dimension_results.items()},
                "recommendations": recommendations,
                # 共享降级指标 + 摘要:写入存储后,release-gate 的 clinic 判定不再把启发式当 AI 静默使用。
                "ai_assist": ai_assist,
                "summary": {
                    "overall_score": rounded_score,
                    "grade": grade,
                    "ai_assist": ai_assist,
                },
                "langfuse_trace_id": getattr(trace_span, "trace_id", None) if trace_span is not None else None,
                "langfuse_trace_url": None,
            }
            if trace_span is not None:
                trace_span.update(output={"overall_score": output["overall_score"], "grade": output["grade"]})
                output["langfuse_trace_url"] = langfuse_service.get_trace_url(
                    trace_id=trace_span.trace_id
                )
            return output


clinic_service = ClinicService()
