from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    REPO_ROOT
    / "docs"
    / "agent-control-plane-prd"
    / "duckdock-analysis-worker"
    / "scripts"
    / "duckdock_analysis_worker.py"
)


def load_worker_module():
    spec = importlib.util.spec_from_file_location("duckdock_analysis_worker", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_sample_pack(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": "duckdock-pack-v1",
                    "report_id": "rpt_test_worker",
                    "provider": "openclaw",
                    "actor_username": "employee",
                }
            ),
        )
        zf.writestr(
            "runtime.json",
            json.dumps(
                {
                    "provider": "openclaw",
                    "runtime_id": "runtime-1",
                    "workspace": "duckdock-demo",
                }
            ),
        )
        zf.writestr(
            "inventory/skills.json",
            json.dumps(
                [
                    {
                        "id": "skill/upload-check",
                        "name": "DuckDock Upload Check Skill",
                        "summary": "Validates report upload sessions.",
                        "criticality": "high",
                        "workspace": "duckdock-demo",
                        "creator": "employee",
                    }
                ]
            ),
        )
        zf.writestr(
            "inventory/sessions.ndjson",
            json.dumps({"id": "session-1", "summary": "Validated report upload and analysis finalize path."}) + "\n",
        )
        zf.writestr(
            "summaries/weekly.md",
            "# DuckDock upload path dry-run\n\nValidated report upload session, MinIO PUT, finalize and ingestion.",
        )


def test_baseline_analysis_writes_standard_result_files(tmp_path):
    worker = load_worker_module()
    pack_path = tmp_path / "duckdock-pack-v1.zip"
    result_dir = tmp_path / "result"
    write_sample_pack(pack_path)

    result = worker.analyze_local_pack(pack_path, result_dir, analysis_mode="baseline")

    assert result["summary"]["analysis_mode"] == "baseline-script"
    assert result["summary"]["asset_count"] == 1
    assert result["summary"]["worktrace_count"] == 1
    assert (result_dir / "analysis-result.json").exists()
    assert (result_dir / "asset-cards.json").exists()
    assert (result_dir / "worktrace-summary.md").exists()
    assert (result_dir / "memory-candidates.json").exists()
    assert (result_dir / "handover-signals.json").exists()


def test_llm_analysis_uses_qwen_compatible_schema(monkeypatch, tmp_path):
    worker = load_worker_module()
    pack_path = tmp_path / "duckdock-pack-v1.zip"
    result_dir = tmp_path / "result"
    write_sample_pack(pack_path)

    def fake_request_llm_json(config, messages):
        assert config.model == "qwen3.7-max"
        assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert any("DuckDock" in message["content"] for message in messages)
        return {
            "analysis_summary": {
                "summary": "发现一个高影响 DuckDock 上传校验技能。",
                "limitations": ["测试返回，不包含真实模型调用。"],
            },
            "asset_cards": [
                {
                    "external_id": "skill/upload-check",
                    "asset_type": "skill",
                    "name": "DuckDock Upload Check Skill",
                    "description": "用于验证 DuckDock 上报链路的技能。",
                    "criticality": "high",
                    "status": "active",
                    "project": "duckdock-demo",
                    "owner_hint": "employee",
                    "confidence": 0.91,
                }
            ],
            "worktrace_summary_md": "# DuckDock 上传校验\n\n- 已验证上传、归档和分析链路。",
            "memory_candidates": [
                {
                    "candidate_type": "project_context",
                    "subject_type": "project",
                    "subject_key": "duckdock-demo",
                    "title": "DuckDock 上传链路上下文",
                    "summary": "该项目通过 Reporter 包验证上传、MinIO 暂存和分析归档。",
                    "confidence": 0.86,
                    "sensitivity": "internal",
                }
            ],
            "handover_signals": [
                {
                    "signal_type": "handover",
                    "subject_type": "skill",
                    "subject_key": "skill/upload-check",
                    "title": "上传校验 Skill 需要确认维护人",
                    "summary": "该技能影响 DuckDock 上报验证，应在岗位交接时确认维护人。",
                    "confidence": 0.82,
                    "sensitivity": "internal",
                }
            ],
        }

    monkeypatch.setattr(worker, "request_llm_json", fake_request_llm_json)
    result = worker.analyze_local_pack(
        pack_path,
        result_dir,
        analysis_mode="llm",
        llm_config=worker.LLMConfig(
            api_key="test-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen3.7-max",
            enable_thinking=True,
        ),
    )

    analysis_result = json.loads((result_dir / "analysis-result.json").read_text(encoding="utf-8"))
    asset_cards = json.loads((result_dir / "asset-cards.json").read_text(encoding="utf-8"))
    memories = json.loads((result_dir / "memory-candidates.json").read_text(encoding="utf-8"))
    signals = json.loads((result_dir / "handover-signals.json").read_text(encoding="utf-8"))

    assert result["summary"]["analysis_mode"] == "llm-worker"
    assert result["summary"]["model"] == "qwen3.7-max"
    assert analysis_result["processing"]["model"] == "qwen3.7-max"
    assert "测试返回" in analysis_result["limitations"][0]
    assert asset_cards[0]["criticality"] == "high"
    assert "DuckDock 上传校验" in (result_dir / "worktrace-summary.md").read_text(encoding="utf-8")
    assert memories[0]["candidate_type"] == "project_context"
    assert signals[0]["signal_type"] == "handover"
    # Explicit degradation contract: when the LLM actually produced the output,
    # ai_assist must say mode=llm, not degraded.
    ai_assist = result["summary"]["ai_assist"]
    assert ai_assist == {"mode": "llm", "degraded": False, "reason": None}
    assert analysis_result["summary"]["ai_assist"] == {"mode": "llm", "degraded": False, "reason": None}


def test_llm_mode_requires_key_but_auto_falls_back(tmp_path):
    worker = load_worker_module()
    pack_path = tmp_path / "duckdock-pack-v1.zip"
    result_dir = tmp_path / "result"
    write_sample_pack(pack_path)
    config = worker.LLMConfig(api_key="", model="qwen3.7-max")

    with pytest.raises(worker.WorkerError):
        worker.analyze_local_pack(pack_path, result_dir / "strict", analysis_mode="llm", llm_config=config)

    result = worker.analyze_local_pack(pack_path, result_dir / "auto", analysis_mode="auto", llm_config=config)
    analysis_result = json.loads((result_dir / "auto" / "analysis-result.json").read_text(encoding="utf-8"))

    assert result["summary"]["analysis_mode"] == "baseline-script"
    assert "LLM analysis is not configured" in analysis_result["limitations"][0]
    # Explicit, machine-readable degradation marker: auto mode fell back to
    # baseline because no LLM key resolved. No more silent masquerade.
    expected = {"mode": "baseline", "degraded": True, "reason": "no LLM key"}
    assert result["summary"]["ai_assist"] == expected
    assert analysis_result["summary"]["ai_assist"] == expected


def test_baseline_mode_marks_ai_assist_baseline_without_degraded(tmp_path):
    # Pure baseline mode is the user's explicit choice, not a fallback:
    # mode=baseline but degraded=False (nothing was supposed to run an LLM).
    worker = load_worker_module()
    pack_path = tmp_path / "duckdock-pack-v1.zip"
    result_dir = tmp_path / "result"
    write_sample_pack(pack_path)

    result = worker.analyze_local_pack(pack_path, result_dir, analysis_mode="baseline")
    analysis_result = json.loads((result_dir / "analysis-result.json").read_text(encoding="utf-8"))

    ai_assist = result["summary"]["ai_assist"]
    assert ai_assist["mode"] == "baseline"
    assert ai_assist["degraded"] is False
    assert ai_assist["reason"] is None
    assert analysis_result["summary"]["ai_assist"] == ai_assist


def test_auto_mode_marks_degraded_when_llm_call_fails(monkeypatch, tmp_path):
    # auto mode with a key, but the LLM call raises -> baseline + degraded=True
    # with a reason that surfaces the failure (still no silent masquerade).
    worker = load_worker_module()
    pack_path = tmp_path / "duckdock-pack-v1.zip"
    result_dir = tmp_path / "result"
    write_sample_pack(pack_path)

    def boom(config, messages):
        raise worker.WorkerError("LLM HTTP 503: upstream unavailable")

    monkeypatch.setattr(worker, "request_llm_json", boom)
    config = worker.LLMConfig(api_key="test-key", model="qwen3.7-max")

    result = worker.analyze_local_pack(pack_path, result_dir, analysis_mode="auto", llm_config=config)
    analysis_result = json.loads((result_dir / "analysis-result.json").read_text(encoding="utf-8"))

    assert result["summary"]["analysis_mode"] == "baseline-script"
    ai_assist = result["summary"]["ai_assist"]
    assert ai_assist["mode"] == "baseline"
    assert ai_assist["degraded"] is True
    assert ai_assist["reason"] and "LLM analysis failed" in ai_assist["reason"]
    assert analysis_result["summary"]["ai_assist"] == ai_assist


def test_request_json_reports_network_errors_without_traceback(monkeypatch):
    worker = load_worker_module()

    def raise_url_error(_req, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr(worker.urllib.request, "urlopen", raise_url_error)

    with pytest.raises(worker.WorkerError) as exc_info:
        worker.request_json("POST", "http://backend:8801/api/v1/analysis/jobs/lease", token="test-token", payload={})

    assert "HTTP request failed http://backend:8801/api/v1/analysis/jobs/lease" in str(exc_info.value)
    assert "connection refused" in str(exc_info.value)
