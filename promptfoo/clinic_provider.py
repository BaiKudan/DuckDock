import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.clinic_service import clinic_service  # noqa: E402


def _load_fixture(case_id: str, fixture_path: str):
    path = Path(fixture_path)
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        item = json.loads(stripped)
        if item.get("id") == case_id:
            return item
    raise ValueError(f"Fixture case not found: {case_id}")


def call_api(prompt, options, context):
    vars_data = context.get("vars", {})
    case_id = vars_data.get("case_id")
    fixture_path = vars_data.get("fixture_path")
    mode = vars_data.get("mode", "hybrid")
    if not case_id or not fixture_path:
        raise ValueError("Promptfoo vars must include case_id and fixture_path")

    fixture = _load_fixture(case_id, fixture_path)
    previous_mode = settings.CLINIC_EVAL_MODE
    try:
        settings.CLINIC_EVAL_MODE = mode
        result = clinic_service.evaluate(
            skills_data=fixture["skills"],
            scan_results=fixture.get("scan_results", {}),
            prev_scores=fixture.get("prev_scores"),
        )
    finally:
        settings.CLINIC_EVAL_MODE = previous_mode

    return {"output": json.dumps(result, ensure_ascii=False)}
