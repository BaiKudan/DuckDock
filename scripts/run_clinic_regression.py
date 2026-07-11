import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.clinic_service import clinic_service  # noqa: E402


def load_fixtures(path: Path) -> list[dict[str, Any]]:
    fixtures = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        fixtures.append(json.loads(stripped))
    return fixtures


def in_range(value: float, value_range: list[float]) -> bool:
    return float(value_range[0]) <= float(value) <= float(value_range[1])


def assert_dimension(case_id: str, dim_id: str, result: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    score = float(result.get("score", 0))
    if "score_range" in spec and not in_range(score, spec["score_range"]):
        failures.append(f"{case_id}:{dim_id} score {score} not in range {spec['score_range']}")

    confidence = result.get("confidence")
    if "min_confidence" in spec and confidence is not None and float(confidence) < float(spec["min_confidence"]):
        failures.append(
            f"{case_id}:{dim_id} confidence {confidence} below {spec['min_confidence']}"
        )

    evidence = result.get("evidence") or []
    if "evidence_min" in spec and len(evidence) < int(spec["evidence_min"]):
        failures.append(
            f"{case_id}:{dim_id} evidence count {len(evidence)} below {spec['evidence_min']}"
        )

    issues = " | ".join((result.get("issues") or [])).lower()
    for token in spec.get("issues_contains", []):
        if str(token).lower() not in issues:
            failures.append(f"{case_id}:{dim_id} issues missing token '{token}'")

    return failures


def run_case(case: dict[str, Any], *, force_mode: str | None = None) -> tuple[bool, list[str], dict[str, Any] | None]:
    case_id = case["id"]
    mode = force_mode or case.get("mode") or "deterministic"
    previous_mode = settings.CLINIC_EVAL_MODE
    try:
        settings.CLINIC_EVAL_MODE = mode
        result = clinic_service.evaluate(
            skills_data=case["skills"],
            scan_results=case.get("scan_results", {}),
            prev_scores=case.get("prev_scores"),
        )
    finally:
        settings.CLINIC_EVAL_MODE = previous_mode

    failures: list[str] = []
    overall = float(result["overall_score"])
    if "expected_overall_range" in case and not in_range(overall, case["expected_overall_range"]):
        failures.append(
            f"{case_id}: overall score {overall} not in range {case['expected_overall_range']}"
        )

    for dim_id, spec in (case.get("dimension_assertions") or {}).items():
        dim_result = (result.get("dimension_scores") or {}).get(dim_id)
        if not dim_result:
            failures.append(f"{case_id}: missing dimension '{dim_id}'")
            continue
        failures.extend(assert_dimension(case_id, dim_id, dim_result, spec))

    return len(failures) == 0, failures, result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Clinic regression fixtures.")
    parser.add_argument(
        "--fixtures",
        default=str(BACKEND_ROOT / "app" / "clinic_assets" / "fixtures" / "clinic_golden_set.v1.jsonl"),
        help="Path to Clinic fixture JSONL file.",
    )
    parser.add_argument("--case", help="Run only one fixture id.")
    parser.add_argument(
        "--include-live",
        action="store_true",
        help="Include fixtures marked with requires_judge=true.",
    )
    parser.add_argument(
        "--mode",
        choices=["deterministic", "hybrid"],
        help="Force evaluation mode for all fixtures.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    args = parser.parse_args()

    fixtures = load_fixtures(Path(args.fixtures))
    if args.case:
        fixtures = [fixture for fixture in fixtures if fixture["id"] == args.case]
    if not args.include_live:
        fixtures = [fixture for fixture in fixtures if not fixture.get("requires_judge")]

    if not fixtures:
        print("No fixtures selected.")
        return 1

    summary: list[dict[str, Any]] = []
    any_failure = False
    for fixture in fixtures:
        ok, failures, result = run_case(fixture, force_mode=args.mode)
        any_failure = any_failure or (not ok)
        summary.append(
            {
                "id": fixture["id"],
                "mode": args.mode or fixture.get("mode", "deterministic"),
                "status": "passed" if ok else "failed",
                "overall_score": None if result is None else result.get("overall_score"),
                "failures": failures,
            }
        )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for item in summary:
            print(f"[{item['status'].upper()}] {item['id']} mode={item['mode']} overall={item['overall_score']}")
            for failure in item["failures"]:
                print(f"  - {failure}")

    return 1 if any_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
