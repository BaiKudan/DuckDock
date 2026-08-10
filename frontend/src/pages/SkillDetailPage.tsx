import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  CircleDashed,
  Copy,
  Download,
  FileCode2,
  GitCompareArrows,
  Globe2,
  LoaderCircle,
  LockKeyhole,
  PlayCircle,
  Pencil,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  Trash2,
} from "lucide-react";
import { useNavigate, useParams } from "../router";
import {
  scansApi,
  skillsApi,
  type IssueSeverity,
  type ScanResult,
  type SandboxValidationRun,
  type SkillVersion,
  type SkillVersionDetail,
  type SkillVersionSharingState,
} from "../api/client";
import { useI18n } from "../i18n";
import { AiAssistBadge } from "../components/AiAssistBadge";
import { Badge, Button, Card, IconTile, Input, MonoPill, PageHeader, type Tone } from "../components/ui";

const VERSION_STATUS_BADGE: Record<string, Tone> = {
  production: "emerald",
  quarantine: "amber",
  scanning: "indigo",
  review: "amber",
  rejected: "rose",
};

const SCAN_STATUS_CONFIG = {
  pending: { labelKey: "skill.scan.pending", tone: "amber", Icon: CircleDashed },
  running: { labelKey: "skill.scan.running", tone: "indigo", Icon: LoaderCircle },
  passed: { labelKey: "skill.scan.passed", tone: "emerald", Icon: ShieldCheck },
  warned: { labelKey: "skill.scan.warned", tone: "amber", Icon: ShieldAlert },
  failed: { labelKey: "skill.scan.failed", tone: "rose", Icon: ShieldX },
} as const satisfies Record<string, { labelKey: string; tone: Tone; Icon: typeof CircleDashed }>;

const SANDBOX_STATUS_CONFIG = {
  pending: { label: "Pending", tone: "amber", Icon: CircleDashed },
  running: { label: "Running", tone: "indigo", Icon: LoaderCircle },
  passed: { label: "Passed", tone: "emerald", Icon: ShieldCheck },
  failed: { label: "Failed", tone: "rose", Icon: ShieldX },
  skipped: { label: "Skipped", tone: "neutral", Icon: CircleDashed },
} as const satisfies Record<string, { label: string; tone: Tone; Icon: typeof CircleDashed }>;

const SEVERITY_CONFIG: Record<IssueSeverity, { labelKey: string; tone: Tone; badge: string; dot: string }> = {
  critical: { labelKey: "skill.severity.critical", tone: "rose", badge: "border-rose-200 bg-rose-50 text-rose-700", dot: "bg-rose-600" },
  high: { labelKey: "skill.severity.high", tone: "orange", badge: "border-orange-200 bg-orange-50 text-orange-700", dot: "bg-orange-500" },
  medium: { labelKey: "skill.severity.medium", tone: "amber", badge: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  low: { labelKey: "skill.severity.low", tone: "indigo", badge: "border-indigo-200 bg-indigo-50 text-indigo-700", dot: "bg-indigo-500" },
  info: { labelKey: "skill.severity.info", tone: "neutral", badge: "border-slate-200 bg-slate-100 text-slate-700", dot: "bg-slate-400" },
};

type Tab = "files" | "diff" | "security";

function SkillManageModal({
  ns,
  skill,
  description,
  mode,
  onClose,
  onUpdated,
  onDeleted,
}: {
  ns: string;
  skill: string;
  description: string | null | undefined;
  mode: "edit" | "delete";
  onClose: () => void;
  onUpdated: (description: string | null) => void;
  onDeleted: () => void;
}) {
  const { t } = useI18n();
  const [nextDescription, setNextDescription] = useState(description ?? "");
  const [confirmName, setConfirmName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "edit") {
        const { data } = await skillsApi.update(ns, skill, {
          description: nextDescription.trim() || null,
        });
        onUpdated(data.description);
      } else {
        if (confirmName !== skill) {
          setError(t("skills.manage.confirmDelete"));
          return;
        }
        await skillsApi.delete(ns, skill);
        onDeleted();
      }
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      setError(
        Array.isArray(detail)
          ? detail[0]?.msg
          : detail ?? (mode === "edit" ? t("skills.manage.updateFailed") : t("skills.manage.deleteFailed"))
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/10 px-4 backdrop-blur-sm">
      <Card className="w-full max-w-lg" padded>
        <h2 className="text-xl font-semibold tracking-tight text-slate-900">
          {mode === "edit" ? t("skills.manage.updateTitle") : t("skills.manage.deleteTitle")}
        </h2>
        {error ? (
          <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
        ) : null}
        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">{t("field.name")}</label>
            <Input value={skill} disabled />
            <p className="mt-2 text-xs text-slate-500">{t("skills.manage.nameImmutable")}</p>
          </div>
          {mode === "edit" ? (
            <div>
              <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">{t("field.description")}</label>
              <Input value={nextDescription} onChange={(event) => setNextDescription(event.target.value)} />
            </div>
          ) : (
            <div>
              <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-700">{t("skills.manage.deleteMessage")}</p>
              <label className="mt-4 block">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">{t("skills.manage.confirmDelete")}</div>
                <Input value={confirmName} onChange={(event) => setConfirmName(event.target.value)} />
              </label>
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={loading} danger={mode === "delete"}>
              {loading ? t("common.loading") : mode === "edit" ? t("common.save") : t("common.delete")}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}

function ScanPanel({ ns, skill, version }: { ns: string; skill: string; version: SkillVersion }) {
  const { locale, t } = useI18n();
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [filterSeverity, setFilterSeverity] = useState<IssueSeverity | "all">("all");

  const fetchScan = useCallback(async () => {
    try {
      const { data } = await scansApi.get(ns, skill, version.tag);
      setScan(data);
      if (data.status === "pending" || data.status === "running") {
        setTimeout(fetchScan, 2500);
      }
    } catch {
      setScan(null);
    } finally {
      setLoading(false);
    }
  }, [ns, skill, version.tag]);

  useEffect(() => {
    setLoading(true);
    setScan(null);
    fetchScan();
  }, [fetchScan]);

  async function handleTrigger() {
    setTriggering(true);
    try {
      const { data } = await scansApi.trigger(ns, skill, version.tag);
      setScan(data);
      setTimeout(fetchScan, 2500);
    } finally {
      setTriggering(false);
    }
  }

  if (loading) {
    return <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-sm text-slate-500">{t("skill.scan.loading")}</div>;
  }

  const statusConfig = scan ? SCAN_STATUS_CONFIG[scan.status] : null;
  const StatusIcon = statusConfig?.Icon ?? ShieldAlert;
  const issues = scan?.issues ?? [];
  const filteredIssues = filterSeverity === "all" ? issues : issues.filter((issue) => issue.severity === filterSeverity);

  return (
    <div className="space-y-4">
      <Card padded>
        <div className="mb-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <IconTile size="lg">
              <StatusIcon className={`h-5 w-5 ${scan?.status === "running" ? "animate-spin" : ""}`} />
            </IconTile>
            <div>
              <Badge tone={statusConfig?.tone ?? "indigo"}>{statusConfig ? t(statusConfig.labelKey) : t("skill.scan.noScanYet")}</Badge>
              {scan?.completed_at ? (
                <div className="mt-1 text-xs text-slate-500">
                  {t("skill.scan.completedAt", { time: new Date(scan.completed_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US") })}
                </div>
              ) : null}
            </div>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleTrigger}
            disabled={triggering || scan?.status === "running" || scan?.status === "pending"}
            icon={<RefreshCw className="h-4 w-4" />}
          >
            {triggering ? t("skill.scan.triggering") : scan ? t("skill.scan.rescan") : t("skill.scan.trigger")}
          </Button>
        </div>

        {scan ? (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {(["critical", "high", "medium", "low"] as IssueSeverity[]).map((severity) => {
              const count = scan[`${severity}_count` as keyof ScanResult] as number;
              const config = SEVERITY_CONFIG[severity];
              return (
                <button
                  key={severity}
                  onClick={() => setFilterSeverity(filterSeverity === severity ? "all" : severity)}
                  className={`rounded-lg border p-3 text-left transition ${
                    filterSeverity === severity ? config.badge : "border-slate-200 bg-slate-50 hover:border-slate-300"
                  }`}
                >
                  <div className="flex items-center gap-2 text-xs">
                    <div className={`h-2 w-2 rounded-full ${config.dot}`} />
                    <span>{t(config.labelKey)}</span>
                  </div>
                  <div className="mt-2 text-2xl font-bold tracking-tight text-slate-900">{count}</div>
                </button>
              );
            })}
          </div>
        ) : null}
      </Card>

      {scan && issues.length > 0 ? (
        <Card
          title={t("skill.scan.issues", {
            count: filteredIssues.length,
            filter: filterSeverity !== "all" ? t("skill.scan.filterSuffix", { severity: t(SEVERITY_CONFIG[filterSeverity].labelKey) }) : "",
          })}
          action={
            filterSeverity !== "all" ? (
              <Button variant="link" onClick={() => setFilterSeverity("all")}>
                {t("skill.scan.clearFilter")}
              </Button>
            ) : null
          }
        >
          <div className="divide-y divide-slate-200">
            {filteredIssues.map((issue, index) => {
              const config = SEVERITY_CONFIG[issue.severity];
              return (
                <div key={index} className="px-5 py-4 transition-colors hover:bg-slate-50/70 sm:px-6">
                  <div className="flex items-start gap-3">
                    <div className={`mt-1 h-2 w-2 shrink-0 rounded-full ${config.dot}`} />
                    <div className="min-w-0 flex-1">
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <Badge tone={config.tone}>{t(config.labelKey)}</Badge>
                        <MonoPill>{issue.rule}</MonoPill>
                        {issue.file ? <span className="font-mono text-[11px] text-slate-500">{issue.file}{issue.line ? `:${issue.line}` : ""}</span> : null}
                      </div>
                      <p className="text-sm text-slate-700">{issue.message}</p>
                      {issue.snippet ? (
                        <pre className="mt-3 overflow-x-auto rounded-md border border-slate-200 bg-slate-50 px-3 py-3 font-mono text-xs leading-relaxed text-slate-700">
                          {issue.snippet}
                        </pre>
                      ) : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      ) : null}

      {scan && issues.length === 0 && scan.status === "passed" ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-8 text-center text-sm text-emerald-700">
          {t("skill.scan.noIssues")}
        </div>
      ) : null}

      {!scan ? (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
          <div>{t("skill.scan.none")}</div>
          <Button variant="secondary" className="mt-4" onClick={handleTrigger} icon={<PlayCircle className="h-4 w-4" />}>
            {t("skill.scan.run")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function SandboxPanel({ ns, skill, version }: { ns: string; skill: string; version: SkillVersion }) {
  const [run, setRun] = useState<SandboxValidationRun | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchRun = useCallback(async () => {
    try {
      const { data } = await scansApi.getSandbox(ns, skill, version.tag);
      setRun(data);
      if (data.status === "pending" || data.status === "running") {
        setTimeout(fetchRun, 2500);
      }
    } catch {
      setRun(null);
    } finally {
      setLoading(false);
    }
  }, [ns, skill, version.tag]);

  useEffect(() => {
    setLoading(true);
    setRun(null);
    fetchRun();
  }, [fetchRun]);

  if (loading) {
    return <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-sm text-slate-500">Loading sandbox validation...</div>;
  }

  if (!run) {
    return (
      <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
        No sandbox validation has been recorded for this version yet.
      </div>
    );
  }

  const config = SANDBOX_STATUS_CONFIG[run.status];
  const StatusIcon = config.Icon;

  return (
    <div className="space-y-4">
      <Card padded>
        <div className="mb-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <IconTile size="lg">
              <StatusIcon className={`h-5 w-5 ${run.status === "running" ? "animate-spin" : ""}`} />
            </IconTile>
            <div>
              <Badge tone={config.tone}>{config.label}</Badge>
              <div className="mt-1 text-xs text-slate-500">Engine: {run.engine}</div>
            </div>
          </div>
          <MonoPill>{run.status}</MonoPill>
        </div>
        {run.summary ? <div className="text-sm text-slate-700">{run.summary}</div> : null}
      </Card>

      {run.checks?.length ? (
        <Card title="Sandbox checks">
          <div className="divide-y divide-slate-200">
            {run.checks.map((check) => {
              const checkConfig = SANDBOX_STATUS_CONFIG[check.status];
              return (
                <div key={check.name} className="px-5 py-4 transition-colors hover:bg-slate-50/70 sm:px-6">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={checkConfig.tone}>{check.status}</Badge>
                    <span className="font-mono text-xs text-slate-700">{check.name}</span>
                  </div>
                  <div className="mt-2 text-sm text-slate-700">{check.summary}</div>
                  {check.details ? (
                    <pre className="mt-3 overflow-x-auto rounded-md border border-slate-200 bg-slate-50 px-3 py-3 font-mono text-xs leading-relaxed text-slate-700">
                      {JSON.stringify(check.details, null, 2)}
                    </pre>
                  ) : null}
                </div>
              );
            })}
          </div>
        </Card>
      ) : null}

      {run.logs?.length ? (
        <Card title="Sandbox logs">
          <pre className="max-h-[24rem] overflow-x-auto px-5 py-4 font-mono text-xs leading-relaxed text-slate-800 sm:px-6">
            {run.logs.join("\n\n")}
          </pre>
        </Card>
      ) : null}
    </div>
  );
}

export default function SkillDetailPage() {
  const { ns, skill } = useParams<{ ns: string; skill: string }>();
  const navigate = useNavigate();
  const { locale, t } = useI18n();

  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [selected, setSelected] = useState<SkillVersionDetail | null>(null);
  const [tab, setTab] = useState<Tab>("files");
  const [diffFrom, setDiffFrom] = useState("");
  const [diffTo, setDiffTo] = useState("");
  const [diff, setDiff] = useState<string | null>(null);
  const [loadingVersions, setLoadingVersions] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [loadingDiff, setLoadingDiff] = useState(false);
  const [sharing, setSharing] = useState<SkillVersionSharingState | null>(null);
  const [loadingSharing, setLoadingSharing] = useState(false);
  const [updatingSharing, setUpdatingSharing] = useState(false);
  const [updatingReview, setUpdatingReview] = useState(false);
  const [updatingSharingApproval, setUpdatingSharingApproval] = useState(false);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const [manageMode, setManageMode] = useState<"edit" | "delete" | null>(null);
  const [skillDescription, setSkillDescription] = useState<string | null>(null);

  const loadVersion = useCallback(async (tag: string) => {
    if (!ns || !skill) return;
    setLoadingDetail(true);
    setLoadingSharing(true);
    setDiff(null);
    try {
      const [{ data: detail }, { data: sharingState }] = await Promise.all([
        skillsApi.getVersion(ns, skill, tag),
        skillsApi.getSharing(ns, skill, tag),
      ]);
      setSelected(detail);
      setSharing(sharingState);
      setTab("files");
    } finally {
      setLoadingDetail(false);
      setLoadingSharing(false);
    }
  }, [ns, skill]);

  useEffect(() => {
    if (!ns || !skill) return;
    skillsApi.listVersions(ns, skill).then(({ data }) => {
      setVersions(data);
      setLoadingVersions(false);
      if (data.length > 0) void loadVersion(data[0].tag);
    });
    skillsApi.get(ns, skill).then(({ data }) => {
      setSkillDescription(data.description);
    });
  }, [loadVersion, ns, skill]);

  function applySharingState(next: SkillVersionSharingState) {
    setSharing(next);
    setVersions((current) =>
      current.map((version) =>
        version.tag === next.tag
          ? {
              ...version,
              is_public_shared: next.is_public_shared,
              public_shared_at: next.public_shared_at,
            }
          : version
      )
    );
    setSelected((current) =>
      current && current.tag === next.tag
        ? {
            ...current,
            is_public_shared: next.is_public_shared,
            public_shared_at: next.public_shared_at,
          }
        : current
    );
  }

  async function handleToggleSharing() {
    if (!ns || !skill || !selected || !sharing) return;
    setUpdatingSharing(true);
    setCopyMessage(null);
    try {
      const { data } = await skillsApi.updateSharing(ns, skill, selected.tag, {
        is_public_shared: !sharing.is_public_shared,
        license_name: sharing.license_name ?? undefined,
        license_attested: sharing.license_attested,
        risk_acknowledged: sharing.risk_acknowledged,
        public_expires_in_days: sharing.expires_at ? Math.max(1, Math.ceil((new Date(sharing.expires_at).getTime() - Date.now()) / 86400000)) : undefined,
      });
      applySharingState(data);
    } finally {
      setUpdatingSharing(false);
    }
  }

  async function handleCopy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopyMessage(t("skill.copy.copied", { label }));
      setTimeout(() => setCopyMessage(null), 1800);
    } catch {
      setCopyMessage(t("skill.copy.failed", { label }));
      setTimeout(() => setCopyMessage(null), 1800);
    }
  }

  async function loadDiff() {
    if (!ns || !skill || !diffFrom || !diffTo) return;
    setLoadingDiff(true);
    setDiff(null);
    try {
      const { data } = await skillsApi.diff(ns, skill, diffFrom, diffTo);
      setDiff(data.diff);
    } finally {
      setLoadingDiff(false);
    }
  }

  async function handleReviewDecision(decision: "approve" | "reject" | "reset") {
    if (!ns || !skill || !selected) return;
    setUpdatingReview(true);
    try {
      const { data } = await skillsApi.updateReview(ns, skill, selected.tag, { decision });
      setVersions((current) => current.map((item) => (item.id === data.id ? data : item)));
      const detail = await skillsApi.getVersion(ns, skill, selected.tag);
      setSelected(detail.data);
    } finally {
      setUpdatingReview(false);
    }
  }

  async function handleSharingApproval(approval_status: "approved" | "rejected") {
    if (!ns || !skill || !selected || !sharing) return;
    setUpdatingSharingApproval(true);
    try {
      const { data } = await skillsApi.updateSharingApproval(ns, skill, selected.tag, { approval_status });
      applySharingState(data);
    } finally {
      setUpdatingSharingApproval(false);
    }
  }

  const selectedVersion = versions.find((version) => version.tag === selected?.tag);
  const metadataLabels: Record<string, string> = {
    name: t("skill.metadata.name"),
    version: t("skill.metadata.version"),
    description: t("skill.metadata.description"),
    author: t("skill.metadata.author"),
    tags: t("skill.metadata.tags"),
  };
  const statusLabels: Record<string, string> = {
    production: t("skill.status.production"),
    quarantine: t("skill.status.quarantine"),
    scanning: t("skill.status.scanning"),
    review: "待审核",
    rejected: t("skill.status.rejected"),
  };
  const gateClinic = selected?.gate_result?.clinic ?? null;

  return (
    <div className="app-page max-w-7xl">
      {manageMode && ns && skill ? (
        <SkillManageModal
          ns={ns}
          skill={skill}
          description={skillDescription}
          mode={manageMode}
          onClose={() => setManageMode(null)}
          onUpdated={(description) => {
            setSkillDescription(description);
            setManageMode(null);
          }}
          onDeleted={() => {
            setManageMode(null);
            navigate(`/namespaces/${ns}`);
          }}
        />
      ) : null}

      <Button variant="link" className="px-0" onClick={() => navigate(`/namespaces/${ns}`)} icon={<ArrowLeft className="h-4 w-4" />}>
        {t("skill.backToNamespace")}
      </Button>

      <div className="mt-4">
        <PageHeader
          eyebrow={t("skill.badge")}
          title={skill ?? ""}
          description={
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <MonoPill>{skill}</MonoPill>
              {ns ? <MonoPill>{ns}</MonoPill> : null}
              <span className="text-sm text-slate-500">{t("skill.subtitle", { namespace: ns ?? "" })}</span>
            </div>
          }
          actions={
            <>
              <Button variant="secondary" onClick={() => setManageMode("edit")} icon={<Pencil className="h-4 w-4" />}>
                {t("skills.manage.edit")}
              </Button>
              <Button variant="secondary" danger onClick={() => setManageMode("delete")} icon={<Trash2 className="h-4 w-4" />}>
                {t("skills.manage.delete")}
              </Button>
              <Button variant="primary" onClick={() => navigate(`/namespaces/${ns}/${skill}/publish`)}>
                {t("skill.publishVersion")}
              </Button>
            </>
          }
        />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 xl:grid-cols-[18rem_minmax(0,1fr)]">
        <Card>
          <div className="border-b border-slate-200 px-4 py-3 text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
            {t("skill.versions")}
          </div>
          {loadingVersions ? (
            <div className="px-4 py-6 text-sm text-slate-500">{t("skill.loadingVersions")}</div>
          ) : versions.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-slate-500">{t("skill.noVersions")}</div>
          ) : (
            <div className="divide-y divide-slate-200">
              {versions.map((version) => (
                <button
                  key={version.id}
                  onClick={() => loadVersion(version.tag)}
                  className={`w-full px-4 py-4 text-left transition-colors hover:bg-slate-50/70 ${selected?.tag === version.tag ? "bg-indigo-50/70" : ""}`}
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className={`font-mono text-sm font-semibold ${selected?.tag === version.tag ? "text-indigo-700" : "text-slate-900"}`}>{version.tag}</span>
                    <div className="flex items-center gap-2">
                      {version.is_public_shared ? <Badge tone="indigo">{t("skill.visibility.public")}</Badge> : null}
                      <Badge tone={VERSION_STATUS_BADGE[version.status] ?? "indigo"}>{statusLabels[version.status] ?? version.status}</Badge>
                    </div>
                  </div>
                  <div className="font-mono text-xs text-slate-500">{version.commit_sha.slice(0, 8)}</div>
                  <div className="mt-1 text-xs text-slate-500">{new Date(version.created_at).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US")}</div>
                </button>
              ))}
            </div>
          )}
        </Card>

        <div>
          {selected ? (
            <Card className="mb-6" padded>
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div className="flex items-start gap-3">
                  <IconTile size="lg" tone={sharing?.is_public_shared ? "indigo" : "neutral"}>
                    {sharing?.is_public_shared ? <Globe2 className="h-5 w-5" /> : <LockKeyhole className="h-5 w-5" />}
                  </IconTile>
                  <div>
                    <div className="text-sm font-semibold tracking-tight text-slate-900">{t("skill.distribution.title")}</div>
                    <div className="mt-1 text-sm text-slate-600">
                      {loadingSharing
                        ? t("skill.distribution.loading")
                        : sharing?.is_public_shared
                          ? sharing.public_ready
                            ? t("skill.distribution.sharedReady")
                            : t("skill.distribution.sharedPending")
                          : t("skill.distribution.private")}
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Badge tone={sharing?.is_public_shared ? "indigo" : "neutral"}>{sharing?.is_public_shared ? t("skill.distribution.shared") : t("skill.distribution.privateBadge")}</Badge>
                      {sharing?.public_ready ? <Badge tone="emerald">{t("skill.distribution.anonymousEnabled")}</Badge> : null}
                      {!sharing?.public_ready && sharing?.is_public_shared ? <Badge tone="amber">{t("skill.distribution.waitingProduction")}</Badge> : null}
                      {selected.is_public_shared && selected.public_shared_at ? (
                        <MonoPill>{t("skill.distribution.sharedAt", { time: new Date(selected.public_shared_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US") })}</MonoPill>
                      ) : null}
                    </div>
                  </div>
                </div>
                <Button
                  variant="secondary"
                  className="min-w-[12rem]"
                  onClick={handleToggleSharing}
                  disabled={loadingSharing || updatingSharing}
                >
                  {updatingSharing
                    ? t("skill.distribution.updating")
                    : sharing?.is_public_shared
                      ? t("skill.distribution.disable")
                      : t("skill.distribution.enable")}
                </Button>
              </div>

              {sharing?.is_public_shared ? (
                <div className="mt-5 grid gap-3 xl:grid-cols-3">
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{t("skill.distribution.slug")}</div>
                    <div className="mt-2 font-mono text-sm text-slate-800">{sharing.public_slug}</div>
                    <Button variant="link" className="mt-3 text-xs" onClick={() => handleCopy(sharing.public_slug, t("skill.distribution.slug"))} icon={<Copy className="h-3.5 w-3.5" />}>
                      {t("skill.distribution.copySlug")}
                    </Button>
                  </div>
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 xl:col-span-2">
                    <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{t("skill.distribution.downloadEndpoint")}</div>
                    <div className="mt-2 break-all font-mono text-xs leading-relaxed text-slate-700">{sharing.public_download_url}</div>
                    <div className="mt-3 flex flex-wrap items-center gap-3">
                      <Button variant="link" className="text-xs" onClick={() => handleCopy(sharing.public_download_url, t("skill.distribution.downloadEndpoint"))} icon={<Copy className="h-3.5 w-3.5" />}>
                        {t("skill.distribution.copyUrl")}
                      </Button>
                      <a href={sharing.public_inspect_url} target="_blank" rel="noreferrer" className="text-xs font-medium text-indigo-600 hover:text-indigo-700">
                        {t("skill.distribution.openInspect")}
                      </a>
                    </div>
                  </div>
                </div>
              ) : null}

              {copyMessage ? <div className="mt-4 text-xs text-slate-500">{copyMessage}</div> : null}
            </Card>
          ) : null}

          {selected?.gate_result ? (
            <Card className="mb-6" padded>
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <div className="text-sm font-semibold tracking-tight text-slate-900">Release gate</div>
                  <div className="mt-1 text-sm text-slate-600">
                    final_status: <span className="font-mono text-slate-800">{String(selected.gate_result.final_status ?? selected.status)}</span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <MonoPill>review:{selected.review_status}</MonoPill>
                    <MonoPill>issues:{Number(selected.gate_result.issue_count ?? 0)}</MonoPill>
                    {gateClinic ? (
                      <>
                        <MonoPill>clinic:{String(gateClinic.score ?? "n/a")}</MonoPill>
                        <AiAssistBadge aiAssist={gateClinic.ai_assist} />
                      </>
                    ) : null}
                  </div>
                </div>
                {selected.review_required ? (
                  <div className="flex flex-wrap gap-2">
                    <Button variant="secondary" onClick={() => handleReviewDecision("approve")} disabled={updatingReview}>
                      {updatingReview ? "处理中..." : "批准版本"}
                    </Button>
                    <Button variant="secondary" danger onClick={() => handleReviewDecision("reject")} disabled={updatingReview}>
                      拒绝版本
                    </Button>
                    <Button variant="secondary" onClick={() => handleReviewDecision("reset")} disabled={updatingReview}>
                      重置审核
                    </Button>
                  </div>
                ) : null}
              </div>
              {Array.isArray(selected.gate_result.issues) && selected.gate_result.issues.length ? (
                <div className="mt-4 space-y-2">
                  {(selected.gate_result.issues as Array<{ code?: string; severity?: string; message?: string }>).map((issue, index) => (
                    <div key={`${issue.code}-${index}`} className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
                      <div className="font-mono text-xs text-slate-500">{issue.severity ?? "info"} / {issue.code ?? "issue"}</div>
                      <div className="mt-1 text-slate-700">{issue.message ?? "Unknown gate issue"}</div>
                    </div>
                  ))}
                </div>
              ) : null}
            </Card>
          ) : null}

          {sharing?.is_public_shared && sharing.requires_approval ? (
            <Card className="mb-6" padded>
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <div className="text-sm font-semibold tracking-tight text-slate-900">Public sharing approval</div>
                  <div className="mt-1 text-sm text-slate-600">
                    status: <span className="font-mono text-slate-800">{sharing.approval_status ?? "pending"}</span>
                  </div>
                  <div className="mt-2 text-xs text-slate-500">
                    license: {sharing.license_name ?? "n/a"} | attested: {sharing.license_attested ? "yes" : "no"} | risk acknowledged: {sharing.risk_acknowledged ? "yes" : "no"}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={() => handleSharingApproval("approved")} disabled={updatingSharingApproval}>
                    {updatingSharingApproval ? "处理中..." : "批准共享"}
                  </Button>
                  <Button variant="secondary" danger onClick={() => handleSharingApproval("rejected")} disabled={updatingSharingApproval}>
                    拒绝共享
                  </Button>
                </div>
              </div>
            </Card>
          ) : null}

          <div className="mb-4 flex flex-wrap gap-2">
            {(["files", "security", "diff"] as Tab[]).map((entry) => (
              <button
                key={entry}
                onClick={() => setTab(entry)}
                className={`rounded-md px-4 py-2 text-sm font-medium transition ${
                  tab === entry ? "border border-indigo-200 bg-indigo-50 text-indigo-700" : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                {entry === "files" ? t("skill.tab.files") : entry === "security" ? t("skill.tab.security") : t("skill.tab.diff")}
              </button>
            ))}
          </div>

          {tab === "files" ? (
            loadingDetail ? (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-sm text-slate-500">{t("skill.loadingDetails")}</div>
            ) : !selected ? (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-sm text-slate-500">{t("skill.selectVersionFiles")}</div>
            ) : (
              <div className="space-y-4">
                {selected.skill_metadata ? (
                  <Card padded>
                    <div className="mb-3 text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t("skill.metadata")}</div>
                    <div className="grid gap-3 md:grid-cols-2">
                      {Object.entries(selected.skill_metadata).map(([key, value]) => (
                        <div key={key} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-sm">
                          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{metadataLabels[key] ?? key}</div>
                          <div className="mt-1 text-slate-800">{String(value)}</div>
                        </div>
                      ))}
                    </div>
                  </Card>
                ) : null}

                {Object.entries(selected.files).map(([path, content]) => (
                  <Card key={path}>
                    <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-3">
                      <div className="inline-flex items-center gap-2 font-mono text-xs text-slate-700">
                        <FileCode2 className="h-4 w-4 text-slate-400" />
                        {path}
                      </div>
                      <a href={skillsApi.downloadUrl(ns!, skill!, selected.tag)} className="inline-flex items-center gap-1.5 text-xs font-medium text-indigo-600 hover:text-indigo-700">
                        <Download className="h-3.5 w-3.5" />
                        {t("skill.downloadTar")}
                      </a>
                    </div>
                    <pre className="max-h-96 overflow-x-auto px-4 py-4 font-mono text-xs leading-relaxed text-slate-800">{content}</pre>
                  </Card>
                ))}
              </div>
            )
          ) : null}

          {tab === "security" ? (
            selectedVersion ? (
              <div className="space-y-4">
                <ScanPanel ns={ns!} skill={skill!} version={selectedVersion} />
                <SandboxPanel ns={ns!} skill={skill!} version={selectedVersion} />
              </div>
            ) : (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-8 text-sm text-slate-500">{t("skill.selectVersionScan")}</div>
            )
          ) : null}

          {tab === "diff" ? (
            <div className="space-y-4">
              <Card padded>
                <div className="mb-4 flex items-center gap-2 text-slate-900">
                  <GitCompareArrows className="h-4 w-4 text-indigo-600" />
                  <span className="text-sm font-semibold tracking-tight">{t("skill.compare.title")}</span>
                </div>
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
                  <select
                    value={diffFrom}
                    onChange={(event) => setDiffFrom(event.target.value)}
                    className="flex-1 rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-900 shadow-sm outline-none transition-colors focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100"
                  >
                    <option value="">{t("skill.compare.from")}</option>
                    {versions.map((version) => (
                      <option key={version.tag} value={version.tag}>
                        {version.tag}
                      </option>
                    ))}
                  </select>
                  <span className="text-slate-300">-&gt;</span>
                  <select
                    value={diffTo}
                    onChange={(event) => setDiffTo(event.target.value)}
                    className="flex-1 rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-900 shadow-sm outline-none transition-colors focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100"
                  >
                    <option value="">{t("skill.compare.to")}</option>
                    {versions.map((version) => (
                      <option key={version.tag} value={version.tag}>
                        {version.tag}
                      </option>
                    ))}
                  </select>
                  <Button variant="primary" className="whitespace-nowrap" onClick={loadDiff} disabled={!diffFrom || !diffTo || loadingDiff}>
                    {loadingDiff ? t("skill.compare.loading") : t("skill.compare.button")}
                  </Button>
                </div>
              </Card>

              {diff !== null ? (
                <Card>
                  <div className="border-b border-slate-200 bg-slate-50 px-4 py-3 font-mono text-xs text-slate-700">
                    {diffFrom} -&gt; {diffTo}
                  </div>
                  {diff ? (
                    <pre className="max-h-[32rem] overflow-x-auto px-4 py-4 font-mono text-xs leading-relaxed">
                      {diff.split("\n").map((line, index) => (
                        <span
                          key={index}
                          className={`block ${
                            line.startsWith("+") && !line.startsWith("+++") ? "bg-emerald-50 text-emerald-700"
                              : line.startsWith("-") && !line.startsWith("---") ? "bg-rose-50 text-rose-700"
                                : line.startsWith("@@") ? "text-indigo-700"
                                  : "text-slate-600"
                          }`}
                        >
                          {line}
                        </span>
                      ))}
                    </pre>
                  ) : (
                    <div className="px-4 py-8 text-center text-sm text-slate-500">{t("skill.compare.empty")}</div>
                  )}
                </Card>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
