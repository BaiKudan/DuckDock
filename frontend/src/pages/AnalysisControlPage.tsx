import { App as AntApp, Modal } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit,
  Clock3,
  Copy,
  Database,
  ExternalLink,
  FileJson,
  Info,
  KeyRound,
  RefreshCw,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  analysisApi,
  type AnalysisJobStatus,
  type AnalysisResultArtifact,
  type AnalysisResultArtifactKind,
  type AnalysisWorker,
  type AnalysisWorkerStatus,
  type MemoryCandidate,
  type MemoryCandidateStatus,
  type MemoryCandidateType,
  type ReportAnalysisJob,
} from "../api/client";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  MetricCard,
  MonoPill,
  PageHeader,
  type Tone,
} from "../components/ui";

const workerStatusLabels: Record<AnalysisWorkerStatus, string> = {
  active: "active",
  disabled: "disabled",
  stale: "stale",
};

const jobStatusLabels: Record<AnalysisJobStatus, string> = {
  pending: "pending",
  leased: "leased",
  running: "running",
  succeeded: "succeeded",
  failed: "failed",
  cancelled: "cancelled",
};

const artifactLabels: Record<AnalysisResultArtifactKind, string> = {
  analysis_result: "analysis-result.json",
  asset_cards: "asset-cards.json",
  worktrace_summary: "worktrace-summary.md",
  memory_candidates: "memory-candidates.json",
  handover_signals: "handover-signals.json",
  redaction_report: "redaction-report.json",
  other: "other",
};

const candidateTypeLabels: Record<MemoryCandidateType, string> = {
  asset_summary: "资产摘要",
  worktrace_summary: "工作历程",
  project_context: "项目上下文",
  ownership_signal: "归属信号",
  handover_signal: "交接信号",
  risk_signal: "风险信号",
  knowledge_note: "知识笔记",
};

const candidateStatusLabels: Record<MemoryCandidateStatus, string> = {
  candidate: "待审核",
  confirmed: "已确认",
  rejected: "已拒绝",
  superseded: "已过期",
};

const materializedCountKeys = [
  "asset_cards",
  "asset_ownerships",
  "runtime_bindings",
  "worktrace_summaries",
  "memory_candidates",
  "handover_signals",
  "risk_signals",
];

export default function AnalysisControlPage() {
  const { message, modal } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [workerModalOpen, setWorkerModalOpen] = useState(false);
  const [workerName, setWorkerName] = useState("DuckDock OpenClaw Worker");
  const [createdWorker, setCreatedWorker] = useState<(AnalysisWorker & { token: string }) | null>(null);
  const [artifactJob, setArtifactJob] = useState<ReportAnalysisJob | null>(null);

  const workersQuery = useQuery({
    queryKey: ["analysis", "workers"],
    queryFn: async () => (await analysisApi.listWorkers()).data,
  });
  const jobsQuery = useQuery({
    queryKey: ["analysis", "jobs"],
    queryFn: async () => (await analysisApi.listJobs()).data,
  });
  const queueMetricsQuery = useQuery({
    queryKey: ["analysis", "queue-metrics"],
    queryFn: async () => (await analysisApi.getQueueMetrics()).data,
  });
  const candidatesQuery = useQuery({
    queryKey: ["analysis", "memory-candidates"],
    queryFn: async () => (await analysisApi.listMemoryCandidates()).data,
  });
  const artifactsQuery = useQuery({
    queryKey: ["analysis", "job-artifacts", artifactJob?.id],
    queryFn: async () => (await analysisApi.listJobArtifacts(artifactJob?.id ?? 0)).data,
    enabled: artifactJob !== null,
  });

  async function openArtifactDownload(artifact: AnalysisResultArtifact) {
    try {
      const { data } = await analysisApi.getArtifactDownloadLink(artifact.id);
      window.open(data.download_url, "_blank", "noopener,noreferrer");
    } catch (error: unknown) {
      message.error(getErrorDetail(error) ?? "生成下载链接失败");
    }
  }

  const createWorkerMutation = useMutation({
    mutationFn: async () =>
      (
        await analysisApi.createWorker({
          name: workerName.trim() || "DuckDock OpenClaw Worker",
          capabilities_json: {
            result_artifacts: true,
            protocol: "duckdock-analysis-worker-v1",
          },
        })
      ).data,
    onSuccess: async (worker) => {
      setCreatedWorker(worker);
      message.success("Worker token 已创建");
      await queryClient.invalidateQueries({ queryKey: ["analysis", "workers"] });
    },
    onError: (error: unknown) => {
      message.error(getErrorDetail(error) ?? "创建 Worker 失败");
    },
  });

  const disableWorkerMutation = useMutation({
    mutationFn: async (workerId: number) => (await analysisApi.disableWorker(workerId)).data,
    onSuccess: async (result) => {
      message.success(`Worker 已禁用，${result.requeued_job_count} 个任务已重新入队`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["analysis", "workers"] }),
        queryClient.invalidateQueries({ queryKey: ["analysis", "jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["analysis", "queue-metrics"] }),
      ]);
    },
    onError: (error: unknown) => {
      message.error(getErrorDetail(error) ?? "禁用 Worker 失败");
    },
  });

  const cancelJobMutation = useMutation({
    mutationFn: async (jobId: number) => (await analysisApi.cancelJob(jobId)).data,
    onSuccess: async () => {
      message.success("分析任务已取消");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["analysis", "jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["analysis", "queue-metrics"] }),
      ]);
    },
    onError: (error: unknown) => {
      message.error(getErrorDetail(error) ?? "取消分析任务失败");
    },
  });

  const retryJobMutation = useMutation({
    mutationFn: async (jobId: number) => (await analysisApi.retryJob(jobId)).data,
    onSuccess: async () => {
      message.success("分析任务已重新入队");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["analysis", "jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["analysis", "queue-metrics"] }),
      ]);
    },
    onError: (error: unknown) => {
      message.error(getErrorDetail(error) ?? "重试分析任务失败");
    },
  });

  const reviewCandidateMutation = useMutation({
    mutationFn: async ({
      candidateId,
      status,
    }: {
      candidateId: number;
      status: Exclude<MemoryCandidateStatus, "candidate">;
    }) => (await analysisApi.reviewMemoryCandidate(candidateId, status)).data,
    onSuccess: async () => {
      message.success("记忆候选状态已更新");
      await queryClient.invalidateQueries({ queryKey: ["analysis", "memory-candidates"] });
    },
    onError: (error: unknown) => {
      message.error(getErrorDetail(error) ?? "更新记忆候选失败");
    },
  });

  const workers = useMemo(() => workersQuery.data ?? [], [workersQuery.data]);
  const jobs = useMemo(() => jobsQuery.data ?? [], [jobsQuery.data]);
  const candidates = useMemo(() => candidatesQuery.data ?? [], [candidatesQuery.data]);
  const loading = workersQuery.isLoading || jobsQuery.isLoading || queueMetricsQuery.isLoading || candidatesQuery.isLoading;

  const metrics = useMemo(() => {
    const queueMetrics = queueMetricsQuery.data;
    const activeWorkers = queueMetrics?.online_worker_count ?? workers.filter((worker) => worker.status === "active").length;
    const registeredWorkers = queueMetrics?.registered_worker_count ?? workers.length;
    const staleWorkers = queueMetrics?.stale_worker_count ?? workers.filter((worker) => worker.status === "stale").length;
    const disabledWorkers =
      queueMetrics?.disabled_worker_count ?? workers.filter((worker) => worker.status === "disabled").length;
    const backlogJobs =
      queueMetrics?.backlog_count ?? jobs.filter((job) => ["pending", "leased", "running"].includes(job.status)).length;
    const activeJobs = queueMetrics?.active_job_count ?? jobs.filter((job) => ["leased", "running"].includes(job.status)).length;
    const expiredLeases = queueMetrics?.expired_lease_count ?? 0;
    const pressure = queueMetrics?.queued_per_online_worker ?? 0;
    const oldestPendingSeconds = queueMetrics?.oldest_pending_seconds ?? null;
    const saturationLevel = queueMetrics?.saturation_level ?? "healthy";
    const saturationReason = queueMetrics?.saturation_reason ?? null;
    const candidateMemories = candidates.filter((candidate) => candidate.status === "candidate").length;
    return {
      activeWorkers,
      registeredWorkers,
      staleWorkers,
      disabledWorkers,
      backlogJobs,
      activeJobs,
      expiredLeases,
      pressure,
      oldestPendingSeconds,
      saturationLevel,
      saturationReason,
      candidateMemories,
    };
  }, [workers, jobs, candidates, queueMetricsQuery.data]);

  async function refreshAll() {
    await Promise.all([workersQuery.refetch(), jobsQuery.refetch(), queueMetricsQuery.refetch(), candidatesQuery.refetch()]);
    message.success("分析控制台已刷新");
  }

  function openCreateWorker() {
    setCreatedWorker(null);
    setWorkerName("DuckDock OpenClaw Worker");
    setWorkerModalOpen(true);
  }

  function confirmDisableWorker(worker: AnalysisWorker) {
    modal.confirm({
      title: `禁用 Worker：${worker.name}`,
      content: "禁用后该 token 将不能继续领取或提交任务；它正在处理的任务会释放回队列或标记失败。",
      okText: "禁用",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => disableWorkerMutation.mutateAsync(worker.id),
    });
  }

  function confirmCancelJob(job: ReportAnalysisJob) {
    modal.confirm({
      title: `取消分析任务 #${job.id}`,
      content: "取消后该任务会进入终态；如需重新处理，可以再执行重试。",
      okText: "取消任务",
      okButtonProps: { danger: true },
      cancelText: "返回",
      onOk: () => cancelJobMutation.mutateAsync(job.id),
    });
  }

  function confirmRetryJob(job: ReportAnalysisJob) {
    modal.confirm({
      title: `重试分析任务 #${job.id}`,
      content: "任务会清空租约并重新入队，下一次由可用 Worker 领取处理。",
      okText: "重试",
      cancelText: "返回",
      onOk: () => retryJobMutation.mutateAsync(job.id),
    });
  }

  return (
    <div className="app-page max-w-7xl space-y-6">
      <PageHeader
        eyebrow="ANALYSIS CONTROL"
        title="分析控制台"
        description="管理专用 OpenClaw Worker、异步分析任务、结果文件和长期记忆候选。原始上报包与完整分析结果留在 MinIO，MySQL 只保留可审核的索引和结论。"
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => void refreshAll()}
              disabled={loading}
              icon={<RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />}
            >
              刷新
            </Button>
            <Button variant="primary" onClick={openCreateWorker} icon={<KeyRound className="h-4 w-4" />}>
              新建 Worker
            </Button>
          </>
        }
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Worker"
          value={`${metrics.activeWorkers}/${metrics.registeredWorkers}`}
          detail={`${metrics.staleWorkers} stale / ${metrics.disabledWorkers} disabled`}
          icon={BrainCircuit}
          loading={loading}
          tone={metrics.activeWorkers === 0 && metrics.backlogJobs > 0 ? "rose" : "neutral"}
        />
        <MetricCard
          label="队列积压"
          value={String(metrics.backlogJobs)}
          detail={`active ${metrics.activeJobs} / expired ${metrics.expiredLeases}`}
          icon={Clock3}
          loading={loading}
          tone={metrics.saturationLevel === "saturated" ? "rose" : metrics.saturationLevel === "watch" ? "indigo" : "neutral"}
        />
        <MetricCard
          label="处理压力"
          value={metrics.pressure.toFixed(2)}
          detail={`${formatDuration(metrics.oldestPendingSeconds)} oldest / ${metrics.saturationReason ?? "healthy"}`}
          icon={FileJson}
          loading={loading}
          tone={metrics.saturationLevel === "saturated" ? "rose" : "neutral"}
        />
        <MetricCard
          label="记忆候选"
          value={String(metrics.candidateMemories)}
          detail="等待管理员确认"
          icon={Database}
          loading={loading}
        />
      </div>

      <div className="flex gap-3 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3.5 text-sm leading-6 text-indigo-700">
        <Info className="mt-0.5 h-[18px] w-[18px] shrink-0 text-indigo-600" />
        <div>
          这个页面只管理 DuckDock 自己的异步分析层。用户侧 Reporter 负责上传包，分析 Worker 从 DuckDock 领取一次性下载 URL，产出标准结果文件后再回写 MinIO。
        </div>
      </div>

      <Card title="Worker 池" description="一个或多个专用 OpenClaw 可以并行领取分析任务。Worker token 只在创建时显示一次。">
        <WorkerTable
          workers={workers}
          loading={workersQuery.isLoading}
          busyWorkerId={disableWorkerMutation.variables ?? null}
          onDisable={confirmDisableWorker}
        />
      </Card>

      <Card title="分析任务" description="每个任务对应一次用户侧 Reporter 上传包。任务成功后会索引结果文件，并把简化结论写入控制平面。">
        <JobTable
          jobs={jobs}
          loading={jobsQuery.isLoading}
          busyJobId={
            cancelJobMutation.variables !== undefined
              ? cancelJobMutation.variables
              : retryJobMutation.variables !== undefined
                ? retryJobMutation.variables
                : null
          }
          onOpenArtifacts={setArtifactJob}
          onCancel={confirmCancelJob}
          onRetry={confirmRetryJob}
        />
      </Card>

      <Card title="长期记忆候选" description="这里展示分析 Worker 归纳出的资产、项目、工作历程和交接信号。确认后才进入可长期使用的管理视图。">
        <MemoryCandidateTable
          candidates={candidates}
          loading={candidatesQuery.isLoading}
          busyId={
            reviewCandidateMutation.variables
              ? String(reviewCandidateMutation.variables.candidateId)
              : null
          }
          onReview={(candidateId, status) => reviewCandidateMutation.mutate({ candidateId, status })}
        />
      </Card>

      <Modal
        title="新建分析 Worker"
        open={workerModalOpen}
        okText={createdWorker ? "关闭" : "创建"}
        cancelText={createdWorker ? undefined : "取消"}
        confirmLoading={createWorkerMutation.isPending}
        onCancel={() => setWorkerModalOpen(false)}
        onOk={() => {
          if (createdWorker) {
            setWorkerModalOpen(false);
          } else {
            createWorkerMutation.mutate();
          }
        }}
      >
        {createdWorker ? (
          <div className="space-y-4">
            <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">
              Token 只显示一次。把它配置到专用 OpenClaw Worker 的启动参数或环境变量中。
            </div>
            <CopyBlock value={createdWorker.token} onCopied={() => message.success("Token 已复制")} />
            <div className="grid gap-2 text-sm text-slate-500">
              <div className="flex items-center gap-2">
                <span>Worker key</span>
                <MonoPill>{createdWorker.worker_key}</MonoPill>
              </div>
              <div className="flex items-center gap-2">
                <span>Token prefix</span>
                <MonoPill>{createdWorker.token_prefix}</MonoPill>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="text-sm leading-6 text-slate-500">
              创建后，使用该 token 运行 `duckdock_analysis_worker.py` 或让专用 OpenClaw 按标准分析 skill 执行轮询。
            </div>
            <Input
              value={workerName}
              onChange={(event) => setWorkerName(event.target.value)}
              placeholder="DuckDock OpenClaw Worker"
              maxLength={128}
            />
          </div>
        )}
      </Modal>

      <Modal
        title={artifactJob ? `任务 #${artifactJob.id} 结果文件` : "结果文件"}
        open={artifactJob !== null}
        footer={null}
        width={920}
        onCancel={() => setArtifactJob(null)}
      >
        <ArtifactTable
          artifacts={artifactsQuery.data ?? []}
          loading={artifactsQuery.isLoading}
          onOpenDownload={(artifact) => void openArtifactDownload(artifact)}
        />
      </Modal>
    </div>
  );
}

function WorkerTable({
  workers,
  loading,
  busyWorkerId,
  onDisable,
}: {
  workers: AnalysisWorker[];
  loading: boolean;
  busyWorkerId: number | null;
  onDisable: (worker: AnalysisWorker) => void;
}) {
  if (loading) {
    return <EmptyState text="正在加载 Worker..." icon={BrainCircuit} />;
  }
  if (workers.length === 0) {
    return <EmptyState text="还没有分析 Worker。请先创建 token 并部署专用 OpenClaw Worker。" icon={BrainCircuit} />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
        <thead className="bg-slate-50">
          <tr className="table-label">
            <th className="px-6 py-4">名称</th>
            <th className="px-6 py-4">Worker Key</th>
            <th className="px-6 py-4">Token</th>
            <th className="px-6 py-4">状态</th>
            <th className="px-6 py-4">最近心跳</th>
            <th className="px-6 py-4">创建时间</th>
            <th className="px-6 py-4">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 bg-white">
          {workers.map((worker) => (
            <tr key={worker.id} className="transition hover:bg-slate-50/70">
              <td className="px-6 py-4 font-semibold text-slate-900">{worker.name}</td>
              <td className="px-6 py-4">
                <MonoPill>{worker.worker_key}</MonoPill>
              </td>
              <td className="px-6 py-4">
                <MonoPill>{worker.token_prefix}...</MonoPill>
              </td>
              <td className="px-6 py-4">
                <Badge tone={statusTone(worker.status)}>{workerStatusLabels[worker.status]}</Badge>
              </td>
              <td className="px-6 py-4 text-slate-500">{formatDate(worker.last_seen_at)}</td>
              <td className="px-6 py-4 text-slate-500">{formatDate(worker.created_at)}</td>
              <td className="px-6 py-4">
                <Button
                  variant="secondary"
                  size="sm"
                  danger
                  onClick={() => onDisable(worker)}
                  disabled={worker.status === "disabled" || busyWorkerId === worker.id}
                >
                  禁用
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JobTable({
  jobs,
  loading,
  busyJobId,
  onOpenArtifacts,
  onCancel,
  onRetry,
}: {
  jobs: ReportAnalysisJob[];
  loading: boolean;
  busyJobId: number | null;
  onOpenArtifacts: (job: ReportAnalysisJob) => void;
  onCancel: (job: ReportAnalysisJob) => void;
  onRetry: (job: ReportAnalysisJob) => void;
}) {
  if (loading) {
    return <EmptyState text="正在加载分析任务..." icon={Clock3} />;
  }
  if (jobs.length === 0) {
    return <EmptyState text="还没有分析任务。用户侧 Reporter 上传并 finalize 后会自动创建任务。" icon={Clock3} />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
        <thead className="bg-slate-50">
          <tr className="table-label">
            <th className="px-6 py-4">任务</th>
            <th className="px-6 py-4">运行时</th>
            <th className="px-6 py-4">状态</th>
            <th className="px-6 py-4">Worker</th>
            <th className="px-6 py-4">产出</th>
            <th className="px-6 py-4">错误</th>
            <th className="px-6 py-4">更新时间</th>
            <th className="px-6 py-4">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 bg-white">
          {jobs.map((job) => (
            <tr key={job.id} className="transition hover:bg-slate-50/70">
              <td className="px-6 py-4">
                <div className="font-mono text-sm font-semibold text-slate-900">#{job.id}</div>
                <div className="mt-1 max-w-[220px] truncate font-mono text-xs text-slate-500">{job.input_object_key}</div>
              </td>
              <td className="px-6 py-4">
                <MonoPill>runtime:{job.runtime_id}</MonoPill>
              </td>
              <td className="px-6 py-4">
                <Badge tone={statusTone(job.status)}>{jobStatusLabels[job.status]}</Badge>
                <div className="mt-2 text-xs text-slate-400">
                  {job.attempts}/{job.max_attempts} attempts
                </div>
              </td>
              <td className="px-6 py-4 text-slate-500">{job.lease_owner ?? job.worker_id ?? "-"}</td>
              <td className="px-6 py-4">
                <JobOutputSummary job={job} />
              </td>
              <td className="max-w-[240px] truncate px-6 py-4 text-rose-600">{job.error_message ?? "-"}</td>
              <td className="px-6 py-4 text-slate-500">{formatDate(job.updated_at)}</td>
              <td className="px-6 py-4">
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" size="sm" onClick={() => onOpenArtifacts(job)}>
                    结果文件
                  </Button>
                  {["pending", "leased", "running"].includes(job.status) ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      danger
                      onClick={() => onCancel(job)}
                      disabled={busyJobId === job.id}
                    >
                      取消
                    </Button>
                  ) : null}
                  {["failed", "cancelled"].includes(job.status) ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => onRetry(job)}
                      disabled={busyJobId === job.id}
                    >
                      重试
                    </Button>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JobOutputSummary({ job }: { job: ReportAnalysisJob }) {
  const output = getJobOutput(job);
  if (!output.hasDetails) {
    return <span className="text-slate-500">{job.result_size_bytes ? formatBytes(job.result_size_bytes) : "-"}</span>;
  }
  return (
    <div className="min-w-[220px] space-y-1.5 text-xs leading-5 text-slate-500">
      <div className="flex flex-wrap items-center gap-1.5">
        <MonoPill>{output.artifacts} 文件</MonoPill>
        {output.schemaVersion ? <MonoPill>{output.schemaVersion}</MonoPill> : null}
        {output.failureCount > 0 ? <Badge tone="rose">{output.failureCount} 失败</Badge> : null}
      </div>
      <div className="font-mono text-[11px] text-slate-500">
        解析 {output.parsed} / 入库 {output.inserted} / 更新 {output.updated} / 去重 {output.deduped}
      </div>
      {output.model || output.analysisMode ? (
        <div className="max-w-[260px] truncate text-[11px] text-slate-400">
          {[output.analysisMode, output.model].filter(Boolean).join(" · ")}
        </div>
      ) : null}
      {output.traceId ? (
        <div className="max-w-[260px] truncate font-mono text-[11px] text-slate-400">trace:{output.traceId}</div>
      ) : null}
    </div>
  );
}

function ArtifactTable({
  artifacts,
  loading,
  onOpenDownload,
}: {
  artifacts: AnalysisResultArtifact[];
  loading: boolean;
  onOpenDownload: (artifact: AnalysisResultArtifact) => void;
}) {
  if (loading) {
    return <EmptyState text="正在加载结果文件..." icon={FileJson} />;
  }
  if (artifacts.length === 0) {
    return <EmptyState text="这个任务还没有索引到结果文件。" icon={FileJson} />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
        <thead className="bg-slate-50">
          <tr className="table-label">
            <th className="px-4 py-3">类型</th>
            <th className="px-4 py-3">对象</th>
            <th className="px-4 py-3">大小</th>
            <th className="px-4 py-3">SHA256</th>
            <th className="px-4 py-3">创建时间</th>
            <th className="px-4 py-3">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 bg-white">
          {artifacts.map((artifact) => (
            <tr key={artifact.id} className="transition hover:bg-slate-50/70">
              <td className="px-4 py-3 font-semibold text-slate-900">{artifactLabels[artifact.kind]}</td>
              <td className="max-w-[360px] truncate px-4 py-3 font-mono text-xs text-slate-500">{artifact.object_key}</td>
              <td className="px-4 py-3 text-slate-500">{formatBytes(artifact.size_bytes)}</td>
              <td className="max-w-[160px] truncate px-4 py-3 font-mono text-xs text-slate-500">{artifact.sha256 ?? "-"}</td>
              <td className="px-4 py-3 text-slate-500">{formatDate(artifact.created_at)}</td>
              <td className="px-4 py-3">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => onOpenDownload(artifact)}
                  icon={<ExternalLink className="h-3.5 w-3.5" />}
                >
                  打开
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MemoryCandidateTable({
  candidates,
  loading,
  busyId,
  onReview,
}: {
  candidates: MemoryCandidate[];
  loading: boolean;
  busyId: string | null;
  onReview: (candidateId: number, status: Exclude<MemoryCandidateStatus, "candidate">) => void;
}) {
  if (loading) {
    return <EmptyState text="正在加载记忆候选..." icon={Database} />;
  }
  if (candidates.length === 0) {
    return <EmptyState text="还没有记忆候选。分析 Worker 成功处理上传包后会在这里出现。" icon={Database} />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
        <thead className="bg-slate-50">
          <tr className="table-label">
            <th className="px-6 py-4">候选内容</th>
            <th className="px-6 py-4">类型</th>
            <th className="px-6 py-4">主体</th>
            <th className="px-6 py-4">置信度</th>
            <th className="px-6 py-4">状态</th>
            <th className="px-6 py-4">来源</th>
            <th className="px-6 py-4">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 bg-white">
          {candidates.map((candidate) => {
            const busy = busyId === String(candidate.id);
            return (
              <tr key={candidate.id} className="transition hover:bg-slate-50/70">
                <td className="max-w-[360px] px-6 py-4">
                  <div className="font-semibold text-slate-900">{candidate.title}</div>
                  <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{candidate.summary ?? "-"}</div>
                </td>
                <td className="px-6 py-4">
                  <MonoPill>{candidateTypeLabels[candidate.candidate_type]}</MonoPill>
                </td>
                <td className="px-6 py-4 text-slate-500">
                  <div>{candidate.subject_type}</div>
                  <div className="mt-1 font-mono text-xs">{candidate.subject_key ?? "-"}</div>
                </td>
                <td className="px-6 py-4 font-mono text-slate-500">{Math.round(candidate.confidence * 100)}%</td>
                <td className="px-6 py-4">
                  <Badge tone={statusTone(candidate.status)}>{candidateStatusLabels[candidate.status]}</Badge>
                </td>
                <td className="max-w-[220px] truncate px-6 py-4 font-mono text-xs text-slate-500">
                  {candidate.source_object_uri ?? `job:${candidate.analysis_job_id ?? "-"}`}
                </td>
                <td className="px-6 py-4">
                  {candidate.status === "candidate" ? (
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => onReview(candidate.id, "confirmed")}
                        disabled={busy}
                      >
                        确认
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        danger
                        onClick={() => onReview(candidate.id, "rejected")}
                        disabled={busy}
                      >
                        拒绝
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => onReview(candidate.id, "superseded")}
                        disabled={busy}
                      >
                        过期
                      </Button>
                    </div>
                  ) : (
                    <span className="text-xs text-slate-400">{formatDate(candidate.reviewed_at)}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CopyBlock({ value, onCopied }: { value: string; onCopied: () => void }) {
  async function copy() {
    await navigator.clipboard.writeText(value);
    onCopied();
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-950 p-4 text-slate-100">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">DUCKDOCK_WORKER_TOKEN</div>
        <button type="button" onClick={() => void copy()} className="rounded-md p-1.5 text-slate-300 hover:bg-white/10">
          <Copy className="h-4 w-4" />
        </button>
      </div>
      <code className="block break-all text-xs leading-5">{value}</code>
    </div>
  );
}

function statusTone(status: AnalysisWorkerStatus | AnalysisJobStatus | MemoryCandidateStatus): Tone {
  if (status === "active" || status === "succeeded" || status === "confirmed") {
    return "emerald";
  }
  if (status === "pending" || status === "leased" || status === "running" || status === "candidate") {
    return "indigo";
  }
  if (status === "failed" || status === "rejected") {
    return "rose";
  }
  if (status === "stale" || status === "superseded") {
    return "amber";
  }
  return "neutral";
}

function getJobOutput(job: ReportAnalysisJob) {
  const artifacts = asNumber(job.summary_json?.result_artifact_count);
  const materialized = isRecord(job.summary_json?.materialized_counts)
    ? job.summary_json.materialized_counts
    : null;
  const metadata = isRecord(job.summary_json?.worker_metadata) ? job.summary_json.worker_metadata : null;
  const failures = Array.isArray(materialized?.failures) ? materialized.failures.length : 0;
  return {
    artifacts: artifacts ?? 0,
    parsed: sumCountBucket(materialized?.parsed_counts),
    inserted: sumCountBucket(materialized?.inserted_counts, materialized),
    updated: sumCountBucket(materialized?.updated_counts),
    deduped: sumCountBucket(materialized?.deduped_counts),
    failureCount: failures,
    schemaVersion: stringValue(job.summary_json?.analysis_result_schema_version),
    model: stringValue(metadata?.model),
    traceId: stringValue(metadata?.trace_id),
    analysisMode: stringValue(metadata?.analysis_mode),
    hasDetails: artifacts !== null || materialized !== null,
  };
}

function sumCountBucket(value: unknown, fallback?: unknown) {
  const record = isRecord(value) ? value : isRecord(fallback) ? fallback : null;
  if (!record) {
    return 0;
  }
  return materializedCountKeys.reduce((total, key) => {
    const value = asNumber(record[key]);
    return total + (value ?? 0);
  }, 0);
}

function stringValue(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function formatBytes(value: number | null) {
  if (!value) {
    return "-";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatDuration(seconds: number | null) {
  if (seconds === null) {
    return "-";
  }
  if (seconds < 60) {
    return `${seconds}s`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`;
  }
  return `${Math.round(seconds / 3600)}h`;
}

function formatDate(value: string | null) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function asNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getErrorDetail(error: unknown) {
  if (!isRecord(error)) {
    return null;
  }
  const response = error.response;
  if (!isRecord(response)) {
    return null;
  }
  const data = response.data;
  if (!isRecord(data)) {
    return null;
  }
  return typeof data.detail === "string" ? data.detail : null;
}
