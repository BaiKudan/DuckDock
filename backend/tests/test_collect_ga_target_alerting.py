from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_target_alerting as target_alerting
from scripts.ga_release_identity import build_release_binding


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
EXERCISE_ID = "ga-20260806-001"
TARGET = "customer-production"
SCHEDULE = "platform-primary"
DELIVERY_IDENTITY = "alert-delivery@example.com"
ONCALL_IDENTITY = "oncall-primary@example.com"


def _binding() -> dict:
    return build_release_binding(
        scope="target-production",
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
    )


def _generate_key(path: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
        check=True,
    )


def _sign(path: Path, key: Path, namespace: str) -> Path:
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", namespace, str(path)],
        check=True,
    )
    return Path(f"{path}.sig")


def _policy(tmp_path: Path, *, reused_key: bool = False) -> tuple[Path, Path, Path]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for signed alerting receipt tests")
    delivery_key = tmp_path / "delivery_key"
    oncall_key = tmp_path / "oncall_key"
    _generate_key(delivery_key)
    if reused_key:
        oncall_public = delivery_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    else:
        _generate_key(oncall_key)
        oncall_public = oncall_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    delivery_public = delivery_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    trust = tmp_path / "alerting_allowed_signers"
    trust.write_text(
        f"{DELIVERY_IDENTITY} {delivery_public}\n{ONCALL_IDENTITY} {oncall_public}\n",
        encoding="utf-8",
    )
    policy_path = tmp_path / "alerting-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": target_alerting.POLICY_SCHEMA_VERSION,
                "policy_id": "duckdock-target-alerting-authority",
                "organization": "DuckDock Test Operations",
                "allowed_signers_path": str(trust),
                "allowed_signers_sha256": hashlib.sha256(trust.read_bytes()).hexdigest(),
                "delivery_identities": [DELIVERY_IDENTITY],
                "oncall_schedules": {SCHEDULE: [ONCALL_IDENTITY]},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return policy_path, delivery_key, oncall_key


def _args(tmp_path: Path, policy_path: Path) -> SimpleNamespace:
    firing = tmp_path / "firing.json"
    ack = tmp_path / "ack.json"
    resolved = tmp_path / "resolved.json"
    return SimpleNamespace(
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        alertmanager_url="https://alerts.example.com",
        ca_file=None,
        bearer_token_file=None,
        exercise_id=EXERCISE_ID,
        oncall_schedule=SCHEDULE,
        alerting_policy=policy_path,
        delivery_signer_identity=DELIVERY_IDENTITY,
        oncall_signer_identity=ONCALL_IDENTITY,
        firing_receipt=firing,
        firing_signature=Path(f"{firing}.sig"),
        ack_receipt=ack,
        ack_signature=Path(f"{ack}.sig"),
        resolved_receipt=resolved,
        resolved_signature=Path(f"{resolved}.sig"),
        alert_duration=900,
        request_timeout=1.0,
        alert_state_timeout=1.0,
        receipt_timeout=1.0,
        output=tmp_path / "alerting.json",
        release_binding=_binding(),
    )


def _delivery_receipt(alert_name: str, event: str, observed_at: datetime) -> dict:
    return {
        "schema_version": target_alerting.DELIVERY_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "target_environment": TARGET,
        "alert_name": alert_name,
        "event": event,
        "receipt_id": f"delivery-{event}-001",
        "deliveries": [
            {
                "channel": "oncall-webhook",
                "receiver": "primary-oncall",
                "provider_receipt_id": f"provider-{event}-oncall",
                "delivered_at": observed_at.isoformat(),
            },
            {
                "channel": "pager",
                "receiver": "primary-pager",
                "provider_receipt_id": f"provider-{event}-pager",
                "delivered_at": observed_at.isoformat(),
            },
        ],
        "schedule": SCHEDULE,
        "delivered": True,
        "delivered_at": observed_at.isoformat(),
    }


def _acknowledgement(alert_name: str, observed_at: datetime) -> dict:
    return {
        "schema_version": target_alerting.ACK_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "target_environment": TARGET,
        "alert_name": alert_name,
        "receipt_id": "oncall-ack-001",
        "schedule": SCHEDULE,
        "acknowledged": True,
        "acknowledged_by": ONCALL_IDENTITY,
        "acknowledged_at": observed_at.isoformat(),
    }


class FakeAlertmanager:
    def __init__(
        self,
        args: SimpleNamespace,
        delivery_key: Path,
        oncall_key: Path,
        *,
        wrong_ack_key: bool = False,
    ) -> None:
        self.args = args
        self.delivery_key = delivery_key
        self.oncall_key = oncall_key
        self.wrong_ack_key = wrong_ack_key
        self.active = False
        self.posts = 0
        self.labels: dict[str, str] = {}

    def _write_signed(self, path: Path, payload: dict, key: Path, namespace: str) -> None:
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        _sign(path, key, namespace)

    def post_alert(self, *, labels: dict, starts_at: datetime, ends_at: datetime) -> dict:
        self.posts += 1
        self.labels = labels
        now = datetime.now(timezone.utc)
        alert_name = labels["alertname"]
        if self.posts == 1:
            self.active = True
            self._write_signed(
                self.args.firing_receipt,
                _delivery_receipt(alert_name, "firing", now),
                self.delivery_key,
                target_alerting.DELIVERY_SIGNATURE_NAMESPACE,
            )
            self._write_signed(
                self.args.ack_receipt,
                _acknowledgement(alert_name, datetime.now(timezone.utc)),
                self.delivery_key if self.wrong_ack_key else self.oncall_key,
                target_alerting.ACK_SIGNATURE_NAMESPACE,
            )
        else:
            self.active = False
            self._write_signed(
                self.args.resolved_receipt,
                _delivery_receipt(alert_name, "resolved", now),
                self.delivery_key,
                target_alerting.DELIVERY_SIGNATURE_NAMESPACE,
            )
        payload = [
            {
                "labels": labels,
                "annotations": {"summary": "DuckDock GA signed on-call delivery exercise"},
                "startsAt": starts_at.isoformat(),
                "endsAt": ends_at.isoformat(),
            }
        ]
        raw_request = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        raw_response = "{}"
        return {
            "observed_at": now.isoformat(),
            "http_status": 200,
            "response_bytes": len(raw_response),
            "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
            "request_sha256": hashlib.sha256(raw_request.encode()).hexdigest(),
            "raw_response_body": raw_response,
            "raw_request_body": raw_request,
            "request_payload": payload,
        }

    def active_observation(self, alert_name: str) -> dict:
        raw_response = json.dumps(
            [{"labels": self.labels}] if self.active else [],
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "http_status": 200,
            "response_bytes": len(raw_response),
            "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
            "request_sha256": hashlib.sha256(b"").hexdigest(),
            "raw_response_body": raw_response,
            "raw_request_body": "",
            "matching_alerts": 1 if self.active else 0,
            "label_selector_sha256": target_alerting._canonical_digest(
                {"alertname": alert_name}
            ),
        }


class AlertmanagerAPIHandler(BaseHTTPRequestHandler):
    active_labels: dict[str, str] | None = None

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        alert = payload[0]
        ends_at = datetime.fromisoformat(alert["endsAt"].replace("Z", "+00:00"))
        self.__class__.active_labels = (
            alert["labels"] if ends_at > datetime.now(timezone.utc) else None
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self) -> None:  # noqa: N802
        payload = (
            [{"labels": self.__class__.active_labels}]
            if self.__class__.active_labels is not None
            else []
        )
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_alertmanager_client_posts_and_observes_same_alert_state() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), AlertmanagerAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = target_alerting.AlertmanagerClient(
        f"http://127.0.0.1:{server.server_port}",
        ca_file=None,
        bearer_token_file=None,
        timeout_seconds=1.0,
    )
    labels = {
        "alertname": "DuckDockGA_client-test",
        "severity": "critical",
    }
    started_at = datetime.now(timezone.utc)
    try:
        firing = client.post_alert(
            labels=labels,
            starts_at=started_at,
            ends_at=started_at + timedelta(minutes=5),
        )
        active = client.active_observation(labels["alertname"])
        resolved = client.post_alert(
            labels=labels,
            starts_at=started_at,
            ends_at=datetime.now(timezone.utc),
        )
        inactive = client.active_observation(labels["alertname"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert firing["http_status"] == 200
    assert active["matching_alerts"] == 1
    assert resolved["request_sha256"] != firing["request_sha256"]
    assert inactive["matching_alerts"] == 0


def test_target_alerting_collector_binds_active_alert_and_signed_receipts(tmp_path: Path) -> None:
    policy_path, delivery_key, oncall_key = _policy(tmp_path)
    args = _args(tmp_path, policy_path)

    report = target_alerting.collect(
        args,
        client=FakeAlertmanager(args, delivery_key, oncall_key),
    )

    assert report["schema_version"] == target_alerting.SCHEMA_VERSION
    assert report["status"] == "PASS"
    assert report["alert_exercise"]["active_observation"]["passed"] is True
    assert report["alert_exercise"]["inactive_observation"]["passed"] is True
    assert report["firing_receipt"]["event"] == "firing"
    assert report["resolved_receipt"]["event"] == "resolved"
    assert report["oncall_acknowledgement"]["acknowledged_by"] == ONCALL_IDENTITY


def test_alerting_policy_rejects_public_key_reuse_across_service_and_human(
    tmp_path: Path,
) -> None:
    policy_path, _, _ = _policy(tmp_path, reused_key=True)

    with pytest.raises(ValueError, match="reuses a public key"):
        target_alerting.load_alerting_policy(
            policy_path,
            schedule=SCHEDULE,
            delivery_identity=DELIVERY_IDENTITY,
            oncall_identity=ONCALL_IDENTITY,
        )


def test_wrong_key_cannot_create_oncall_acknowledgement(tmp_path: Path) -> None:
    policy_path, delivery_key, oncall_key = _policy(tmp_path)
    args = _args(tmp_path, policy_path)
    args.receipt_timeout = 0.02
    fake = FakeAlertmanager(
        args,
        delivery_key,
        oncall_key,
        wrong_ack_key=True,
    )

    with pytest.raises(RuntimeError, match="invalid alerting receipt signature"):
        target_alerting.collect(
            args,
            client=fake,
        )
    assert fake.posts == 2
    assert fake.active is False


def test_receipt_replay_is_rejected_before_alert_injection(tmp_path: Path) -> None:
    policy_path, delivery_key, oncall_key = _policy(tmp_path)
    args = _args(tmp_path, policy_path)
    args.firing_receipt.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must not exist"):
        target_alerting.collect(
            args,
            client=FakeAlertmanager(args, delivery_key, oncall_key),
        )


def test_delivery_receipt_requires_two_distinct_channels_and_receivers() -> None:
    alert_name = f"DuckDockGA_{EXERCISE_ID}"
    receipt = _delivery_receipt(alert_name, "firing", datetime.now(timezone.utc))
    receipt["deliveries"] = receipt["deliveries"][:1]

    with pytest.raises(ValueError, match="invalid signed firing"):
        target_alerting.validate_delivery_receipt(
            receipt,
            exercise_id=EXERCISE_ID,
            target_environment=TARGET,
            alert_name=alert_name,
            schedule=SCHEDULE,
            event="firing",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://alerts.example.com",
        "https://127.0.0.1:9093",
        "https://alerts.example.invalid",
        "https://user:password@alerts.example.com",
    ],
)
def test_target_alertmanager_url_must_be_credential_free_nonlocal_https(url: str) -> None:
    with pytest.raises(ValueError, match="credential-free non-local HTTPS"):
        target_alerting.require_https_url(url)
