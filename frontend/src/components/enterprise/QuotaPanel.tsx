import { useEffect, useState } from "react";
import { lifecycleApi, type NamespaceQuota, type RetentionPolicy } from "../../api/client";
import { useI18n } from "../../i18n";
import { NumberField, Panel, formatBytes } from "./Panel";

export function QuotaPanel({
  ns,
  quota,
  retention,
  onQuotaChange,
  onRetentionChange,
}: {
  ns: string;
  quota: NamespaceQuota;
  retention: RetentionPolicy | null;
  onQuotaChange: (value: NamespaceQuota) => void;
  onRetentionChange: (value: RetentionPolicy | null) => void;
}) {
  const { t } = useI18n();
  const [quotaForm, setQuotaForm] = useState({
    max_skills: quota.max_skills,
    max_versions_per_skill: quota.max_versions_per_skill,
    max_total_versions: quota.max_total_versions,
    max_storage_bytes: quota.max_storage_bytes,
  });
  const [retentionForm, setRetentionForm] = useState({
    keep_last_n: retention?.keep_last_n ?? 10,
    keep_days: retention?.keep_days ?? 90,
    delete_rejected: retention?.delete_rejected ?? true,
  });

  useEffect(() => {
    setQuotaForm({
      max_skills: quota.max_skills,
      max_versions_per_skill: quota.max_versions_per_skill,
      max_total_versions: quota.max_total_versions,
      max_storage_bytes: quota.max_storage_bytes,
    });
  }, [quota]);

  useEffect(() => {
    setRetentionForm({
      keep_last_n: retention?.keep_last_n ?? 10,
      keep_days: retention?.keep_days ?? 90,
      delete_rejected: retention?.delete_rejected ?? true,
    });
  }, [retention]);

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Panel title={t("quota.title")} subtitle={t("quota.subtitle")}>
        <UsageRow label={t("quota.skills")} current={quota.current_skills} max={quota.max_skills} />
        <UsageRow label={t("quota.versions")} current={quota.current_total_versions} max={quota.max_total_versions} />
        <UsageRow label={t("quota.storage")} current={quota.current_storage_bytes} max={quota.max_storage_bytes} formatter={formatBytes} />

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <NumberField label={t("field.maxSkills")} value={quotaForm.max_skills} onChange={(value) => setQuotaForm((current) => ({ ...current, max_skills: value }))} />
          <NumberField label={t("field.maxVersionsPerSkill")} value={quotaForm.max_versions_per_skill} onChange={(value) => setQuotaForm((current) => ({ ...current, max_versions_per_skill: value }))} />
          <NumberField label={t("field.maxTotalVersions")} value={quotaForm.max_total_versions} onChange={(value) => setQuotaForm((current) => ({ ...current, max_total_versions: value }))} />
          <NumberField label={t("field.maxStorageMb")} value={Math.round(quotaForm.max_storage_bytes / (1024 * 1024))} onChange={(value) => setQuotaForm((current) => ({ ...current, max_storage_bytes: value * 1024 * 1024 }))} />
        </div>

        <button
          onClick={async () => {
            const { data } = await lifecycleApi.updateQuota(ns, quotaForm);
            onQuotaChange(data);
          }}
          className="button-primary mt-6"
        >
          {t("quota.save")}
        </button>
      </Panel>

      <Panel title={t("retention.title")} subtitle={t("retention.subtitle")}>
        <div className="grid gap-4 md:grid-cols-2">
          <NumberField label={t("field.keepLast")} value={Number(retentionForm.keep_last_n)} onChange={(value) => setRetentionForm((current) => ({ ...current, keep_last_n: value }))} />
          <NumberField label={t("field.keepDays")} value={Number(retentionForm.keep_days)} onChange={(value) => setRetentionForm((current) => ({ ...current, keep_days: value }))} />
        </div>

        <label className="mt-5 flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          <input type="checkbox" checked={retentionForm.delete_rejected} onChange={(event) => setRetentionForm((current) => ({ ...current, delete_rejected: event.target.checked }))} />
          {t("retention.deleteRejected")}
        </label>

        <div className="mt-6 flex gap-3">
          <button
            onClick={async () => {
              const { data } = await lifecycleApi.updateRetention(ns, {
                keep_last_n: retentionForm.keep_last_n || null,
                keep_days: retentionForm.keep_days || null,
                delete_rejected: retentionForm.delete_rejected,
              });
              onRetentionChange(data);
            }}
            className="button-primary"
          >
            {t("retention.save")}
          </button>
          <button
            onClick={async () => {
              await lifecycleApi.triggerGC(ns);
            }}
            className="button-secondary"
          >
            {t("retention.runGc")}
          </button>
        </div>
      </Panel>
    </div>
  );
}

function UsageRow({
  label,
  current,
  max,
  formatter,
}: {
  label: string;
  current: number;
  max: number;
  formatter?: (value: number) => string;
}) {
  const format = formatter ?? ((value: number) => String(value));
  const width = max > 0 ? Math.min(100, (current / max) * 100) : 0;
  return (
    <div className="mb-5">
      <div className="mb-2 flex justify-between text-sm">
        <span className="font-medium text-slate-700">{label}</span>
        <span className="text-slate-500">
          {format(current)} / {format(max)}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full rounded-full bg-indigo-500" style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}
