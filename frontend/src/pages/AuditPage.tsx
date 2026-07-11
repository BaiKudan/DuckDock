import { FileClock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { auditApi, namespacesApi, type AuditLog, type Namespace } from "../api/client";
import { useI18n } from "../i18n";
import { Badge, Card, EmptyState, IconTile, MonoPill, PageHeader, type Tone } from "../components/ui";

const ACTION_LABELS: Record<string, string> = {
  "namespace.deleted": "Namespace deleted",
  "namespace.restored": "Namespace restored",
  "skill.deleted": "Skill deleted",
  "skill.restored": "Skill restored",
  "skill.purged": "Skill permanently deleted",
  "skill.version.public_shared.updated": "Public sharing updated",
  "skill.version.downloaded": "Version downloaded",
  "artifact.manifest.issued": "Manifest URL issued",
  "artifact.download_link.issued": "Artifact URL issued",
  "clawhub.skill.downloaded": "ClawHub package downloaded",
  "clawhub.file.read": "ClawHub file read",
};

function actionTone(action: string): Tone {
  if (action.includes("restored")) {
    return "emerald";
  }
  if (action.includes("deleted") || action.includes("tombstone")) {
    return "rose";
  }
  if (action.includes("download") || action.includes("issued") || action.includes("read")) {
    return "amber";
  }
  return "neutral";
}

function displayAction(action: string) {
  return ACTION_LABELS[action] ?? action;
}

function formatDetails(details: Record<string, unknown> | null) {
  if (!details) {
    return "-";
  }
  const preferredOrder = ["namespace", "skill", "tag", "reason", "anonymous", "format", "expires_in", "path"];
  const entries = Object.entries(details);
  entries.sort((a, b) => {
    const ai = preferredOrder.indexOf(a[0]);
    const bi = preferredOrder.indexOf(b[0]);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });
  return entries
    .slice(0, 4)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(", ");
}

export default function AuditPage() {
  const { t } = useI18n();
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [namespace, setNamespace] = useState("");
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    namespacesApi.list().then(({ data }) => setNamespaces(data));
  }, []);

  useEffect(() => {
    setLoading(true);
    auditApi
      .list({
        namespace: namespace || undefined,
        action: action || undefined,
        limit: 100,
      })
      .then(({ data }) => setLogs(data))
      .finally(() => setLoading(false));
  }, [namespace, action]);

  const actions = useMemo(() => Array.from(new Set(logs.map((log) => log.action))).sort(), [logs]);

  return (
    <div className="app-page page-stack">
      <PageHeader
        eyebrow={t("audit.title")}
        title={t("audit.title")}
        description={t("audit.subtitle")}
        actions={
          <>
            <select
              value={namespace}
              onChange={(event) => setNamespace(event.target.value)}
              className="input-control min-w-[200px]"
            >
              <option value="">{t("audit.allNamespaces")}</option>
              {namespaces.map((item) => (
                <option key={item.id} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
            <select
              value={action}
              onChange={(event) => setAction(event.target.value)}
              className="input-control min-w-[220px]"
            >
              <option value="">{t("audit.allActions")}</option>
              {actions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </>
        }
      />

      <Card
        title={
          <span className="flex items-center gap-3">
            <IconTile tone="indigo">
              <FileClock className="h-4 w-4" />
            </IconTile>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-slate-900">{t("audit.title")}</span>
              <span className="block text-xs font-normal text-slate-500">
                {t("audit.records", { count: logs.length })}
              </span>
            </span>
          </span>
        }
      >
        {loading ? (
          <div className="px-6 py-12 text-sm text-slate-500">{t("audit.loading")}</div>
        ) : logs.length === 0 ? (
          <EmptyState text={t("audit.empty")} icon={FileClock} />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr className="table-label text-left">
                  <th className="px-6 py-3">{t("audit.time")}</th>
                  <th className="px-6 py-3">{t("audit.actor")}</th>
                  <th className="px-6 py-3">{t("audit.action")}</th>
                  <th className="px-6 py-3">{t("audit.resource")}</th>
                  <th className="px-6 py-3">{t("audit.details")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {logs.map((log) => (
                  <tr key={log.id} className="transition hover:bg-slate-50/70">
                    <td className="px-6 py-4">
                      <span className="font-mono text-xs text-slate-500">
                        {new Date(log.created_at).toLocaleString()}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm font-semibold text-slate-900">{log.username ?? "-"}</td>
                    <td className="px-6 py-4">
                      <Badge tone={actionTone(log.action)}>{displayAction(log.action)}</Badge>
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-500">
                      {log.resource_type ?? "-"}
                      {log.resource_id != null ? ` #${log.resource_id}` : ""}
                    </td>
                    <td className="px-6 py-4">
                      {log.details ? (
                        <MonoPill className="bg-transparent px-0 text-slate-500">
                          {formatDetails(log.details)}
                        </MonoPill>
                      ) : (
                        <span className="font-mono text-xs text-slate-500">{formatDetails(log.details)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
