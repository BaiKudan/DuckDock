#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("duckdock-analysis-worker")


RESULT_FILES = {
    "analysis_result": ("analysis-result.json", "application/json"),
    "asset_cards": ("asset-cards.json", "application/json"),
    "worktrace_summary": ("worktrace-summary.md", "text/markdown"),
    "memory_candidates": ("memory-candidates.json", "application/json"),
    "handover_signals": ("handover-signals.json", "application/json"),
}

WORKER_RECIPE_VERSION = "duckdock-analysis-worker-v1"
WORKER_PROMPT_VERSION = "duckdock-analysis-prompt-v1"
LLM_USAGE_METADATA_KEY = "_duckdock_llm_usage"
LLM_TRACE_METADATA_KEY = "_duckdock_llm_trace_id"

ASSET_TYPE_BY_INVENTORY = {
    "skills": "skill",
    "agents": "agent",
    "prompts": "prompt",
    "workflows": "workflow",
    "tools": "tool",
    "mcp": "mcp",
    "memories": "knowledge_base",
    "memory": "knowledge_base",
    "knowledge_bases": "knowledge_base",
    "scheduled_tasks": "scheduled_task",
}

SECRET_KEYS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "private_key",
    "cookie",
    "authorization",
    "client_secret",
    "webhook_secret",
)

DEFAULT_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_LLM_MODEL = "qwen3.7-max"
DEFAULT_LLM_TIMEOUT_SECONDS = 90
DEFAULT_LLM_MAX_INPUT_CHARS = 24000

ANALYSIS_MODES = {"baseline", "llm", "auto"}
VALID_ASSET_TYPES = {
    "skill",
    "agent",
    "prompt",
    "workflow",
    "mcp",
    "tool",
    "knowledge_base",
    "scheduled_task",
    "credential_ref",
    "workspace",
    "other",
}
VALID_ASSET_STATUSES = {"active", "inactive", "archived", "orphaned", "risky", "transferred"}
VALID_CANDIDATE_TYPES = {
    "asset_summary",
    "worktrace_summary",
    "project_context",
    "ownership_signal",
    "handover_signal",
    "risk_signal",
    "knowledge_note",
}
VALID_SENSITIVITIES = {"public", "internal", "confidential", "restricted"}


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str = DEFAULT_LLM_BASE_URL
    model: str = DEFAULT_LLM_MODEL
    enable_thinking: bool = True
    timeout_seconds: int = DEFAULT_LLM_TIMEOUT_SECONDS
    max_input_chars: int = DEFAULT_LLM_MAX_INPUT_CHARS


class WorkerError(RuntimeError):
    pass


class LLMHTTPError(WorkerError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"LLM HTTP {status_code}: {truncate(detail, 1200)}")
        self.status_code = status_code
        self.detail = detail


def main() -> int:
    args = parse_args()
    api_base = (args.api_base or os.getenv("DUCKDOCK_API_BASE") or "").rstrip("/")
    token = args.worker_token or os.getenv("DUCKDOCK_WORKER_TOKEN") or ""
    worker_name = args.worker_name or os.getenv("DUCKDOCK_WORKER_NAME") or "duckdock-analysis-worker"
    llm_config = llm_config_from_args(args)
    if args.pack_file:
        result = analyze_local_pack(
            Path(args.pack_file),
            Path(args.work_dir or tempfile.mkdtemp(prefix="duckdock-worker-")),
            analysis_mode=args.analysis_mode,
            llm_config=llm_config,
        )
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        return 0
    if not api_base:
        raise WorkerError("DUCKDOCK_API_BASE is required")
    if not token:
        raise WorkerError("DUCKDOCK_WORKER_TOKEN is required")

    if args.poll:
        completed = 0
        while True:
            processed = process_one(api_base=api_base, token=token, worker_name=worker_name, args=args)
            if processed:
                completed += 1
            if args.max_jobs and completed >= args.max_jobs:
                break
            if args.once:
                break
            time.sleep(args.poll_seconds)
        return 0

    processed = process_one(api_base=api_base, token=token, worker_name=worker_name, args=args)
    return 0 if processed or args.allow_empty else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DuckDock analysis worker")
    parser.add_argument("--api-base", default=None, help="DuckDock API base, e.g. http://127.0.0.1:8990/api/v1")
    parser.add_argument("--worker-token", default=None, help="DuckDock analysis worker token")
    parser.add_argument("--worker-name", default=None, help="Worker display name")
    parser.add_argument("--work-dir", default=os.getenv("DUCKDOCK_WORKER_WORK_DIR"), help="Local work directory")
    parser.add_argument("--pack-file", default=None, help="Analyze a local duckdock-pack-v1.zip without calling DuckDock")
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    parser.add_argument("--poll", action="store_true", help="Poll continuously")
    parser.add_argument("--dry-run", action="store_true", help="Lease and analyze but do not upload/finalize")
    parser.add_argument("--allow-empty", action="store_true", help="Return success when no job is available")
    parser.add_argument("--poll-seconds", type=int, default=int(os.getenv("DUCKDOCK_WORKER_POLL_SECONDS", "60")))
    parser.add_argument("--lease-seconds", type=int, default=int(os.getenv("DUCKDOCK_WORKER_LEASE_SECONDS", "1800")))
    parser.add_argument("--max-jobs", type=int, default=0, help="Maximum jobs in poll mode; 0 means unlimited")
    parser.add_argument(
        "--analysis-mode",
        choices=sorted(ANALYSIS_MODES),
        default=normalize_analysis_mode(os.getenv("DUCKDOCK_ANALYSIS_MODE", "baseline")),
        help="Analysis strategy: baseline, llm, or auto. auto falls back to baseline if LLM is unavailable.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("DUCKDOCK_LLM_BASE_URL") or os.getenv("SKILL_GEN_BASE_URL") or DEFAULT_LLM_BASE_URL,
        help="OpenAI-compatible chat completion base URL.",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("DUCKDOCK_LLM_MODEL") or DEFAULT_LLM_MODEL,
        help="OpenAI-compatible model name for llm analysis mode.",
    )
    parser.add_argument(
        "--llm-enable-thinking",
        default=os.getenv("DUCKDOCK_LLM_ENABLE_THINKING", "true"),
        help="Whether to pass enable_thinking=true to compatible providers that support it.",
    )
    parser.add_argument(
        "--llm-timeout-seconds",
        type=int,
        default=int(os.getenv("DUCKDOCK_LLM_TIMEOUT_SECONDS", str(DEFAULT_LLM_TIMEOUT_SECONDS))),
    )
    parser.add_argument(
        "--llm-max-input-chars",
        type=int,
        default=int(os.getenv("DUCKDOCK_LLM_MAX_INPUT_CHARS", str(DEFAULT_LLM_MAX_INPUT_CHARS))),
    )
    return parser.parse_args()


def process_one(*, api_base: str, token: str, worker_name: str, args: argparse.Namespace) -> bool:
    lease_payload = {
        "lease_seconds": args.lease_seconds,
        "worker_name": worker_name,
        "capabilities_json": {
            "runtime": "openclaw-compatible",
            "skill": "duckdock-analysis-worker",
            "mode": normalize_analysis_mode(args.analysis_mode),
            "llm_model": args.llm_model if normalize_analysis_mode(args.analysis_mode) in {"llm", "auto"} else None,
        },
    }
    lease = request_json("POST", f"{api_base}/analysis/jobs/lease", token=token, payload=lease_payload)
    lease_job = lease.get("job")
    if not lease_job:
        print(json.dumps({"status": "empty", "message": "No pending analysis job"}, indent=2))
        return False

    job = lease_job["job"]
    job_id = int(job["id"])
    report_id = _report_id_from_job(job)
    work_root = Path(args.work_dir or tempfile.mkdtemp(prefix=f"duckdock-job-{job_id}-"))
    work_root.mkdir(parents=True, exist_ok=True)
    pack_path = work_root / "duckdock-pack-v1.zip"
    try:
        download_file(lease_job["download_url"], pack_path)
        heartbeat(api_base=api_base, token=token, job_id=job_id)
        analysis = analyze_local_pack(
            pack_path,
            work_root / "result",
            report_id=report_id,
            analysis_mode=args.analysis_mode,
            llm_config=llm_config_from_args(args),
        )
        if args.dry_run:
            request_json(
                "POST",
                f"{api_base}/analysis/jobs/{job_id}/fail",
                token=token,
                payload={
                    "error_message": "Dry run completed; job released without finalize",
                    "retryable": True,
                    "summary_json": {"dry_run": True, **analysis["summary"]},
                },
            )
            print(json.dumps({"status": "dry_run", "job_id": job_id, **analysis["summary"]}, ensure_ascii=False, indent=2))
            return True
        artifacts = upload_results(lease_job["result_uploads"], analysis["files"])
        primary = next((item for item in artifacts if item["kind"] == "analysis_result"), artifacts[0])
        finalize = request_json(
            "POST",
            f"{api_base}/analysis/jobs/{job_id}/finalize",
            token=token,
            payload={
                "result_sha256": primary["sha256"],
                "result_size_bytes": primary["size_bytes"],
                "summary_json": analysis["summary"],
                "result_artifacts": artifacts,
                "memory_candidates": [],
            },
        )
        print(
            json.dumps(
                {
                    "status": "succeeded",
                    "job_id": job_id,
                    "report_id": report_id,
                    "result_artifacts": len(artifacts),
                    "final_status": finalize.get("status"),
                    "materialized_counts": (finalize.get("summary_json") or {}).get("materialized_counts"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return True
    except Exception as exc:
        try:
            request_json(
                "POST",
                f"{api_base}/analysis/jobs/{job_id}/fail",
                token=token,
                payload={
                    "error_message": str(exc)[:4000],
                    "retryable": not isinstance(exc, zipfile.BadZipFile),
                    "summary_json": {"stage": "analysis_worker", "analysis_mode": normalize_analysis_mode(args.analysis_mode)},
                },
            )
        except Exception:
            pass
        raise


def analyze_local_pack(
    pack_path: Path,
    result_dir: Path,
    report_id: str | None = None,
    *,
    analysis_mode: str | None = None,
    llm_config: LLMConfig | None = None,
) -> dict[str, Any]:
    result_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pack_path) as zf:
        manifest = read_json_member(zf, "manifest.json") or {}
        runtime = read_json_member(zf, "runtime.json") or {}
        inventory = read_inventory(zf)
        summaries = read_summaries(zf)

    report_id = report_id or str(manifest.get("report_id") or manifest.get("id") or "")
    assets = build_asset_cards(inventory)
    worktrace_count = len(inventory.get("sessions", [])) + len(inventory.get("work_traces", []))
    memories = build_memory_candidates(inventory, summaries)
    signals = build_handover_signals(assets, inventory)
    worktrace_summary = build_worktrace_summary(manifest=manifest, runtime=runtime, inventory=inventory, summaries=summaries)
    limitations: list[str] = []
    processing: dict[str, Any] = {
        "mode": "baseline-script",
        "redaction": "completed",
        "classification": "baseline",
        "recipe_version": WORKER_RECIPE_VERSION,
        "prompt_version": WORKER_PROMPT_VERSION,
    }
    worker_metadata: dict[str, Any] = {
        "recipe_version": WORKER_RECIPE_VERSION,
        "prompt_version": WORKER_PROMPT_VERSION,
        "analysis_mode": "baseline-script",
        "trace_id": optional_env("DUCKDOCK_TRACE_ID"),
        "token_usage": {},
    }
    # Explicit degradation marker mirroring the backend contract. Defaults to a
    # non-degraded baseline: pure "baseline" mode is the operator's explicit
    # choice, not a silent fallback. auto-mode fallbacks flip degraded=True below.
    ai_assist: dict[str, Any] = {"mode": "baseline", "degraded": False, "reason": None}

    mode = normalize_analysis_mode(analysis_mode or os.getenv("DUCKDOCK_ANALYSIS_MODE", "baseline"))
    if mode in {"llm", "auto"}:
        llm_config = llm_config or llm_config_from_env()
        if not llm_config.api_key:
            message = "LLM analysis is not configured; set DUCKDOCK_LLM_API_KEY or DASHSCOPE_API_KEY."
            if mode == "llm":
                raise WorkerError(message)
            limitations.append(message + " Baseline analysis was used.")
            ai_assist = {"mode": "baseline", "degraded": True, "reason": "no LLM key"}
            logger.warning(
                "auto analysis mode degraded to baseline: no LLM key resolved "
                "(set DUCKDOCK_LLM_API_KEY or DASHSCOPE_API_KEY); ai_assist=%s",
                ai_assist,
            )
        else:
            try:
                llm_result = analyze_with_llm(
                    manifest=manifest,
                    runtime=runtime,
                    inventory=inventory,
                    summaries=summaries,
                    baseline_assets=assets,
                    baseline_worktrace_summary=worktrace_summary,
                    baseline_memories=memories,
                    baseline_signals=signals,
                    report_id=report_id,
                    worktrace_count=worktrace_count,
                    config=llm_config,
                )
                assets = llm_result["asset_cards"]
                worktrace_summary = llm_result["worktrace_summary"]
                memories = llm_result["memory_candidates"]
                signals = llm_result["handover_signals"]
                limitations.extend(llm_result.get("limitations") or [])
                processing = {
                    "mode": "llm-worker",
                    "redaction": "completed",
                    "classification": "llm",
                    "model": llm_config.model,
                    "base_url": llm_config.base_url.rstrip("/"),
                    "recipe_version": WORKER_RECIPE_VERSION,
                    "prompt_version": WORKER_PROMPT_VERSION,
                }
                ai_assist = {"mode": "llm", "degraded": False, "reason": None}
                worker_metadata = {
                    "recipe_version": WORKER_RECIPE_VERSION,
                    "prompt_version": WORKER_PROMPT_VERSION,
                    "analysis_mode": "llm-worker",
                    "model": llm_config.model,
                    "trace_id": optional_text(llm_result.get("trace_id")) or optional_env("DUCKDOCK_TRACE_ID"),
                    "token_usage": normalize_token_usage(llm_result.get("token_usage")),
                }
            except Exception as exc:
                if mode == "llm":
                    raise
                reason = f"LLM analysis failed: {truncate(str(exc), 300)}"
                limitations.append(f"LLM analysis failed and baseline analysis was used: {truncate(str(exc), 500)}")
                ai_assist = {"mode": "baseline", "degraded": True, "reason": reason}
                logger.warning(
                    "auto analysis mode degraded to baseline: LLM call failed; ai_assist=%s",
                    ai_assist,
                )

    analysis_result = {
        "schema_version": "duckdock-analysis-v1",
        "generated_at": now_iso(),
        "report_id": report_id,
        "runtime": sanitize_compact(runtime),
        "summary": build_summary(
            assets=assets,
            worktrace_count=worktrace_count,
            memories=memories,
            signals=signals,
            processing=processing,
            ai_assist=ai_assist,
        ),
        "processing": processing,
        "worker_metadata": {key: value for key, value in worker_metadata.items() if value is not None},
        "limitations": limitations,
    }
    files = {
        "analysis_result": write_json(result_dir / "analysis-result.json", analysis_result),
        "asset_cards": write_json(result_dir / "asset-cards.json", assets),
        "worktrace_summary": write_text(result_dir / "worktrace-summary.md", worktrace_summary),
        "memory_candidates": write_json(result_dir / "memory-candidates.json", memories),
        "handover_signals": write_json(result_dir / "handover-signals.json", signals),
    }
    summary = {
        **analysis_result["summary"],
        "worker_metadata": analysis_result["worker_metadata"],
    }
    return {"summary": summary, "files": files}


def normalize_analysis_mode(value: str | None) -> str:
    normalized = (value or "baseline").strip().lower()
    return normalized if normalized in ANALYSIS_MODES else "baseline"


def llm_config_from_args(args: argparse.Namespace) -> LLMConfig:
    return LLMConfig(
        api_key=llm_api_key_from_env(),
        base_url=(args.llm_base_url or DEFAULT_LLM_BASE_URL).rstrip("/"),
        model=args.llm_model or DEFAULT_LLM_MODEL,
        enable_thinking=parse_bool(args.llm_enable_thinking, default=True),
        timeout_seconds=max(5, int(args.llm_timeout_seconds or DEFAULT_LLM_TIMEOUT_SECONDS)),
        max_input_chars=max(4000, int(args.llm_max_input_chars or DEFAULT_LLM_MAX_INPUT_CHARS)),
    )


def llm_config_from_env() -> LLMConfig:
    return LLMConfig(
        api_key=llm_api_key_from_env(),
        base_url=(os.getenv("DUCKDOCK_LLM_BASE_URL") or os.getenv("SKILL_GEN_BASE_URL") or DEFAULT_LLM_BASE_URL).rstrip("/"),
        model=os.getenv("DUCKDOCK_LLM_MODEL") or DEFAULT_LLM_MODEL,
        enable_thinking=parse_bool(os.getenv("DUCKDOCK_LLM_ENABLE_THINKING", "true"), default=True),
        timeout_seconds=max(5, int(os.getenv("DUCKDOCK_LLM_TIMEOUT_SECONDS", str(DEFAULT_LLM_TIMEOUT_SECONDS)))),
        max_input_chars=max(4000, int(os.getenv("DUCKDOCK_LLM_MAX_INPUT_CHARS", str(DEFAULT_LLM_MAX_INPUT_CHARS)))),
    )


def llm_api_key_from_env() -> str:
    return (
        os.getenv("DUCKDOCK_LLM_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("SKILL_GEN_API_KEY")
        or ""
    ).strip()


def optional_env(name: str) -> str | None:
    return optional_text(os.getenv(name))


def optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def normalize_token_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        raw = value.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            usage[key] = raw
        elif isinstance(raw, float) and raw.is_integer():
            usage[key] = int(raw)
    return usage


def build_summary(
    *,
    assets: list[dict[str, Any]],
    worktrace_count: int,
    memories: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    processing: dict[str, Any],
    ai_assist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "asset_count": len(assets),
        "worktrace_count": worktrace_count,
        "memory_candidate_count": len(memories),
        "handover_signal_count": len([item for item in signals if item.get("signal_type") == "handover"]),
        "risk_count": len([item for item in signals if item.get("signal_type") == "risk"]),
        "analysis_mode": processing.get("mode", "baseline-script"),
        # Explicit machine-readable degradation marker (mirrors backend contract).
        "ai_assist": ai_assist or {"mode": "baseline", "degraded": False, "reason": None},
    }
    if processing.get("model"):
        summary["model"] = processing["model"]
    return summary


def analyze_with_llm(
    *,
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    inventory: dict[str, list[dict[str, Any]]],
    summaries: dict[str, str],
    baseline_assets: list[dict[str, Any]],
    baseline_worktrace_summary: str,
    baseline_memories: list[dict[str, Any]],
    baseline_signals: list[dict[str, Any]],
    report_id: str,
    worktrace_count: int,
    config: LLMConfig,
) -> dict[str, Any]:
    messages = build_llm_messages(
        manifest=manifest,
        runtime=runtime,
        inventory=inventory,
        summaries=summaries,
        baseline_assets=baseline_assets,
        baseline_worktrace_summary=baseline_worktrace_summary,
        baseline_memories=baseline_memories,
        baseline_signals=baseline_signals,
        report_id=report_id,
        worktrace_count=worktrace_count,
        max_input_chars=config.max_input_chars,
    )
    llm_json = request_llm_json(config, messages)
    token_usage = normalize_token_usage(llm_json.pop(LLM_USAGE_METADATA_KEY, None))
    trace_id = optional_text(llm_json.pop(LLM_TRACE_METADATA_KEY, None))
    llm_assets = normalize_asset_cards(llm_json.get("asset_cards"))
    llm_memories = normalize_memory_candidates(llm_json.get("memory_candidates"))
    llm_signals = normalize_handover_signals(llm_json.get("handover_signals"))
    llm_summary_md = llm_json.get("worktrace_summary_md")
    if not isinstance(llm_summary_md, str) or not llm_summary_md.strip():
        llm_summary_md = baseline_worktrace_summary

    return {
        "asset_cards": merge_asset_cards(llm_assets, baseline_assets),
        "worktrace_summary": truncate(clean_markdown(llm_summary_md), 8000) + "\n",
        "memory_candidates": merge_keyed_items(llm_memories, baseline_memories, key_fields=("candidate_type", "subject_key", "title")),
        "handover_signals": merge_keyed_items(llm_signals, baseline_signals, key_fields=("signal_type", "subject_key", "title")),
        "limitations": normalize_limitations((llm_json.get("analysis_summary") or {}).get("limitations")),
        "token_usage": token_usage,
        "trace_id": trace_id,
    }


def build_llm_messages(
    *,
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    inventory: dict[str, list[dict[str, Any]]],
    summaries: dict[str, str],
    baseline_assets: list[dict[str, Any]],
    baseline_worktrace_summary: str,
    baseline_memories: list[dict[str, Any]],
    baseline_signals: list[dict[str, Any]],
    report_id: str,
    worktrace_count: int,
    max_input_chars: int,
) -> list[dict[str, str]]:
    system_prompt = (
        "You are DuckDock's LLM Analysis Worker. Analyze a DuckDock report pack for an enterprise AI Agent "
        "asset control plane. Return only one valid JSON object, no markdown fences, no commentary. "
        "Do not invent unsupported facts. Do not output raw transcripts, secrets, API keys, cookies, private keys, "
        "credential values, or unrelated local inventory. Summarize, classify, and produce compact handover/risk signals."
    )
    payload = build_llm_payload(
        manifest=manifest,
        runtime=runtime,
        inventory=inventory,
        summaries=summaries,
        baseline_assets=baseline_assets,
        baseline_worktrace_summary=baseline_worktrace_summary,
        baseline_memories=baseline_memories,
        baseline_signals=baseline_signals,
        report_id=report_id,
        worktrace_count=worktrace_count,
        max_input_chars=max_input_chars,
    )
    schema = {
        "analysis_summary": {
            "summary": "short Chinese or English summary",
            "asset_count": 0,
            "worktrace_count": 0,
            "memory_candidate_count": 0,
            "handover_signal_count": 0,
            "risk_count": 0,
            "limitations": [],
        },
        "asset_cards": [
            {
                "external_id": "stable source id",
                "asset_type": "skill|agent|prompt|workflow|mcp|tool|knowledge_base|scheduled_task|credential_ref|workspace|other",
                "name": "compact name",
                "description": "compact non-secret description",
                "criticality": "low|medium|high|critical",
                "status": "active|inactive|archived|orphaned|risky|transferred",
                "project": "project or workspace hint",
                "owner_hint": "creator or owner hint",
                "content_hash": "optional hash",
                "confidence": 0.0,
            }
        ],
        "worktrace_summary_md": "# Work summary\n\n- Compact non-secret summary.",
        "memory_candidates": [
            {
                "candidate_type": "asset_summary|worktrace_summary|project_context|ownership_signal|handover_signal|risk_signal|knowledge_note",
                "subject_type": "project|asset|runtime|report",
                "subject_key": "stable subject key",
                "title": "candidate title",
                "summary": "compact memory candidate",
                "confidence": 0.0,
                "sensitivity": "public|internal|confidential|restricted",
            }
        ],
        "handover_signals": [
            {
                "signal_type": "handover|risk",
                "subject_type": "skill|agent|project|credential_ref|runtime|other",
                "subject_key": "stable subject key",
                "title": "signal title",
                "summary": "compact handover or risk reason",
                "confidence": 0.0,
                "sensitivity": "internal|confidential|restricted",
            }
        ],
    }
    user_prompt = (
        "Analyze this DuckDock pack summary and output JSON matching the schema exactly.\n\n"
        "Schema example:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Pack summary:\n"
        f"{payload}"
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def build_llm_payload(
    *,
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    inventory: dict[str, list[dict[str, Any]]],
    summaries: dict[str, str],
    baseline_assets: list[dict[str, Any]],
    baseline_worktrace_summary: str,
    baseline_memories: list[dict[str, Any]],
    baseline_signals: list[dict[str, Any]],
    report_id: str,
    worktrace_count: int,
    max_input_chars: int,
) -> str:
    payload = {
        "report_id": report_id,
        "manifest": sanitize_compact(manifest),
        "runtime": sanitize_compact(runtime),
        "inventory": sanitize_compact(inventory),
        "summaries": {name: truncate(clean_markdown(text), 2500) for name, text in summaries.items()},
        "baseline": {
            "asset_cards": baseline_assets[:80],
            "worktrace_count": worktrace_count,
            "worktrace_summary_md": truncate(baseline_worktrace_summary, 4000),
            "memory_candidates": baseline_memories[:60],
            "handover_signals": baseline_signals[:60],
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_input_chars:
        return text
    compact_inventory = {}
    for key, rows in inventory.items():
        compact_inventory[key] = {
            "count": len(rows),
            "samples": sanitize_compact(rows[:8]),
        }
    compact_payload = {
        "report_id": report_id,
        "manifest": sanitize_compact(manifest),
        "runtime": sanitize_compact(runtime),
        "inventory": compact_inventory,
        "summaries": {name: truncate(clean_markdown(text), 1200) for name, text in summaries.items()},
        "baseline": {
            "asset_cards": baseline_assets[:40],
            "worktrace_count": worktrace_count,
            "worktrace_summary_md": truncate(baseline_worktrace_summary, 2500),
            "memory_candidates": baseline_memories[:30],
            "handover_signals": baseline_signals[:30],
        },
        "input_note": "Payload was compacted to respect DUCKDOCK_LLM_MAX_INPUT_CHARS.",
    }
    return truncate(json.dumps(compact_payload, ensure_ascii=False, indent=2), max_input_chars)


def request_llm_json(config: LLMConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    try:
        return request_llm_json_once(config, messages, response_format=True)
    except LLMHTTPError as exc:
        if exc.status_code == 400:
            return request_llm_json_once(config, messages, response_format=False)
        raise


def request_llm_json_once(config: LLMConfig, messages: list[dict[str, str]], *, response_format: bool) -> dict[str, Any]:
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.2,
        "stream": False,
    }
    if config.enable_thinking:
        body["enable_thinking"] = True
    if response_format:
        body["response_format"] = {"type": "json_object"}
    request_body = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=request_body, method="POST")
    req.add_header("Authorization", f"Bearer {config.api_key}")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_seconds) as response:
            trace_id = (
                response.headers.get("x-request-id")
                or response.headers.get("x-dashscope-request-id")
                or response.headers.get("x-amzn-requestid")
            )
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMHTTPError(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise WorkerError(f"LLM request failed: {exc.reason}") from exc
    response_json = json.loads(raw.decode("utf-8"))
    choices = response_json.get("choices") or []
    if not choices:
        raise WorkerError("LLM response has no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise WorkerError("LLM response has no content")
    parsed = json.loads(extract_json_object(content))
    if not isinstance(parsed, dict):
        raise WorkerError("LLM response JSON must be an object")
    usage = response_json.get("usage")
    if isinstance(usage, dict):
        parsed[LLM_USAGE_METADATA_KEY] = usage
    if trace_id:
        parsed[LLM_TRACE_METADATA_KEY] = trace_id
    return parsed


def extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise WorkerError("LLM response did not contain a JSON object")
    return stripped[start : end + 1]


def read_inventory(zf: zipfile.ZipFile) -> dict[str, list[dict[str, Any]]]:
    inventory: dict[str, list[dict[str, Any]]] = {}
    for name in zf.namelist():
        normalized = name.replace("\\", "/")
        if "/inventory/" not in f"/{normalized}" and not normalized.startswith("inventory/"):
            continue
        if normalized.endswith("/"):
            continue
        stem = Path(normalized).name.rsplit(".", 1)[0]
        if normalized.endswith(".ndjson"):
            rows = []
            text = zf.read(name).decode("utf-8-sig", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
            inventory.setdefault(stem, []).extend(rows)
        elif normalized.endswith(".json"):
            value = read_json_raw(zf, name)
            rows = normalize_json_items(value)
            inventory.setdefault(stem, []).extend(rows)
    return inventory


def read_summaries(zf: zipfile.ZipFile) -> dict[str, str]:
    summaries: dict[str, str] = {}
    for name in zf.namelist():
        normalized = name.replace("\\", "/")
        if "/summaries/" not in f"/{normalized}" and not normalized.startswith("summaries/"):
            continue
        if normalized.endswith(".md") or normalized.endswith(".txt"):
            summaries[Path(normalized).name] = zf.read(name).decode("utf-8-sig", errors="replace")
    return summaries


def build_asset_cards(inventory: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for group, rows in inventory.items():
        asset_type = ASSET_TYPE_BY_INVENTORY.get(group)
        if not asset_type:
            continue
        for row in rows:
            name = first_text(row, "name", "title", "display_name", "id", "external_id")
            if not name:
                continue
            external_id = first_text(row, "external_id", "id", "key", "path") or f"{asset_type}/{stable_id(name)}"
            cards.append(
                {
                    "external_id": external_id,
                    "asset_type": first_text(row, "asset_type", "type", "kind") or asset_type,
                    "name": name,
                    "description": first_text(row, "description", "summary"),
                    "criticality": normalize_level(first_text(row, "criticality", "risk_level"), default="medium"),
                    "status": first_text(row, "status") or "active",
                    "project": first_text(row, "project", "project_name", "workspace", "namespace"),
                    "owner_hint": first_text(row, "owner", "owner_hint", "creator", "created_by"),
                    "content_hash": first_text(row, "content_hash", "sha256", "hash"),
                    "confidence": as_float(row.get("confidence"), 0.75),
                }
            )
    return cards


def build_memory_candidates(inventory: dict[str, list[dict[str, Any]]], summaries: dict[str, str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for memory in inventory.get("memories", []) + inventory.get("memory", []) + inventory.get("knowledge_bases", []):
        title = first_text(memory, "title", "name", "id")
        summary = first_text(memory, "summary", "description")
        if not title or not summary:
            continue
        item = {
            "candidate_type": "project_context",
            "subject_type": first_text(memory, "scope", "subject_type", "type") or "knowledge_base",
            "subject_key": first_text(memory, "id", "external_id", "key") or stable_id(title),
            "title": title,
            "summary": summary,
            "confidence": as_float(memory.get("confidence"), 0.75),
            "sensitivity": first_text(memory, "sensitivity") or "internal",
        }
        add_unique_candidate(candidates, seen, item)
    for filename, text in summaries.items():
        if not text.strip():
            continue
        item = {
            "candidate_type": "worktrace_summary",
            "subject_type": "report_summary",
            "subject_key": filename,
            "title": summary_title(text) or filename,
            "summary": truncate(clean_markdown(text), 1000),
            "confidence": 0.7,
            "sensitivity": "internal",
        }
        add_unique_candidate(candidates, seen, item)
    return candidates


def build_handover_signals(assets: list[dict[str, Any]], inventory: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for asset in assets:
        criticality = str(asset.get("criticality") or "medium").lower()
        asset_type = str(asset.get("asset_type") or "other")
        key = str(asset.get("external_id") or stable_id(str(asset.get("name") or "asset")))
        if criticality in {"high", "critical"}:
            signals.append(
                {
                    "signal_type": "handover",
                    "subject_type": asset_type,
                    "subject_key": key,
                    "title": f"{asset.get('name')} needs owner confirmation",
                    "summary": "High-impact AI asset should be reviewed before employee or digital-worker handover.",
                    "confidence": as_float(asset.get("confidence"), 0.75),
                    "sensitivity": "internal",
                }
            )
        if asset_type == "credential_ref":
            signals.append(
                {
                    "signal_type": "risk",
                    "subject_type": "credential_ref",
                    "subject_key": key,
                    "title": f"{asset.get('name')} needs credential rotation review",
                    "summary": "Only the credential reference is recorded. No credential value is included.",
                    "confidence": 0.7,
                    "sensitivity": "confidential",
                }
            )
    for task in inventory.get("scheduled_tasks", []):
        title = first_text(task, "title", "name", "id")
        if title:
            signals.append(
                {
                    "signal_type": "handover",
                    "subject_type": "scheduled_task",
                    "subject_key": first_text(task, "id", "external_id", "key") or stable_id(title),
                    "title": f"{title} scheduled task should have a fallback owner",
                    "summary": "Scheduled AI automation should be checked during role handover.",
                    "confidence": 0.72,
                    "sensitivity": "internal",
                }
            )
    return signals


def normalize_asset_cards(value: Any) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for item in (value if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        name = first_text(item, "name", "title", "display_name")
        if not name:
            continue
        asset_type = normalize_choice(first_text(item, "asset_type", "type", "kind"), VALID_ASSET_TYPES, "other")
        external_id = first_text(item, "external_id", "id", "key", "path") or f"{asset_type}/{stable_id(name)}"
        cards.append(
            {
                "external_id": truncate(external_id, 255),
                "asset_type": asset_type,
                "name": truncate(name, 255),
                "description": truncate(first_text(item, "description", "summary") or "", 1000),
                "criticality": normalize_level(first_text(item, "criticality", "risk_level"), default="medium"),
                "status": normalize_choice(first_text(item, "status"), VALID_ASSET_STATUSES, "active"),
                "project": truncate(first_text(item, "project", "project_name", "workspace", "namespace") or "", 255),
                "owner_hint": truncate(first_text(item, "owner_hint", "owner", "creator", "created_by") or "", 255),
                "content_hash": truncate(first_text(item, "content_hash", "sha256", "hash") or "", 128),
                "confidence": as_float(item.get("confidence"), 0.7),
            }
        )
    return cards


def normalize_memory_candidates(value: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in (value if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        title = first_text(item, "title", "name")
        summary = first_text(item, "summary", "description")
        if not title or not summary:
            continue
        candidate = {
            "candidate_type": normalize_choice(first_text(item, "candidate_type", "type"), VALID_CANDIDATE_TYPES, "knowledge_note"),
            "subject_type": truncate(first_text(item, "subject_type", "scope") or "report", 128),
            "subject_key": truncate(first_text(item, "subject_key", "external_id", "id", "key") or stable_id(title), 255),
            "title": truncate(title, 255),
            "summary": truncate(summary, 1600),
            "confidence": as_float(item.get("confidence"), 0.7),
            "sensitivity": normalize_choice(first_text(item, "sensitivity"), VALID_SENSITIVITIES, "internal"),
        }
        add_unique_candidate(candidates, seen, candidate)
    return candidates


def normalize_handover_signals(value: Any) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in (value if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        title = first_text(item, "title", "name")
        summary = first_text(item, "summary", "description")
        if not title or not summary:
            continue
        signal_type = normalize_choice(first_text(item, "signal_type", "type"), {"handover", "risk"}, "handover")
        signal = {
            "signal_type": signal_type,
            "subject_type": truncate(first_text(item, "subject_type", "asset_type", "scope") or "other", 128),
            "subject_key": truncate(first_text(item, "subject_key", "external_id", "id", "key") or stable_id(title), 255),
            "title": truncate(title, 255),
            "summary": truncate(summary, 1600),
            "confidence": as_float(item.get("confidence"), 0.7),
            "sensitivity": normalize_choice(first_text(item, "sensitivity"), VALID_SENSITIVITIES - {"public"}, "internal"),
        }
        key = (signal["signal_type"], signal["subject_key"], signal["title"])
        if key in seen:
            continue
        seen.add(key)
        signals.append(signal)
    return signals


def merge_asset_cards(primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in primary + fallback:
        key = str(item.get("external_id") or "").strip() or stable_id(str(item.get("name") or "asset"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def merge_keyed_items(
    primary: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
    *,
    key_fields: tuple[str, str, str],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in primary + fallback:
        key = tuple(str(item.get(field) or "") for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def normalize_limitations(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [truncate(str(item), 500) for item in value if str(item).strip()][:10]


def build_worktrace_summary(
    *,
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    inventory: dict[str, list[dict[str, Any]]],
    summaries: dict[str, str],
) -> str:
    lines = [
        "# DuckDock analysis summary",
        "",
        f"- generated_at: {now_iso()}",
        f"- report_schema: {manifest.get('schema_version', 'unknown')}",
        f"- runtime_provider: {runtime.get('provider', manifest.get('provider', 'unknown'))}",
        f"- asset_records: {sum(len(rows) for key, rows in inventory.items() if key in ASSET_TYPE_BY_INVENTORY)}",
        f"- session_records: {len(inventory.get('sessions', [])) + len(inventory.get('work_traces', []))}",
        f"- memory_records: {len(inventory.get('memories', [])) + len(inventory.get('memory', []))}",
        "",
        "## Included summaries",
        "",
    ]
    if summaries:
        for filename, text in summaries.items():
            lines.append(f"### {filename}")
            lines.append("")
            lines.append(truncate(clean_markdown(text), 1200))
            lines.append("")
    else:
        lines.append("No human-readable report summary was included in the pack.")
    return "\n".join(lines).strip() + "\n"


def upload_results(result_uploads: list[dict[str, Any]], files: dict[str, Path]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for upload in result_uploads:
        kind = upload["kind"]
        path = files.get(kind)
        if path is None:
            continue
        upload_file(upload["upload_url"], path, upload["content_type"])
        artifacts.append(
            {
                "kind": kind,
                "filename": upload["filename"],
                "object_key": upload["object_key"],
                "content_type": upload["content_type"],
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "summary_json": {"generated_by": "duckdock-analysis-worker"},
            }
        )
    return artifacts


def request_json(method: str, url: str, *, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise WorkerError(f"HTTP {exc.code} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise WorkerError(f"HTTP request failed {url}: {exc.reason}") from exc
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def download_file(url: str, path: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, path.open("wb") as fh:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)


def upload_file(url: str, path: Path, content_type: str) -> None:
    req = urllib.request.Request(url, data=path.read_bytes(), method="PUT")
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise WorkerError(f"PUT failed for {path.name}: HTTP {exc.code}: {detail}") from exc


def heartbeat(*, api_base: str, token: str, job_id: int) -> None:
    request_json("POST", f"{api_base}/analysis/jobs/{job_id}/heartbeat", token=token, payload={})


def read_json_member(zf: zipfile.ZipFile, basename: str) -> dict[str, Any] | None:
    for name in zf.namelist():
        normalized = name.replace("\\", "/")
        if normalized == basename or normalized.endswith(f"/{basename}"):
            value = read_json_raw(zf, name)
            return value if isinstance(value, dict) else None
    return None


def read_json_raw(zf: zipfile.ZipFile, name: str) -> Any:
    try:
        return json.loads(zf.read(name).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def normalize_json_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "records", "assets", "sessions", "memories"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [value]
    return []


def write_json(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_text(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_compact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if any(secret in str(key).lower() for secret in SECRET_KEYS):
                result[key] = "[REDACTED]"
            elif isinstance(item, (dict, list)):
                result[key] = sanitize_compact(item)
            elif isinstance(item, str):
                result[key] = truncate(item, 500)
            else:
                result[key] = item
        return result
    if isinstance(value, list):
        return [sanitize_compact(item) for item in value[:100]]
    return value


def first_text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
        if item is not None and not isinstance(item, (dict, list)):
            text = str(item).strip()
            if text:
                return text
    return None


def normalize_level(value: str | None, *, default: str) -> str:
    if not value:
        return default
    normalized = value.lower().replace("-", "_")
    return normalized if normalized in {"low", "medium", "high", "critical"} else default


def normalize_choice(value: str | None, valid: set[str], default: str) -> str:
    if not value:
        return default
    normalized = value.strip().lower().replace("-", "_")
    return normalized if normalized in valid else default


def parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def summary_title(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:255] or None
        if stripped:
            return stripped[:80]
    return None


def clean_markdown(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def add_unique_candidate(candidates: list[dict[str, Any]], seen: set[tuple[str, str, str]], item: dict[str, Any]) -> None:
    key = (str(item.get("candidate_type")), str(item.get("subject_key")), str(item.get("title")))
    if key in seen:
        return
    seen.add(key)
    candidates.append(item)


def _report_id_from_job(job: dict[str, Any]) -> str:
    summary = job.get("summary_json") or {}
    return str(summary.get("report_id") or f"job-{job.get('id')}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkerError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
