# S4 / RT-08 Dynamic Adapter Fleet and G2 Technical Evidence

Date: 2026-07-30
Base commit: `674bc928ee0e213ce1356db4e20f98efca5921ea`
Migration head: `20260730_0037`
Decision: **accepted; Product/Architecture Owner explicitly approved G2 on 2026-07-31**

## Outcome

RT-08 closes the final E02 implementation slice without inflating profile
capability claims:

- OpenClaw Reporter advertises DD-C3 only when Session, Run, trace correlation
  and durable replay are all dynamically accepted.
- Generic OTLP advertises DD-C2 only when an active Namespace TelemetrySink is
  available; the legacy adapter now returns `degraded` with
  `dynamic_handshake_required` instead of a false `ok`.
- Pack/ATIF advertises `DD-C1+ARTIFACT`, accepts `artifact_import` and
  `partial_loss`, and rejects `resumable_upload`. It does not claim full DD-C3.

The machine-readable certification report and checksum sidecar are:

- `g2-adapter-conformance-report-20260730.json`
- `g2-adapter-conformance-report-20260730.sha256`

## Delivered contract

Revision `20260730_0037` adds opaque `RuntimeInstance.public_id`,
credential/Runtime/Namespace-bound `AdapterHandshake`, bounded
`AdapterHeartbeatRecord` history and optional Pack-to-handshake linkage.
The migration passed real MySQL `0036 -> 0037 -> 0036 -> 0037`; current head is
0037 and `alembic check` reports no new operations.

New operations:

| Operation | Contract |
|---|---|
| `POST /api/v2/reporter/handshakes` | strict descriptor, 128-bit base64url nonce, same-descriptor replay, changed-descriptor conflict, dependency-derived accept/reject |
| `POST /api/v2/reporter/heartbeats` | expiry, boot/config/capability drift, Collector status/version and append-only metadata history |
| `GET /api/v2/fleet/runtimes` | Namespace RBAC, opaque IDs, version/capability/heartbeat/Collector/drift/workload summary |

The v1 compatibility surface no longer seeds a hard-coded capability matrix.
New Runtime rows start without capability claims, and capability snapshots are
created only by a validated handshake.

## Shared DD-C0 mapping

| Conformance ID | Evidence |
|---|---|
| CONF-C0-001 | three supported profiles negotiate a strict descriptor |
| CONF-C0-002 | unsupported protocol is validation failure; unknown capabilities are returned in `rejected_capabilities` |
| CONF-C0-003 | response Runtime is derived from ReporterCredential mapping |
| CONF-C0-004 | descriptor forbids Namespace/Runtime identity fields; cross-credential handshake lookup fails closed |
| CONF-C0-005 | expired handshake rejects heartbeat and a new nonce establishes a replacement |
| CONF-C0-006 | response contains opaque `rt_`/`hs_` identifiers and no internal Namespace/Runtime/credential IDs |
| CONF-C0-007 | same nonce + same canonical descriptor replays; changed descriptor persists a 409 conflict/degraded state |

Pack manifests may include the negotiated `handshake_id`; preflight binds it to
the exact Reporter credential and requires `artifact_import`. Generic OTLP may
send `DuckDock-Handshake-Id`; when present it must be an active
`generic-otlp-bridge` handshake with both DD-C2 projection capabilities.
Unbound legacy requests remain compatible but do not constitute G2-certified
traffic.

## Fleet behavior

The Fleet projection shows:

- adapter profile, adapter version and certified capability level;
- accepted and rejected capabilities;
- handshake state and expiry;
- heartbeat state plus the newest 20 persisted heartbeat records;
- boot/config/capability drift;
- Generic Collector health and version;
- latest Run, pending Pack imports and quarantined Pack/trace counts;
- explicitly named Namespace-level TelemetrySink counts.

An unhandshaken Runtime renders `handshake_status=NONE`,
`heartbeat_state=NEVER`, no certified level and an empty capability set. The
query is batched across up to 200 Runtime rows, including a per-handshake
windowed heartbeat-history read, rather than issuing per-Runtime queries.

The React `/fleet` page exposes the same state with Namespace selection and
15-second refresh. It uses only opaque Runtime IDs. A component test verifies
version, capability and heartbeat-history rendering; production build passes.

## Verification

```text
focused three-profile / contract lane             61 passed, 2 skipped
real MySQL Fleet history/query lane                1 passed
live MinIO signed PUT/finalize/readback lane        1 passed
Foundation                                       224 passed, 14 skipped
full backend                                     890 passed, 18 skipped
frontend                                           35 passed
Ruff / compile / import                            PASS
mypy                                               82 errors = baseline 82
MySQL migration downgrade / upgrade / check        PASS / head 0037 / clean
frontend lint                                      0 errors, 9 existing warnings
```

The isolated `duckdock_rt08_test` database used by the real-MySQL lane was
dropped after the test. The default development services remain running:
backend, worker, beat, MySQL, Redis, MinIO, frontend and Collector; backend,
MySQL and Redis health checks are healthy, and the backend exposes all three
RT-08 operation IDs.

## Non-claims and next work

RT-08 completes dynamic capability/Fleet implementation, but it does not
complete NEXT-003. ATIF export, resumable multipart upload, durable Pack batch
ack/cursor and evaluation replay remain open. Pack/ATIF therefore remains
`DD-C1+ARTIFACT`, not DD-C3.

Product/Architecture Owner explicitly replied “批准，继续继续” on 2026-07-31.
G2/M2, S4 and E02 are formally closed; E02 is counted at 100%.
