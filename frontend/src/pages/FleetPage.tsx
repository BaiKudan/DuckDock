import {
  Activity,
  AlertTriangle,
  Boxes,
  History,
  RefreshCw,
  ServerCog,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fleetApi,
  namespacesApi,
  type AdapterConfigDrift,
  type FleetRuntime,
} from "../api/client";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  MetricCard,
  MonoPill,
  PageHeader,
  type Tone,
} from "../components/ui";
import { useI18n } from "../i18n";

const heartbeatTone: Record<FleetRuntime["heartbeat_state"], Tone> = {
  HEALTHY: "emerald",
  STALE: "amber",
  NEVER: "neutral",
  ERROR: "rose",
};

const driftTone: Record<AdapterConfigDrift, Tone> = {
  NONE: "emerald",
  CONFIG_CHANGED: "amber",
  BOOT_CHANGED: "amber",
  CAPABILITY_CHANGED: "rose",
};

function formatTime(value: string | null, locale: string): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

export default function FleetPage() {
  const { locale } = useI18n();
  const zh = locale === "zh";
  const [namespaceId, setNamespaceId] = useState<number | null>(null);
  const namespaces = useQuery({
    queryKey: ["fleet", "namespaces"],
    queryFn: async () => (await namespacesApi.list()).data,
  });
  useEffect(() => {
    if (namespaceId === null && namespaces.data?.length) {
      setNamespaceId(namespaces.data[0].id);
    }
  }, [namespaceId, namespaces.data]);
  const fleet = useQuery({
    queryKey: ["fleet", namespaceId],
    queryFn: async () => (await fleetApi.summary(namespaceId as number)).data,
    enabled: namespaceId !== null,
    refetchInterval: 15_000,
  });
  const summary = fleet.data;

  return (
    <div className="app-page max-w-7xl space-y-6">
      <PageHeader
        eyebrow="RUNTIME FLEET"
        title={zh ? "Runtime Fleet" : "Runtime Fleet"}
        description={
          zh
            ? "按动态握手和心跳观察版本、能力、Collector 与配置漂移；未握手 Runtime 不会显示为可用。"
            : "Observe versions, negotiated capabilities, collectors, and configuration drift from live handshakes."
        }
        actions={
          <div className="flex items-center gap-2">
            <select
              className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700"
              value={namespaceId ?? ""}
              onChange={(event) => setNamespaceId(Number(event.target.value))}
              aria-label={zh ? "命名空间" : "Namespace"}
            >
              {(namespaces.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <Button
              variant="secondary"
              onClick={() => void fleet.refetch()}
              disabled={fleet.isFetching}
              icon={<RefreshCw className={`h-4 w-4 ${fleet.isFetching ? "animate-spin" : ""}`} />}
            >
              {zh ? "刷新" : "Refresh"}
            </Button>
          </div>
        }
      />

      {fleet.error ? (
        <div className="soft-rose rounded-lg px-4 py-3 text-sm">
          {zh ? "Fleet 状态加载失败，请检查 Namespace 权限。" : "Failed to load Fleet status."}
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label={zh ? "Runtime" : "Runtimes"}
          value={summary?.runtime_count ?? 0}
          detail={zh ? "当前 Namespace 中的运行时" : "Runtimes in this Namespace"}
          icon={Boxes}
          loading={fleet.isLoading}
        />
        <MetricCard
          label={zh ? "健康心跳" : "Healthy"}
          value={summary?.healthy_count ?? 0}
          detail={zh ? "位于新鲜度窗口内" : "Within the heartbeat freshness window"}
          icon={Activity}
          tone="indigo"
          loading={fleet.isLoading}
        />
        <MetricCard
          label={zh ? "降级或过期" : "Degraded"}
          value={summary?.degraded_count ?? 0}
          detail={zh ? "需要重新握手或修复链路" : "Requires renegotiation or repair"}
          icon={AlertTriangle}
          tone={summary?.degraded_count ? "rose" : "neutral"}
          loading={fleet.isLoading}
        />
        <MetricCard
          label={zh ? "配置漂移" : "Drifted"}
          value={summary?.drifted_count ?? 0}
          detail={zh ? "启动、配置或能力集合已变化" : "Boot, config, or capabilities changed"}
          icon={ServerCog}
          tone={summary?.drifted_count ? "rose" : "neutral"}
          loading={fleet.isLoading}
        />
      </div>

      {fleet.isLoading ? (
        <Card title={zh ? "Runtime 清单" : "Runtime inventory"} padded>
          <EmptyState text={zh ? "正在加载 Fleet…" : "Loading Fleet…"} />
        </Card>
      ) : null}
      {!fleet.isLoading && summary?.runtimes.length === 0 ? (
        <Card title={zh ? "Runtime 清单" : "Runtime inventory"} padded>
          <EmptyState text={zh ? "这个 Namespace 暂无 Runtime。" : "No Runtime in this Namespace."} />
        </Card>
      ) : null}
      <div className="space-y-4">
        {(summary?.runtimes ?? []).map((runtime) => (
          <RuntimeCard key={runtime.runtime_public_id} runtime={runtime} locale={locale} zh={zh} />
        ))}
      </div>
    </div>
  );
}

function RuntimeCard({
  runtime,
  locale,
  zh,
}: {
  runtime: FleetRuntime;
  locale: string;
  zh: boolean;
}) {
  return (
    <Card
      title={
        <div className="flex flex-wrap items-center gap-2">
          <span>{runtime.name}</span>
          <Badge tone={heartbeatTone[runtime.heartbeat_state]}>{runtime.heartbeat_state}</Badge>
          <Badge tone={runtime.handshake_status === "ACTIVE" ? "emerald" : "neutral"}>
            {runtime.handshake_status}
          </Badge>
        </div>
      }
      description={<MonoPill>{runtime.runtime_public_id}</MonoPill>}
      action={
        runtime.config_drift ? (
          <Badge tone={driftTone[runtime.config_drift]}>{runtime.config_drift}</Badge>
        ) : null
      }
    >
      <div className="grid gap-5 p-5 lg:grid-cols-[1.2fr_1fr_1fr]">
        <InfoBlock
          title={zh ? "适配器与能力" : "Adapter and capabilities"}
          rows={[
            [zh ? "Profile" : "Profile", runtime.profile ?? "—"],
            [zh ? "适配器" : "Adapter", runtime.adapter_id ?? "—"],
            [zh ? "版本" : "Version", runtime.adapter_version ?? "—"],
            [zh ? "认证级别" : "Certified level", runtime.certified_capability_level ?? "—"],
          ]}
        >
          <div className="mt-3 flex flex-wrap gap-1.5">
            {runtime.accepted_capabilities.length ? (
              runtime.accepted_capabilities.map((capability) => (
                <Badge key={capability} tone="indigo">{capability}</Badge>
              ))
            ) : (
              <span className="text-xs text-slate-400">{zh ? "尚未协商能力" : "No negotiated capabilities"}</span>
            )}
          </div>
        </InfoBlock>
        <InfoBlock
          title={zh ? "链路状态" : "Link state"}
          rows={[
            [zh ? "最后心跳" : "Last heartbeat", formatTime(runtime.last_heartbeat_at, locale)],
            [zh ? "握手过期" : "Handshake expires", formatTime(runtime.handshake_expires_at, locale)],
            [zh ? "Collector" : "Collector", runtime.collector_status ?? "—"],
            [zh ? "Collector 版本" : "Collector version", runtime.collector_version ?? "—"],
          ]}
        />
        <InfoBlock
          title={zh ? "工作负载" : "Workload"}
          rows={[
            [zh ? "最近 Run" : "Latest run", formatTime(runtime.latest_run_at, locale)],
            [zh ? "待处理 Pack" : "Pending Packs", String(runtime.pending_pack_import_count)],
            [zh ? "隔离项" : "Quarantined", String(runtime.quarantined_item_count)],
            [
              zh ? "Namespace Sink" : "Namespace sinks",
              `${runtime.namespace_active_telemetry_sink_count}/${runtime.namespace_telemetry_sink_count}`,
            ],
          ]}
        />
      </div>
      <div className="border-t border-slate-200 px-5 py-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-800">
          <History className="h-4 w-4 text-slate-400" />
          {zh ? "最近心跳历史" : "Recent heartbeat history"}
        </div>
        {runtime.heartbeat_history.length ? (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {runtime.heartbeat_history.slice(0, 6).map((item, index) => (
              <div key={`${item.observed_at}-${index}`} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-slate-700">{item.status}</span>
                  <span className="text-slate-400">{formatTime(item.observed_at, locale)}</span>
                </div>
                <div className="mt-1 text-slate-500">
                  {item.config_drift}
                  {item.collector_status ? ` · collector:${item.collector_status}` : ""}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-slate-400">{zh ? "尚无心跳记录。" : "No heartbeat recorded."}</div>
        )}
      </div>
    </Card>
  );
}

function InfoBlock({
  title,
  rows,
  children,
}: {
  title: string;
  rows: Array<[string, string]>;
  children?: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-sm font-semibold text-slate-800">{title}</div>
      <dl className="mt-3 space-y-2 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-start justify-between gap-3">
            <dt className="text-slate-500">{label}</dt>
            <dd className="break-all text-right font-medium text-slate-700">{value}</dd>
          </div>
        ))}
      </dl>
      {children}
    </div>
  );
}
