"""Compatibility smoke test between the installed Langfuse SDK and the
self-hosted Langfuse server.

Mirrors exactly the call pattern in app/services/langfuse_service.py:
  1. build client with (public_key, secret_key, host, environment, timeout)
  2. open a parent span (start_as_current_observation as_type="span")
  3. open a nested generation (as_type="generation")
  4. flush
  5. query back via client.api.observations.get_many with name + time window
  6. resolve trace_url via client.get_trace_url

Run from repo root:
  .venv/Scripts/python backend/scripts/verify_langfuse.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone


def main() -> int:
    # Load env from repo .env so we match what the backend sees
    from dotenv import load_dotenv
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    load_dotenv(os.path.join(repo_root, ".env"))

    pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sk = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3200")
    env = os.environ.get("LANGFUSE_ENVIRONMENT", "development")
    timeout = int(os.environ.get("LANGFUSE_TIMEOUT_SECONDS", "5"))
    print(f"[cfg] host={host}  env={env}  pk={pk[:8]}…  timeout={timeout}s")

    try:
        from langfuse import Langfuse
    except ImportError as exc:
        print(f"[FAIL] SDK import: {exc}")
        return 1

    try:
        client = Langfuse(
            public_key=pk,
            secret_key=sk,
            host=host,
            environment=env,
            timeout=timeout,
        )
    except Exception as exc:
        print(f"[FAIL] client ctor: {type(exc).__name__}: {exc}")
        return 1
    print(f"[ok]  Langfuse client built · SDK={client.__class__.__module__}")

    # Sanity: auth check
    try:
        ok = client.auth_check()
        print(f"[{'ok ' if ok else 'FAIL'}] auth_check={ok}")
        if not ok:
            return 2
    except Exception as exc:
        print(f"[FAIL] auth_check raised: {type(exc).__name__}: {exc}")
        return 2

    # Mirror langfuse_service.start_clinic_evaluation
    eval_id = int(time.time())  # unique marker
    started = datetime.now(timezone.utc)
    try:
        with client.start_as_current_observation(
            name="clinic-evaluation",
            as_type="span",
            input={
                "namespace": "__verify__",
                "namespace_id": -1,
                "clinic_evaluation_id": eval_id,
                "skill_count": 1,
                "mode": "hybrid",
            },
            metadata={
                "rubric_version": "v1",
                "prompt_version": "v1",
                "clinic_evaluation_id": eval_id,
                "namespace_id": -1,
                "namespace_name": "__verify__",
            },
        ) as span:
            print(f"[ok]  parent span opened · id={getattr(span, 'id', '?')}")
            # Mirror start_judge_generation
            with span.start_as_current_observation(
                name="clinic-judge-completeness",
                as_type="generation",
                model="test-model",
                input={"dimension": "completeness", "facts_summary": "verify", "sampled_skills": ["demo"]},
                metadata={"prompt_version": "v1", "rubric_version": "v1"},
            ) as gen:
                gen.update(output={"score": 0.9})
                print(f"[ok]  nested generation opened · id={getattr(gen, 'id', '?')}")
    except Exception as exc:
        print(f"[FAIL] span/generation: {type(exc).__name__}: {exc}")
        return 3

    try:
        client.flush()
        print("[ok]  flush() returned")
    except Exception as exc:
        print(f"[FAIL] flush: {type(exc).__name__}: {exc}")
        return 4

    # Give the server a couple seconds for ingest
    print("[..]  sleeping 6s for ingest…")
    time.sleep(6)

    # Mirror get_trace_link_for_evaluation
    from_ts = started - timedelta(minutes=2)
    to_ts = started + timedelta(hours=1)
    try:
        observations = client.api.observations.get_many(
            fields="core,basic,metadata",
            name="clinic-evaluation",
            limit=20,
            from_start_time=from_ts,
            to_start_time=to_ts,
        )
    except Exception as exc:
        print(f"[FAIL] observations.get_many: {type(exc).__name__}: {exc}")
        return 5
    print(f"[ok]  observations.get_many returned {len(observations.data)} rows")

    match = None
    for observation in observations.data:
        meta = observation.metadata or {}
        if meta.get("clinic_evaluation_id") == eval_id:
            match = observation
            break
    if match is None:
        print(f"[FAIL] no observation with clinic_evaluation_id={eval_id}")
        # Dump first few to help diagnose
        for observation in observations.data[:3]:
            print(
                "        candidate "
                f"id={observation.id} name={observation.name} "
                f"meta={observation.metadata}"
            )
        return 6

    if not match.trace_id:
        print(f"[FAIL] matched observation {match.id} has no trace_id")
        return 6

    print(f"[ok]  observation matched · id={match.id} trace_id={match.trace_id}")
    print(f"       name={match.name}")
    print(f"       metadata.clinic_evaluation_id={match.metadata.get('clinic_evaluation_id')}")

    # get_trace_url
    try:
        url = client.get_trace_url(trace_id=match.trace_id)
        print(f"[ok]  get_trace_url → {url}")
    except Exception as exc:
        print(f"[FAIL] get_trace_url: {type(exc).__name__}: {exc}")
        return 7

    print("\n[SUCCESS] SDK 4.x <-> server v4 round-trip OK for clinic pattern")
    return 0


if __name__ == "__main__":
    sys.exit(main())
