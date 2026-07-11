import { useState } from "react";
import { Bot, Copy, KeyRound, Trash2 } from "lucide-react";
import {
  robotsApi,
  type NamespaceRole,
  type RobotAccount,
  type RobotAccountCreated,
} from "../../api/client";
import { useI18n } from "../../i18n";
import { NumberField, Panel, SelectField, TextField } from "./Panel";

export function RobotsPanel({
  ns,
  robots,
  lastRobotToken,
  onRobotsChange,
  onCreated,
}: {
  ns: string;
  robots: RobotAccount[];
  lastRobotToken: RobotAccountCreated | null;
  onRobotsChange: (value: RobotAccount[]) => void;
  onCreated: (value: RobotAccountCreated | null) => void;
}) {
  const { t } = useI18n();
  const [form, setForm] = useState({
    name: "",
    description: "",
    role: "developer" as NamespaceRole,
    expires_days: 30,
  });

  async function createRobot(event: React.FormEvent) {
    event.preventDefault();
    const { data } = await robotsApi.create(ns, form);
    onCreated(data);
    onRobotsChange([...robots, data]);
    setForm({ name: "", description: "", role: "developer", expires_days: 30 });
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Panel title={t("robots.create.title")} subtitle={t("robots.create.subtitle")}>
        <form onSubmit={createRobot} className="space-y-4">
          <TextField label={t("field.name")} value={form.name} onChange={(value) => setForm((current) => ({ ...current, name: value }))} />
          <TextField label={t("field.description")} value={form.description} onChange={(value) => setForm((current) => ({ ...current, description: value }))} />
          <div className="grid grid-cols-2 gap-4">
            <SelectField
              label={t("field.role")}
              value={form.role}
              options={[
                { label: "admin", value: "admin" },
                { label: "developer", value: "developer" },
                { label: "readonly", value: "readonly" },
              ]}
              onChange={(value) => setForm((current) => ({ ...current, role: value as NamespaceRole }))}
            />
            <NumberField label={t("field.expiresDays")} value={form.expires_days} onChange={(value) => setForm((current) => ({ ...current, expires_days: value }))} />
          </div>
          <button className="button-primary inline-flex items-center gap-2">
            <Bot className="h-4 w-4" />
            {t("robots.create.button")}
          </button>
        </form>

        {lastRobotToken ? (
          <div className="surface-muted mt-4">
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-slate-500">
              <KeyRound className="h-3.5 w-3.5" />
              {t("robots.token")}
            </div>
            <div className="mt-3 break-all rounded-lg border border-emerald-200 bg-white px-3 py-3 font-mono text-sm text-emerald-700">
              {lastRobotToken.token}
            </div>
            <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
              <Copy className="h-3.5 w-3.5" />
              Copy and store it now. It will not be shown again.
            </div>
          </div>
        ) : null}
      </Panel>

      <Panel title={t("robots.list.title")} subtitle={t("robots.list.subtitle")}>
        {robots.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
            {t("robots.empty")}
          </div>
        ) : (
          <div className="space-y-3">
            {robots.map((robot) => (
              <div key={robot.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <Bot className="h-4 w-4 text-indigo-500" />
                      <div className="font-semibold text-slate-900">{robot.name}</div>
                      <span className={robot.is_active ? "soft-emerald" : "soft-rose"}>
                        {robot.is_active ? t("common.enable") : t("robots.disabled")}
                      </span>
                    </div>
                    <div className="mt-1 text-sm text-slate-500">{robot.description ?? t("common.noDescription")}</div>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
                      <span className="mono-pill">prefix:{robot.token_prefix}</span>
                      <span className="mono-pill">role:{robot.role}</span>
                      <span className="mono-pill">
                        {t("robots.lastUsed")}:{robot.last_used_at ?? t("robots.never")}
                      </span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {robot.is_active ? (
                      <button
                        onClick={async () => {
                          const { data } = await robotsApi.disable(ns, robot.id);
                          onRobotsChange(robots.map((item) => (item.id === robot.id ? data : item)));
                        }}
                        className="button-secondary px-3 py-1.5 text-xs"
                      >
                        {t("common.disable")}
                      </button>
                    ) : (
                      <span className="soft-rose">{t("robots.disabled")}</span>
                    )}
                    <button
                      onClick={async () => {
                        await robotsApi.remove(ns, robot.id);
                        onRobotsChange(robots.filter((item) => item.id !== robot.id));
                      }}
                      className="inline-flex items-center gap-1.5 rounded-md border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      {t("common.delete")}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
