import { useEffect, useState } from "react";
import { Play, Repeat, Trash2 } from "lucide-react";
import {
  replicationApi,
  type Namespace,
  type ReplicationJob,
  type ReplicationRule,
} from "../../api/client";
import { useI18n } from "../../i18n";
import { Panel, SelectField, TextField } from "./Panel";

export function ReplicationPanel({
  ns,
  namespaces,
  rules,
  jobsByRule,
  onRulesChange,
  onJobsChange,
}: {
  ns: string;
  namespaces: Namespace[];
  rules: ReplicationRule[];
  jobsByRule: Record<number, ReplicationJob[]>;
  onRulesChange: (value: ReplicationRule[]) => void;
  onJobsChange: (value: Record<number, ReplicationJob[]>) => void;
}) {
  const { t } = useI18n();
  const [form, setForm] = useState({
    name: "",
    destination_namespace: "",
    filter_pattern: "",
    trigger: "manual" as "manual" | "on_publish",
    is_active: true,
  });

  useEffect(() => {
    if (!form.destination_namespace) {
      const fallback = namespaces.find((item) => item.name !== ns);
      if (fallback) {
        setForm((current) => ({ ...current, destination_namespace: fallback.name }));
      }
    }
  }, [form.destination_namespace, namespaces, ns]);

  async function createRule(event: React.FormEvent) {
    event.preventDefault();
    const { data } = await replicationApi.createRule(ns, form);
    onRulesChange([data, ...rules]);
    onJobsChange({ ...jobsByRule, [data.id]: [] });
    setForm((current) => ({ ...current, name: "", filter_pattern: "", trigger: "manual", is_active: true }));
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Panel title={t("replication.create.title")} subtitle={t("replication.create.subtitle")}>
        <form onSubmit={createRule} className="space-y-4">
          <TextField label={t("field.name")} value={form.name} onChange={(value) => setForm((current) => ({ ...current, name: value }))} />
          <SelectField
            label={t("field.destinationNamespace")}
            value={form.destination_namespace}
            options={namespaces
              .filter((item) => item.name !== ns)
              .map((item) => ({ label: item.name, value: item.name }))}
            onChange={(value) => setForm((current) => ({ ...current, destination_namespace: value }))}
          />
          <TextField
            label={t("field.filterPattern")}
            value={form.filter_pattern}
            onChange={(value) => setForm((current) => ({ ...current, filter_pattern: value }))}
            placeholder={t("replication.filterPlaceholder")}
          />
          <SelectField
            label={t("field.trigger")}
            value={form.trigger}
            options={[
              { label: "manual", value: "manual" },
              { label: "on_publish", value: "on_publish" },
            ]}
            onChange={(value) => setForm((current) => ({ ...current, trigger: value as "manual" | "on_publish" }))}
          />
          <label className="flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(event) => setForm((current) => ({ ...current, is_active: event.target.checked }))}
            />
            {t("replication.enableNow")}
          </label>
          <button className="button-primary inline-flex items-center gap-2">
            <Repeat className="h-4 w-4" />
            {t("replication.create.button")}
          </button>
        </form>
      </Panel>

      <Panel title={t("replication.jobs.title")} subtitle={t("replication.jobs.subtitle")}>
        {rules.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
            {t("replication.empty")}
          </div>
        ) : (
          <div className="space-y-3">
            {rules.map((rule) => (
              <div key={rule.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <Repeat className="h-4 w-4 text-indigo-500" />
                      <div className="font-semibold text-slate-900">{rule.name}</div>
                      <span className={rule.is_active ? "soft-emerald" : "soft-rose"}>
                        {rule.is_active ? t("common.enable") : t("common.disable")}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <span className="mono-pill">{rule.src_namespace_name}</span>
                      <span className="text-slate-300">-&gt;</span>
                      <span className="mono-pill">{rule.dst_namespace_name}</span>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
                      <span className="mono-pill">trigger:{rule.trigger}</span>
                      <span className="mono-pill">filter:{rule.filter_pattern ?? "*"}</span>
                      <span className="mono-pill">last:{rule.last_job_status ?? t("replication.never")}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={async () => {
                        const { data } = await replicationApi.updateRule(ns, rule.id, { is_active: !rule.is_active });
                        onRulesChange(rules.map((item) => (item.id === rule.id ? data : item)));
                      }}
                      className="button-secondary px-3 py-1.5 text-xs"
                    >
                      {rule.is_active ? t("common.disable") : t("common.enable")}
                    </button>
                    <button
                      onClick={async () => {
                        await replicationApi.runRule(ns, rule.id);
                        const jobs = await replicationApi.listJobs(ns, rule.id);
                        onJobsChange({ ...jobsByRule, [rule.id]: jobs.data });
                      }}
                      className="button-secondary inline-flex items-center gap-1.5 px-3 py-1.5 text-xs"
                    >
                      <Play className="h-3.5 w-3.5" />
                      {t("common.run")}
                    </button>
                    <button
                      onClick={async () => {
                        await replicationApi.deleteRule(ns, rule.id);
                        onRulesChange(rules.filter((item) => item.id !== rule.id));
                      }}
                      className="inline-flex items-center gap-1.5 rounded-md border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      {t("common.delete")}
                    </button>
                  </div>
                </div>

                {jobsByRule[rule.id]?.length ? (
                  <div className="mt-4 space-y-2 border-t border-slate-200 pt-4">
                    {jobsByRule[rule.id].slice(0, 3).map((job) => (
                      <div key={job.id} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                        <span
                          className={
                            job.status === "completed"
                              ? "soft-emerald"
                              : job.status === "failed"
                                ? "soft-rose"
                                : "soft-indigo"
                          }
                        >
                          {job.status}
                        </span>
                        <span className="ml-2 font-mono">
                          copied={job.skills_copied} skipped={job.skills_skipped} failed={job.skills_failed}
                        </span>
                        <span className="ml-2">{new Date(job.created_at).toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
