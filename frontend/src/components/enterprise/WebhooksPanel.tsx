import { useState } from "react";
import { BellRing, RefreshCw, Trash2, Webhook as WebhookIcon } from "lucide-react";
import { webhooksApi, type Webhook, type WebhookDelivery } from "../../api/client";
import { useI18n } from "../../i18n";
import { Panel, TextField } from "./Panel";

export function WebhooksPanel({
  ns,
  hooks,
  events,
  deliveries,
  onHooksChange,
  onDeliveriesChange,
}: {
  ns: string;
  hooks: Webhook[];
  events: string[];
  deliveries: Record<number, WebhookDelivery[]>;
  onHooksChange: (value: Webhook[]) => void;
  onDeliveriesChange: (value: Record<number, WebhookDelivery[]>) => void;
}) {
  const { t } = useI18n();
  const [form, setForm] = useState({
    name: "",
    url: "",
    secret: "",
    events: [] as string[],
    is_active: true,
  });

  async function createWebhook(event: React.FormEvent) {
    event.preventDefault();
    const { data } = await webhooksApi.create(ns, form);
    onHooksChange([...hooks, data]);
    setForm({ name: "", url: "", secret: "", events: [], is_active: true });
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Panel title={t("webhooks.create.title")} subtitle={t("webhooks.create.subtitle")}>
        <form onSubmit={createWebhook} className="space-y-4">
          <TextField label={t("field.name")} value={form.name} onChange={(value) => setForm((current) => ({ ...current, name: value }))} />
          <TextField label={t("field.url")} value={form.url} onChange={(value) => setForm((current) => ({ ...current, url: value }))} />
          <TextField label={t("field.secret")} value={form.secret} onChange={(value) => setForm((current) => ({ ...current, secret: value }))} />
          <div>
            <div className="mb-2 text-xs uppercase tracking-[0.18em] text-slate-500">{t("field.events")}</div>
            <div className="grid grid-cols-1 gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 sm:grid-cols-2">
              {events.map((item) => (
                <label key={item} className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={form.events.includes(item)}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        events: event.target.checked
                          ? [...current.events, item]
                          : current.events.filter((entry) => entry !== item),
                      }))
                    }
                  />
                  <span className="font-mono text-xs text-slate-600">{item}</span>
                </label>
              ))}
            </div>
          </div>
          <button className="button-primary inline-flex items-center gap-2">
            <BellRing className="h-4 w-4" />
            {t("webhooks.create.button")}
          </button>
        </form>
      </Panel>

      <Panel title={t("webhooks.list.title")} subtitle={t("webhooks.list.subtitle")}>
        {hooks.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
            {t("webhooks.empty")}
          </div>
        ) : (
          <div className="space-y-3">
            {hooks.map((hook) => (
              <div key={hook.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <WebhookIcon className="h-4 w-4 text-indigo-500" />
                      <div className="font-semibold text-slate-900">{hook.name}</div>
                      <span className={hook.is_active ? "soft-emerald" : "soft-rose"}>
                        {hook.is_active ? t("common.enable") : t("common.disable")}
                      </span>
                    </div>
                    <div className="mt-1 break-all text-xs text-slate-500">{hook.url}</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {(hook.events.length ? hook.events : [t("webhooks.noEvents")]).map((item) => (
                        <span key={item} className="mono-pill">
                          {item}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={async () => {
                        const { data } = await webhooksApi.update(ns, hook.id, { is_active: !hook.is_active });
                        onHooksChange(hooks.map((item) => (item.id === hook.id ? data : item)));
                      }}
                      className="button-secondary px-3 py-1.5 text-xs"
                    >
                      {hook.is_active ? t("common.disable") : t("common.enable")}
                    </button>
                    <button
                      onClick={async () => {
                        const { data } = await webhooksApi.deliveries(ns, hook.id);
                        onDeliveriesChange({ ...deliveries, [hook.id]: data });
                      }}
                      className="button-secondary inline-flex items-center gap-1.5 px-3 py-1.5 text-xs"
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      {t("common.refresh")}
                    </button>
                    <button
                      onClick={async () => {
                        await webhooksApi.remove(ns, hook.id);
                        onHooksChange(hooks.filter((item) => item.id !== hook.id));
                      }}
                      className="inline-flex items-center gap-1.5 rounded-md border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      {t("common.delete")}
                    </button>
                  </div>
                </div>

                {deliveries[hook.id]?.length ? (
                  <div className="mt-4 space-y-2 border-t border-slate-200 pt-4">
                    {deliveries[hook.id].slice(0, 5).map((delivery) => (
                      <div key={delivery.id} className="break-all rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                        <span className={delivery.success ? "soft-emerald" : "soft-rose"}>
                          {delivery.success ? t("common.ok") : t("common.fail")}
                        </span>
                        <span className="ml-2 font-mono">{delivery.event}</span>
                        {delivery.response_status ? <span className="ml-2 font-mono">({delivery.response_status})</span> : null}
                        <span className="ml-2">{new Date(delivery.attempted_at).toLocaleString()}</span>
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
