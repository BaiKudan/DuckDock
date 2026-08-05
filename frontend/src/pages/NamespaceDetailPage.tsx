import { ChevronLeft, Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "../router";
import {
  lifecycleApi,
  namespacesApi,
  replicationApi,
  robotsApi,
  skillsApi,
  webhooksApi,
  type Namespace,
  type NamespaceQuota,
  type ReplicationJob,
  type ReplicationRule,
  type RetentionPolicy,
  type RobotAccount,
  type RobotAccountCreated,
  type Skill,
  type Webhook,
  type WebhookDelivery,
} from "../api/client";
import { QuotaPanel } from "../components/enterprise/QuotaPanel";
import { ReplicationPanel } from "../components/enterprise/ReplicationPanel";
import { RobotsPanel } from "../components/enterprise/RobotsPanel";
import { SkillsPanel } from "../components/enterprise/SkillsPanel";
import { GovernancePanel } from "../components/enterprise/GovernancePanel";
import { WebhooksPanel } from "../components/enterprise/WebhooksPanel";
import { Button, Card, Input, MonoPill, PageHeader } from "../components/ui";
import { useI18n } from "../i18n";

type Tab = "skills" | "governance" | "quota" | "webhooks" | "robots" | "replication";

function NamespaceManageModal({
  mode,
  namespace,
  onClose,
  onUpdated,
  onDeleted,
}: {
  mode: "edit" | "delete";
  namespace: Namespace;
  onClose: () => void;
  onUpdated: (value: Namespace) => void;
  onDeleted: () => void;
}) {
  const { t } = useI18n();
  const [description, setDescription] = useState(namespace.description ?? "");
  const [confirmName, setConfirmName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "edit") {
        const { data } = await namespacesApi.update(namespace.name, {
          description: description.trim() || null,
        });
        onUpdated(data);
      } else {
        if (confirmName !== namespace.name) {
          setError(t("namespaces.manage.confirmDelete"));
          return;
        }
        await namespacesApi.delete(namespace.name);
        onDeleted();
      }
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      setError(
        Array.isArray(detail)
          ? detail[0]?.msg
          : detail ?? (mode === "edit" ? t("namespaces.manage.updateFailed") : t("namespaces.manage.deleteFailed"))
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/45 px-4 backdrop-blur-sm">
      <Card className="w-full max-w-lg" padded>
        <h2 className="text-lg font-semibold tracking-tight text-slate-900">
          {mode === "edit" ? t("namespaces.manage.updateTitle") : t("namespaces.manage.deleteTitle")}
        </h2>
        {error ? (
          <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        ) : null}
        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
              {t("field.name")}
            </label>
            <Input value={namespace.name} disabled className="font-mono" />
            <p className="mt-2 text-xs text-slate-500">{t("namespaces.manage.nameImmutable")}</p>
          </div>
          {mode === "edit" ? (
            <div>
              <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
                {t("field.description")}
              </label>
              <Input value={description} onChange={(event) => setDescription(event.target.value)} />
            </div>
          ) : (
            <div>
              <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-700">
                {t("namespaces.manage.deleteMessage")}
              </p>
              <label className="mt-4 block">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
                  {t("namespaces.manage.confirmDelete")}
                </div>
                <Input value={confirmName} onChange={(event) => setConfirmName(event.target.value)} />
              </label>
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={loading} danger={mode === "delete"}>
              {loading
                ? t("common.loading")
                : mode === "edit"
                  ? t("common.save")
                  : t("common.delete")}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}

export default function NamespaceDetailPage() {
  const { ns } = useParams<{ ns: string }>();
  const navigate = useNavigate();
  const { t } = useI18n();

  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [deletedSkills, setDeletedSkills] = useState<Skill[]>([]);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<Tab>("skills");
  const [quota, setQuota] = useState<NamespaceQuota | null>(null);
  const [retention, setRetention] = useState<RetentionPolicy | null>(null);
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [deliveries, setDeliveries] = useState<Record<number, WebhookDelivery[]>>({});
  const [robots, setRobots] = useState<RobotAccount[]>([]);
  const [lastRobotToken, setLastRobotToken] = useState<RobotAccountCreated | null>(null);
  const [rules, setRules] = useState<ReplicationRule[]>([]);
  const [jobsByRule, setJobsByRule] = useState<Record<number, ReplicationJob[]>>({});
  const [loading, setLoading] = useState(true);
  const [manageMode, setManageMode] = useState<"edit" | "delete" | null>(null);

  useEffect(() => {
    if (!ns) {
      return;
    }
    setLoading(true);
    Promise.all([
      namespacesApi.list(),
      skillsApi.list(ns),
      skillsApi.listDeleted(ns),
      lifecycleApi.getQuota(ns),
      lifecycleApi.getRetention(ns).catch(() => ({ data: null })),
      webhooksApi.list(ns),
      webhooksApi.events(),
      robotsApi.list(ns),
      replicationApi.listRules(ns),
    ])
      .then(async ([nsResult, skillsResult, deletedSkillsResult, quotaResult, retentionResult, hooksResult, eventsResult, robotsResult, rulesResult]) => {
        setNamespaces(nsResult.data);
        setSkills(skillsResult.data);
        setDeletedSkills(deletedSkillsResult.data);
        setQuota(quotaResult.data);
        setRetention(retentionResult.data);
        setWebhooks(hooksResult.data);
        setEvents(eventsResult.data);
        setRobots(robotsResult.data);
        setRules(rulesResult.data);
        const jobEntries = await Promise.all(
          rulesResult.data.map(async (rule) => {
            const jobs = await replicationApi.listJobs(ns, rule.id).catch(() => ({ data: [] as ReplicationJob[] }));
            return [rule.id, jobs.data] as const;
          })
        );
        setJobsByRule(Object.fromEntries(jobEntries));
      })
      .finally(() => setLoading(false));
  }, [ns]);

  const currentNamespace = namespaces.find((item) => item.name === ns) ?? null;

  if (!ns) {
    return null;
  }

  return (
    <div className="app-page">
      {manageMode && currentNamespace ? (
        <NamespaceManageModal
          mode={manageMode}
          namespace={currentNamespace}
          onClose={() => setManageMode(null)}
          onUpdated={(value) => {
            setNamespaces((current) => current.map((item) => (item.name === value.name ? value : item)));
            setManageMode(null);
          }}
          onDeleted={() => {
            setManageMode(null);
            navigate("/namespaces");
          }}
        />
      ) : null}

      <button
        onClick={() => navigate("/namespaces")}
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-slate-500 transition-colors hover:text-slate-700"
      >
        <ChevronLeft className="h-4 w-4" />
        {t("namespace.back")}
      </button>

      <PageHeader
        eyebrow={t("namespace.badge")}
        title={ns}
        description={
          <span className="flex flex-col gap-3">
            <MonoPill className="w-fit">namespace::{ns}</MonoPill>
            <span>{t("namespace.subtitle")}</span>
          </span>
        }
        actions={
          <>
            <Button
              variant="secondary"
              icon={<Pencil className="h-4 w-4" />}
              onClick={() => setManageMode("edit")}
            >
              {t("namespaces.manage.edit")}
            </Button>
            <Button
              variant="secondary"
              danger
              icon={<Trash2 className="h-4 w-4" />}
              onClick={() => setManageMode("delete")}
            >
              {t("namespaces.manage.delete")}
            </Button>
          </>
        }
      />

      <div className="mb-8 mt-2 flex flex-wrap gap-2">
        {[
          ["skills", t("namespace.tab.skills")],
          ["governance", "治理"],
          ["quota", t("namespace.tab.quota")],
          ["webhooks", t("namespace.tab.webhooks")],
          ["robots", t("namespace.tab.robots")],
          ["replication", t("namespace.tab.replication")],
        ].map(([value, label]) => (
          <button
            key={value}
            onClick={() => setTab(value as Tab)}
            className={`rounded-md border px-4 py-2 text-sm font-medium transition-colors ${
              tab === value
                ? "border-indigo-200 bg-indigo-50 text-indigo-700"
                : "border-slate-200 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading ? <div className="text-sm text-slate-500">{t("namespace.loading")}</div> : null}
      {!loading && tab === "skills" ? (
        <SkillsPanel
          ns={ns}
          skills={skills}
          deletedSkills={deletedSkills}
          query={query}
          onQueryChange={setQuery}
          onSkillsChange={setSkills}
          onDeletedSkillsChange={setDeletedSkills}
        />
      ) : null}
      {!loading && tab === "governance" ? <GovernancePanel ns={ns} /> : null}
      {!loading && tab === "quota" && quota ? (
        <QuotaPanel
          ns={ns}
          quota={quota}
          retention={retention}
          onQuotaChange={setQuota}
          onRetentionChange={setRetention}
        />
      ) : null}
      {!loading && tab === "webhooks" ? (
        <WebhooksPanel
          ns={ns}
          hooks={webhooks}
          events={events}
          deliveries={deliveries}
          onHooksChange={setWebhooks}
          onDeliveriesChange={setDeliveries}
        />
      ) : null}
      {!loading && tab === "robots" ? (
        <RobotsPanel
          ns={ns}
          robots={robots}
          lastRobotToken={lastRobotToken}
          onRobotsChange={setRobots}
          onCreated={setLastRobotToken}
        />
      ) : null}
      {!loading && tab === "replication" ? (
        <ReplicationPanel
          ns={ns}
          namespaces={namespaces}
          rules={rules}
          jobsByRule={jobsByRule}
          onRulesChange={setRules}
          onJobsChange={setJobsByRule}
        />
      ) : null}
    </div>
  );
}
