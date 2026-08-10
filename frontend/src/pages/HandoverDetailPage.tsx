import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  Download,
  FileArchive,
  GitBranch,
  KeyRound,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";
import { useNavigate, useParams } from "../router";

import {
  controlPlaneApi,
  peopleApi,
  type AIAsset,
  type AiAssist,
  type ApprovalStatus,
  type ApprovalTask,
  type ApprovalType,
  type EvidenceItem,
  type ExecutionAction,
  type ExecutionStatus,
  type HandoverCase,
  type HandoverAcceptanceV2,
  type HandoverEvidenceSnapshotV2,
  type HandoverItem,
  type HandoverSignedPackageV2,
  type HandoverSigningPayloadV2,
  type PersonHandoverProfile,
} from "../api/client";
import { AiAssistBadge } from "../components/AiAssistBadge";
import { Badge, Button, Card, Input, MonoPill, PageHeader, type Tone } from "../components/ui";
import { parseAiAssistHeader } from "../utils/aiAssist";
import { actionRequiresEvidence } from "../utils/handoverEvidence";

const SELECT_CLASS =
  "w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition-colors focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100";

const TEXTAREA_CLASS =
  "w-full resize-y rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition-colors placeholder:text-slate-400 focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100";

const approvalTypes: { label: string; value: ApprovalType }[] = [
  { label: "Manager", value: "manager" },
  { label: "Receiver", value: "receiver" },
  { label: "Security", value: "security" },
  { label: "Platform", value: "platform_admin" },
];

export default function HandoverDetailPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const navigate = useNavigate();
  const numericCaseId = Number(caseId);

  const [handover, setHandover] = useState<HandoverCase | null>(null);
  const [items, setItems] = useState<HandoverItem[]>([]);
  const [approvals, setApprovals] = useState<ApprovalTask[]>([]);
  const [actions, setActions] = useState<ExecutionAction[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [people, setPeople] = useState<PersonHandoverProfile[]>([]);
  const [assets, setAssets] = useState<AIAsset[]>([]);
  const [aiAssist, setAiAssist] = useState<AiAssist | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const [editTitle, setEditTitle] = useState("");
  const [editSubjectUserId, setEditSubjectUserId] = useState("");
  const [editReceiverUserId, setEditReceiverUserId] = useState("");
  const [editFallbackOwnerUserId, setEditFallbackOwnerUserId] = useState("");
  const [approvalType, setApprovalType] = useState<ApprovalType>("manager");
  const [approvalUserId, setApprovalUserId] = useState("");
  const [approvalComment, setApprovalComment] = useState("");
  const [receiptNote, setReceiptNote] = useState<Record<number, string>>({});
  const [receiptResult, setReceiptResult] = useState<Record<number, Extract<ExecutionStatus, "succeeded" | "failed">>>({});
  const [receiptEvidence, setReceiptEvidence] = useState<Record<number, number[]>>({});
  const [uploadFile, setUploadFile] = useState<Record<number, File | null>>({});
  const [uploadSummary, setUploadSummary] = useState<Record<number, string>>({});
  const [verifyNote, setVerifyNote] = useState("");
  const [acknowledgeFailures, setAcknowledgeFailures] = useState(false);
  const [downloadReason, setDownloadReason] = useState("handover package review");
  const [snapshotsV2, setSnapshotsV2] = useState<HandoverEvidenceSnapshotV2[]>([]);
  const [acceptancesV2, setAcceptancesV2] = useState<HandoverAcceptanceV2[]>([]);
  const [packagesV2, setPackagesV2] = useState<HandoverSignedPackageV2[]>([]);
  const [obligationNotes, setObligationNotes] = useState<Record<string, string>>({});
  const [obligationEvidence, setObligationEvidence] = useState<Record<string, number[]>>({});
  const [acceptanceComment, setAcceptanceComment] = useState("Receiver accepts the exact evidence snapshot");
  const [signingKeyPublicId, setSigningKeyPublicId] = useState("");
  const [detachedSignature, setDetachedSignature] = useState("");
  const [signingPayload, setSigningPayload] = useState<HandoverSigningPayloadV2 | null>(null);

  const load = useCallback(async () => {
    if (!Number.isFinite(numericCaseId)) return;
    setLoading(true);
    setError("");
    try {
      const caseRes = await controlPlaneApi.getHandover(numericCaseId);
      const [itemRes, approvalRes, actionRes, evidenceRes, peopleRes, assetRes] = await Promise.all([
        controlPlaneApi.listHandoverItems(numericCaseId),
        controlPlaneApi.listHandoverApprovals(numericCaseId),
        controlPlaneApi.listExecutionActions(numericCaseId),
        controlPlaneApi.listEvidence(),
        peopleApi.listPeople(),
        controlPlaneApi.listAssets(),
      ]);
      const [snapshotRes, acceptanceRes, packageRes] = caseRes.data.namespace_id
        ? await Promise.all([
            controlPlaneApi.listHandoverSnapshotsV2(caseRes.data.namespace_id, numericCaseId),
            controlPlaneApi.listHandoverAcceptancesV2(caseRes.data.namespace_id, numericCaseId),
            controlPlaneApi.listHandoverSignedPackagesV2(caseRes.data.namespace_id, numericCaseId),
          ])
        : [{ data: [] }, { data: [] }, { data: [] }];
      setHandover(caseRes.data);
      setItems(itemRes.data);
      setApprovals(approvalRes.data);
      setActions(actionRes.data);
      setEvidence(evidenceRes.data);
      setPeople(peopleRes.data);
      setAssets(assetRes.data);
      setSnapshotsV2(snapshotRes.data);
      setAcceptancesV2(acceptanceRes.data);
      setPackagesV2(packageRes.data);
      setEditTitle(caseRes.data.title);
      setEditSubjectUserId(caseRes.data.subject_user_id ? String(caseRes.data.subject_user_id) : "");
      setEditReceiverUserId(caseRes.data.receiver_user_id ? String(caseRes.data.receiver_user_id) : "");
      setEditFallbackOwnerUserId(caseRes.data.fallback_owner_user_id ? String(caseRes.data.fallback_owner_user_id) : "");
    } catch (err: unknown) {
      setError(getErrorDetail(err) ?? "交接详情加载失败");
    } finally {
      setLoading(false);
    }
  }, [numericCaseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const itemById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);
  const assetById = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets]);
  const evidenceById = useMemo(() => new Map(evidence.map((item) => [item.id, item])), [evidence]);
  const caseEvidenceIds = useMemo(() => {
    const ids = new Set<number>();
    items.forEach((item) => {
      if (item.evidence_id) ids.add(item.evidence_id);
    });
    actions.forEach((action) => action.evidence_ids.forEach((id) => ids.add(id)));
    return ids;
  }, [actions, items]);
  const caseObjectNeedle = `/handovers/case-${handover?.id ?? numericCaseId}/`;
  const caseEvidence = evidence.filter(
    (item) => caseEvidenceIds.has(item.id) || Boolean(item.object_uri?.includes(caseObjectNeedle))
  );

  async function runAction(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
    } catch (err: unknown) {
      setError(getErrorDetail(err) ?? fallback);
    } finally {
      setBusy(false);
    }
  }

  async function handleAnalyze() {
    setBusy(true);
    setError("");
    try {
      const response = await controlPlaneApi.analyzeHandover(numericCaseId);
      setItems(response.data);
      setAiAssist(parseAiAssistHeader(response.headers["x-ai-assist"]));
      await load();
    } catch (err: unknown) {
      setError(getErrorDetail(err) ?? "交接分析失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleEvidenceDownload(evidenceId: number) {
    await runAction(async () => {
      const { data } = await controlPlaneApi.requestEvidenceDownloadLink(evidenceId, {
        reason: downloadReason || "handover evidence review",
      });
      window.open(data.download_url, "_blank", "noopener,noreferrer");
    }, "证据下载链接签发失败");
  }

  async function handleEvidenceUpload(actionId: number) {
    const file = uploadFile[actionId];
    if (!file) {
      setError("请选择执行证据文件");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("summary", uploadSummary[actionId]?.trim() || `execution evidence for action #${actionId}`);
    form.append("visibility", "sensitive");
    await runAction(async () => {
      const { data } = await controlPlaneApi.uploadHandoverEvidence(numericCaseId, form);
      setReceiptEvidence((current) => ({
        ...current,
        [actionId]: Array.from(new Set([...(current[actionId] ?? []), data.id])),
      }));
      setUploadFile((current) => ({ ...current, [actionId]: null }));
      setUploadSummary((current) => ({ ...current, [actionId]: "" }));
    }, "执行证据上传失败");
  }

  if (!handover && loading) {
    return <div className="app-page text-sm text-slate-500">加载中...</div>;
  }

  if (!handover) {
    return <div className="app-page text-sm text-slate-500">{error || "未找到交接单"}</div>;
  }

  const packageEnabled = ["verifying", "completed"].includes(handover.status);
  const terminalActions = actions.filter((action) => ["succeeded", "failed"].includes(action.status)).length;
  const latestSnapshotV2 = snapshotsV2[0] ?? null;
  const acceptedV2 = acceptancesV2.find((item) => item.decision === "ACCEPTED") ?? null;

  return (
    <div className="app-page max-w-7xl space-y-6">
      <Button variant="link" className="px-0" onClick={() => navigate("/control-plane")} icon={<ArrowLeft className="h-4 w-4" />}>
        返回控制台
      </Button>

      <PageHeader
        eyebrow="HANDOVER"
        title={handover.title}
        description={
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge tone={handoverStatusTone(handover.status)}>{handover.status}</Badge>
            <MonoPill>case #{handover.id}</MonoPill>
            <MonoPill>{handover.case_type}</MonoPill>
            {aiAssist ? <AiAssistBadge aiAssist={aiAssist} /> : null}
          </div>
        }
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" disabled={busy || loading} onClick={() => void load()} icon={<RefreshCw className="h-4 w-4" />}>
              刷新
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => void runAction(() => controlPlaneApi.collectHandover(handover.id), "采集失败")}>
              采集
            </Button>
            <Button disabled={busy} onClick={() => void handleAnalyze()}>
              分析
            </Button>
          </div>
        }
      />

      {error ? <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}

      <Card title="交接属性" padded>
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_11rem_11rem_11rem_auto]">
          <label className="block">
            <div className="table-label mb-2">标题</div>
            <Input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} />
          </label>
          <label className="block">
            <div className="table-label mb-2">交接对象</div>
            <select value={editSubjectUserId} onChange={(event) => setEditSubjectUserId(event.target.value)} className={SELECT_CLASS}>
              <option value="">未指定</option>
              {people.map((person) => <option key={person.id} value={person.id}>{person.username}</option>)}
            </select>
          </label>
          <label className="block">
            <div className="table-label mb-2">接收人</div>
            <select value={editReceiverUserId} onChange={(event) => setEditReceiverUserId(event.target.value)} className={SELECT_CLASS}>
              <option value="">未指定</option>
              {people.map((person) => <option key={person.id} value={person.id}>{person.username}</option>)}
            </select>
          </label>
          <label className="block">
            <div className="table-label mb-2">Fallback owner</div>
            <select value={editFallbackOwnerUserId} onChange={(event) => setEditFallbackOwnerUserId(event.target.value)} className={SELECT_CLASS}>
              <option value="">未指定</option>
              {people.map((person) => <option key={person.id} value={person.id}>{person.username}</option>)}
            </select>
          </label>
          <div className="flex items-end">
            <Button
              disabled={busy || ["completed", "rejected", "cancelled"].includes(handover.status)}
              onClick={() =>
                void runAction(
                  () =>
                    controlPlaneApi.updateHandover(handover.id, {
                      title: editTitle,
                      subject_user_id: editSubjectUserId ? Number(editSubjectUserId) : null,
                      receiver_user_id: editReceiverUserId ? Number(editReceiverUserId) : null,
                      fallback_owner_user_id: editFallbackOwnerUserId ? Number(editFallbackOwnerUserId) : null,
                    }),
                  "保存交接属性失败"
                )
              }
            >
              保存
            </Button>
          </div>
        </div>
      </Card>

      <Card
        title="Handover 2.0 · Evidence snapshot"
        description="冻结生产版本、依赖图、最近运行、Eval baseline、权限、风险与 runbook；义务解决后由接收人验收并外部签名。"
        action={
          <Button
            size="sm"
            disabled={busy || !handover.namespace_id}
            onClick={() =>
              void runAction(
                () => controlPlaneApi.createHandoverSnapshotV2(handover.id, `snapshot-${handover.id}-${Date.now()}`),
                "证据快照创建失败",
              )
            }
            icon={<GitBranch className="h-4 w-4" />}
          >
            创建新快照
          </Button>
        }
      >
        {!latestSnapshotV2 ? (
          <div className="px-5 py-8 text-sm text-slate-500">还没有不可变证据快照。</div>
        ) : (
          <div className="space-y-5 p-5 sm:p-6">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <SnapshotMetric label="快照序号" value={`#${latestSnapshotV2.sequence}`} />
              <SnapshotMetric label="当前 readiness" value={latestSnapshotV2.current_readiness_outcome} tone={latestSnapshotV2.current_readiness_outcome === "READY" ? "emerald" : "rose"} />
              <SnapshotMetric label="依赖图" value={`${latestSnapshotV2.node_count} nodes / ${latestSnapshotV2.edge_count} edges`} />
              <SnapshotMetric label="检查项" value={String(latestSnapshotV2.check_count)} />
              <SnapshotMetric label="未解决阻塞" value={String(latestSnapshotV2.open_blocking_obligation_count)} tone={latestSnapshotV2.open_blocking_obligation_count ? "rose" : "emerald"} />
            </div>

            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
              <div className="table-label">Snapshot digest</div>
              <div className="mt-1 break-all font-mono text-xs text-slate-700">{latestSnapshotV2.snapshot_digest}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                {Array.from(new Set(latestSnapshotV2.nodes.map((node) => String(node.kind ?? "unknown")))).map((kind) => (
                  <MonoPill key={kind}>{kind}</MonoPill>
                ))}
              </div>
            </div>

            <div>
              <div className="mb-2 text-sm font-semibold text-slate-900">Readiness checks</div>
              <div className="grid gap-2 lg:grid-cols-2">
                {latestSnapshotV2.readiness.map((check, index) => {
                  const outcome = String(check.outcome ?? "BLOCK");
                  return (
                    <div key={`${String(check.key ?? "check")}-${index}`} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-xs text-slate-700">{String(check.key ?? "-")}</div>
                        <div className="mt-1 text-xs text-slate-500">{String(check.reason_code ?? "-")}</div>
                      </div>
                      <Badge tone={outcome === "PASS" ? "emerald" : "rose"}>{outcome}</Badge>
                    </div>
                  );
                })}
              </div>
            </div>

            <div>
              <div className="mb-2 text-sm font-semibold text-slate-900">Obligations</div>
              <div className="space-y-3">
                {latestSnapshotV2.obligations.map((obligation) => {
                  const selected = obligationEvidence[obligation.public_id] ?? [];
                  return (
                    <div key={obligation.public_id} className="rounded-lg border border-slate-200 p-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="text-sm font-semibold text-slate-900">{obligation.title}</div>
                          <div className="mt-1 flex flex-wrap gap-2">
                            <MonoPill>{obligation.obligation_type}</MonoPill>
                            <Badge tone={obligation.severity === "BLOCKING" ? "rose" : "amber"}>{obligation.severity}</Badge>
                            {obligation.requires_evidence ? <Badge tone="amber">evidence required</Badge> : null}
                          </div>
                        </div>
                        {obligation.receipt ? <Badge tone={obligation.receipt.decision === "FULFILLED" ? "emerald" : "rose"}>{obligation.receipt.decision}</Badge> : <Badge tone="amber">OPEN</Badge>}
                      </div>
                      {!obligation.receipt ? (
                        <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                          <Input
                            value={obligationNotes[obligation.public_id] ?? ""}
                            onChange={(event) => setObligationNotes((current) => ({ ...current, [obligation.public_id]: event.target.value }))}
                            placeholder="义务处置说明"
                          />
                          <select
                            multiple
                            value={selected.map(String)}
                            onChange={(event) =>
                              setObligationEvidence((current) => ({
                                ...current,
                                [obligation.public_id]: Array.from(event.target.selectedOptions).map((option) => Number(option.value)),
                              }))
                            }
                            className={`${SELECT_CLASS} min-h-10`}
                          >
                            {caseEvidence.map((item) => <option key={item.id} value={item.id}>#{item.id} {item.summary}</option>)}
                          </select>
                          <Button
                            size="sm"
                            disabled={busy || !obligationNotes[obligation.public_id]?.trim() || (obligation.requires_evidence && selected.length === 0)}
                            onClick={() =>
                              void runAction(
                                () => controlPlaneApi.recordHandoverObligationReceiptV2(obligation.public_id, {
                                  decision: "FULFILLED",
                                  note: obligationNotes[obligation.public_id] || "resolved",
                                  evidence_ids: selected,
                                  idempotency_key: `obligation-${obligation.public_id}`,
                                }),
                                "义务回执失败",
                              )
                            }
                          >
                            标记完成
                          </Button>
                        </div>
                      ) : (
                        <div className="mt-3 text-xs text-slate-500">receipt {obligation.receipt.public_id} · {obligation.receipt.receipt_digest}</div>
                      )}
                    </div>
                  );
                })}
                {!latestSnapshotV2.obligations.length ? <div className="text-sm text-slate-500">快照没有生成义务。</div> : null}
              </div>
            </div>

            <div className="grid gap-4 border-t border-slate-200 pt-5 xl:grid-cols-2">
              <div className="space-y-3">
                <div className="text-sm font-semibold text-slate-900">Receiver acceptance</div>
                {acceptedV2 ? (
                  <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
                    ACCEPTED · {acceptedV2.public_id}<div className="mt-1 break-all font-mono text-xs">{acceptedV2.acceptance_digest}</div>
                  </div>
                ) : (
                  <>
                    <textarea value={acceptanceComment} onChange={(event) => setAcceptanceComment(event.target.value)} rows={3} className={TEXTAREA_CLASS} />
                    <Button
                      disabled={busy || !packageEnabled || latestSnapshotV2.open_blocking_obligation_count > 0 || acceptanceComment.trim().length < 2}
                      onClick={() =>
                        void runAction(
                          () => controlPlaneApi.createHandoverAcceptanceV2({
                            snapshot_public_id: latestSnapshotV2.public_id,
                            decision: "ACCEPTED",
                            comment: acceptanceComment,
                            acknowledges_failures: acknowledgeFailures,
                            idempotency_key: `accept-${latestSnapshotV2.public_id}`,
                          }),
                          "Handover 2.0 验收失败",
                        )
                      }
                      icon={<ShieldCheck className="h-4 w-4" />}
                    >
                      验收精确快照
                    </Button>
                  </>
                )}
              </div>

              <div className="space-y-3">
                <div className="text-sm font-semibold text-slate-900">Detached Ed25519 signature</div>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy || !acceptedV2}
                  onClick={() =>
                    acceptedV2
                      ? void runAction(async () => {
                          const response = await controlPlaneApi.getHandoverSigningPayloadV2(acceptedV2.public_id);
                          setSigningPayload(response.data);
                        }, "签名载荷获取失败")
                      : undefined
                  }
                  icon={<KeyRound className="h-4 w-4" />}
                >
                  获取签名载荷
                </Button>
                {signingPayload ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="break-all font-mono text-xs text-slate-700">manifest {signingPayload.manifest_digest}</div>
                    <textarea readOnly value={signingPayload.payload_base64} rows={3} className={`${TEXTAREA_CLASS} mt-2 font-mono text-xs`} />
                  </div>
                ) : null}
                <Input value={signingKeyPublicId} onChange={(event) => setSigningKeyPublicId(event.target.value)} placeholder="Package signing key public ID" />
                <textarea value={detachedSignature} onChange={(event) => setDetachedSignature(event.target.value)} rows={3} className={`${TEXTAREA_CLASS} font-mono text-xs`} placeholder="Canonical base64 Ed25519 signature" />
                <Button
                  disabled={busy || !acceptedV2 || !signingPayload || !signingKeyPublicId.trim() || !detachedSignature.trim()}
                  onClick={() =>
                    acceptedV2
                      ? void runAction(
                          () => controlPlaneApi.createHandoverSignedPackageV2({
                            acceptance_public_id: acceptedV2.public_id,
                            signing_key_public_id: signingKeyPublicId.trim(),
                            signature: detachedSignature.trim(),
                            idempotency_key: `signed-package-${acceptedV2.public_id}`,
                          }),
                          "签名交接包创建失败",
                        )
                      : undefined
                  }
                  icon={<FileArchive className="h-4 w-4" />}
                >
                  验签并构建包
                </Button>
              </div>
            </div>

            {packagesV2.length ? (
              <div className="border-t border-slate-200 pt-4">
                <div className="mb-2 text-sm font-semibold text-slate-900">Signed packages</div>
                <div className="space-y-2">
                  {packagesV2.map((item) => (
                    <div key={item.public_id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-3">
                      <div className="min-w-0">
                        <div className="font-mono text-xs text-slate-700">{item.public_id}</div>
                        <div className="mt-1 truncate text-xs text-slate-500">{item.archive_digest} · {item.signing_key_fingerprint}</div>
                      </div>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={busy}
                        onClick={() => void handleEvidenceDownload(item.evidence_item_id)}
                        icon={<Download className="h-4 w-4" />}
                      >
                        下载
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </Card>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-6">
          <Card title="交接条目" description={`${items.length} items`}>
            <div className="divide-y divide-slate-200">
              {items.map((item) => {
                const asset = assetById.get(item.asset_id);
                return (
                  <div key={item.id} className="px-5 py-4 sm:px-6">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-slate-900">{asset?.name ?? `asset #${item.asset_id}`}</div>
                        <div className="mt-1 flex flex-wrap gap-2">
                          <MonoPill>{item.recommended_action}</MonoPill>
                          {asset ? <Badge tone={criticalityTone(asset.criticality)}>{asset.criticality}</Badge> : null}
                        </div>
                      </div>
                      <Badge tone={itemStatusTone(item.status)}>{item.status}</Badge>
                    </div>
                    {item.risk_reason ? <p className="mt-3 text-sm leading-6 text-slate-600">{item.risk_reason}</p> : null}
                  </div>
                );
              })}
              {!items.length ? <div className="px-5 py-8 text-sm text-slate-500">暂无交接条目</div> : null}
            </div>
          </Card>

          <Card title="执行动作" description={`${terminalActions}/${actions.length} completed`} action={
            <Button
              size="sm"
              disabled={busy || handover.status !== "approved"}
              onClick={() => void runAction(() => controlPlaneApi.executeHandover(handover.id, { execution_mode: "manual", idempotency_key: `exec-${handover.id}-${Date.now()}` }), "执行启动失败")}
              icon={<PlayCircle className="h-4 w-4" />}
            >
              执行
            </Button>
          }>
            <div className="divide-y divide-slate-200">
              {actions.map((action) => {
                const item = action.handover_item_id ? itemById.get(action.handover_item_id) : null;
                const asset = item ? assetById.get(item.asset_id) : null;
                const selectedEvidence = receiptEvidence[action.id] ?? [];
                const result = receiptResult[action.id] ?? "succeeded";
                const requiresEvidence = actionRequiresEvidence(action, item, asset);
                return (
                  <div key={action.id} className="px-5 py-5 sm:px-6">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-slate-900">{action.action_type}</div>
                        <div className="mt-1 flex flex-wrap gap-2">
                          <MonoPill>action #{action.id}</MonoPill>
                          <Badge tone={executionTone(action.status)}>{action.status}</Badge>
                          {requiresEvidence ? <Badge tone="amber">证据必选</Badge> : null}
                        </div>
                      </div>
                    </div>
                    {["succeeded", "failed"].includes(action.status) ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {action.evidence_ids.map((id) => <EvidencePill key={id} evidence={evidenceById.get(id)} id={id} />)}
                      </div>
                    ) : (
                      <div className="mt-4 grid gap-3 lg:grid-cols-[10rem_minmax(0,1fr)_minmax(0,1fr)_auto]">
                        <select
                          value={result}
                          onChange={(event) =>
                            setReceiptResult((current) => ({
                              ...current,
                              [action.id]: event.target.value as Extract<ExecutionStatus, "succeeded" | "failed">,
                            }))
                          }
                          className={SELECT_CLASS}
                        >
                          <option value="succeeded">succeeded</option>
                          <option value="failed">failed</option>
                        </select>
                        <Input
                          value={receiptNote[action.id] ?? ""}
                          onChange={(event) => setReceiptNote((current) => ({ ...current, [action.id]: event.target.value }))}
                          placeholder="回执说明"
                        />
                        <select
                          multiple
                          value={selectedEvidence.map(String)}
                          onChange={(event) =>
                            setReceiptEvidence((current) => ({
                              ...current,
                              [action.id]: Array.from(event.target.selectedOptions).map((option) => Number(option.value)),
                            }))
                          }
                          className={`${SELECT_CLASS} min-h-10`}
                        >
                          {caseEvidence.map((item) => <option key={item.id} value={item.id}>#{item.id} {item.summary}</option>)}
                        </select>
                        <Button
                          disabled={busy || (result === "succeeded" && requiresEvidence && selectedEvidence.length === 0)}
                          onClick={() =>
                            void runAction(
                              () =>
                                controlPlaneApi.completeExecutionAction(handover.id, action.id, {
                                  result,
                                  note: receiptNote[action.id] || "manual receipt",
                                  evidence_ids: selectedEvidence,
                                  idempotency_key: `receipt-${action.id}`,
                                }),
                              "回执提交失败"
                            )
                          }
                        >
                          回执
                        </Button>
                        <div className="lg:col-span-4 grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                          <Input
                            type="file"
                            onChange={(event) =>
                              setUploadFile((current) => ({
                                ...current,
                                [action.id]: event.target.files?.[0] ?? null,
                              }))
                            }
                          />
                          <Input
                            value={uploadSummary[action.id] ?? ""}
                            onChange={(event) => setUploadSummary((current) => ({ ...current, [action.id]: event.target.value }))}
                            placeholder="执行证据说明"
                          />
                          <Button
                            variant="secondary"
                            disabled={busy || !uploadFile[action.id] || handover.status !== "executing"}
                            onClick={() => void handleEvidenceUpload(action.id)}
                            icon={<Upload className="h-4 w-4" />}
                          >
                            上传证据
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
              {!actions.length ? <div className="px-5 py-8 text-sm text-slate-500">暂无执行动作</div> : null}
            </div>
          </Card>
        </div>

        <aside className="space-y-6">
          <Card title="审批" padded>
            <div className="space-y-3">
              {approvals.map((approval) => (
                <div key={approval.id} className="rounded-lg border border-slate-200 px-3 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <MonoPill>{approval.approval_type}</MonoPill>
                    <Badge tone={approvalTone(approval.status)}>{approval.status}</Badge>
                  </div>
                  <div className="mt-2 text-xs text-slate-500">approver #{approval.approver_user_id}</div>
                  {approval.status === "pending" ? (
                    <div className="mt-3 flex gap-2">
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => void runAction(() => controlPlaneApi.decideApproval(handover.id, approval.id, { decision: "approved", comment: approvalComment || null }), "审批失败")} icon={<CheckCircle2 className="h-4 w-4" />}>
                        批准
                      </Button>
                      <Button size="sm" variant="secondary" danger disabled={busy} onClick={() => void runAction(() => controlPlaneApi.decideApproval(handover.id, approval.id, { decision: "rejected", comment: approvalComment || null }), "审批失败")} icon={<XCircle className="h-4 w-4" />}>
                        拒绝
                      </Button>
                    </div>
                  ) : null}
                </div>
              ))}
              <textarea value={approvalComment} onChange={(event) => setApprovalComment(event.target.value)} rows={2} className={TEXTAREA_CLASS} placeholder="审批备注" />
              <div className="grid gap-2">
                <select value={approvalType} onChange={(event) => setApprovalType(event.target.value as ApprovalType)} className={SELECT_CLASS}>
                  {approvalTypes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
                <select value={approvalUserId} onChange={(event) => setApprovalUserId(event.target.value)} className={SELECT_CLASS}>
                  <option value="">选择审批人</option>
                  {people.map((person) => <option key={person.id} value={person.id}>{person.username}</option>)}
                </select>
                <Button
                  variant="secondary"
                  disabled={busy || !approvalUserId}
                  onClick={() => void runAction(() => controlPlaneApi.submitHandover(handover.id, [{ approval_type: approvalType, approver_user_id: Number(approvalUserId) }]), "提交审批失败")}
                >
                  提交审批
                </Button>
              </div>
            </div>
          </Card>

          <Card title="证据" padded>
            <div className="space-y-2">
              {caseEvidence.map((item) => (
                <EvidenceRow
                  key={item.id}
                  evidence={item}
                  disabled={busy}
                  onDownload={() => void handleEvidenceDownload(item.id)}
                />
              ))}
              {!caseEvidence.length ? <div className="text-sm text-slate-500">暂无关联证据</div> : null}
            </div>
          </Card>

          <Card title="封装包" padded>
            <div className="space-y-3">
              <Input value={downloadReason} onChange={(event) => setDownloadReason(event.target.value)} />
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  disabled={busy || !packageEnabled}
                  onClick={() => void runAction(() => controlPlaneApi.createHandoverPackage(handover.id), "构建封装包失败")}
                  icon={<FileArchive className="h-4 w-4" />}
                >
                  构建
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy || !packageEnabled}
                  onClick={() =>
                    void runAction(async () => {
                      const { data } = await controlPlaneApi.downloadHandoverPackageLink(handover.id, { reason: downloadReason });
                      window.open(data.download_url, "_blank", "noopener,noreferrer");
                    }, "下载链接签发失败")
                  }
                  icon={<Download className="h-4 w-4" />}
                >
                  下载
                </Button>
              </div>
            </div>
          </Card>

          <Card title="验收" padded>
            <div className="space-y-3">
              <textarea value={verifyNote} onChange={(event) => setVerifyNote(event.target.value)} rows={3} className={TEXTAREA_CLASS} placeholder="验收备注" />
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <input type="checkbox" checked={acknowledgeFailures} onChange={(event) => setAcknowledgeFailures(event.target.checked)} />
                acknowledge failures
              </label>
              <Button
                disabled={busy || handover.status !== "verifying"}
                onClick={() => void runAction(() => controlPlaneApi.verifyHandover(handover.id, { note: verifyNote || null, acknowledge_failures: acknowledgeFailures }), "验收失败")}
                icon={<ShieldCheck className="h-4 w-4" />}
              >
                完成交接
              </Button>
            </div>
          </Card>
        </aside>
      </div>
    </div>
  );
}

function EvidencePill({ evidence, id }: { evidence: EvidenceItem | undefined; id: number }) {
  return <MonoPill>evidence #{id}{evidence ? ` ${evidence.visibility}` : ""}</MonoPill>;
}

function SnapshotMetric({ label, value, tone = "indigo" }: { label: string; value: string; tone?: Tone }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="table-label">{label}</div>
      <div className="mt-2"><Badge tone={tone}>{value}</Badge></div>
    </div>
  );
}

export function EvidenceRow({
  evidence,
  disabled,
  onDownload,
}: {
  evidence: EvidenceItem;
  disabled: boolean;
  onDownload: () => void;
}) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-3">
      <div className="flex items-center justify-between gap-2">
        <MonoPill>#{evidence.id}</MonoPill>
        <Badge tone={evidenceTone(evidence.visibility)}>{evidence.visibility}</Badge>
      </div>
      <div className="mt-2 text-sm leading-6 text-slate-700">{evidence.summary}</div>
      {evidence.object_uri ? (
        <Button
          className="mt-3"
          size="sm"
          variant="secondary"
          disabled={disabled}
          onClick={onDownload}
          icon={<Download className="h-4 w-4" />}
        >
          下载
        </Button>
      ) : (
        <div className="mt-3 text-xs text-slate-500">无可下载对象</div>
      )}
    </div>
  );
}

function handoverStatusTone(status: HandoverCase["status"]): Tone {
  if (status === "completed") return "emerald";
  if (status === "rejected" || status === "cancelled") return "rose";
  if (status === "executing" || status === "verifying") return "amber";
  return "indigo";
}

function itemStatusTone(status: HandoverItem["status"]): Tone {
  if (status === "done") return "emerald";
  if (status === "failed") return "rose";
  if (status === "skipped") return "neutral";
  return "amber";
}

function approvalTone(status: ApprovalStatus): Tone {
  if (status === "approved") return "emerald";
  if (status === "rejected") return "rose";
  return "amber";
}

function executionTone(status: ExecutionStatus): Tone {
  if (status === "succeeded") return "emerald";
  if (status === "failed") return "rose";
  if (status === "requires_manual") return "amber";
  return "indigo";
}

function criticalityTone(criticality: AIAsset["criticality"]): Tone {
  if (criticality === "critical" || criticality === "high") return "rose";
  if (criticality === "medium") return "amber";
  return "emerald";
}

function evidenceTone(visibility: EvidenceItem["visibility"]): Tone {
  if (visibility === "restricted") return "rose";
  if (visibility === "sensitive") return "amber";
  return "neutral";
}

function getErrorDetail(err: unknown) {
  if (!isRecord(err) || !isRecord(err.response) || !isRecord(err.response.data)) {
    return null;
  }
  const detail = err.response.data.detail;
  if (typeof detail === "string") return detail;
  if (isRecord(detail) && typeof detail.message === "string") return detail.message;
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
