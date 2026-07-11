import { Boxes, Plus, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { namespacesApi, type Namespace } from "../api/client";
import { Badge, Button, Card, IconTile, Input, MonoPill, PageHeader } from "../components/ui";
import { formatDate, useI18n } from "../i18n";

function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: (ns: Namespace) => void }) {
  const { t } = useI18n();
  const [form, setForm] = useState({ name: "", description: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { data } = await namespacesApi.create(form);
      onCreated(data);
    } catch (err: any) {
      setError(err.response?.data?.detail ?? t("namespaces.create.failed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/10 px-4 backdrop-blur-sm">
      <Card className="w-full max-w-md" padded>
        <h2 className="text-xl font-semibold tracking-tight text-slate-900">{t("namespaces.create.title")}</h2>
        {error ? (
          <div className="soft-rose mt-4 rounded-lg px-4 py-3 text-sm">{error}</div>
        ) : null}
        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
              {t("field.name")}
            </label>
            <Input
              required
              autoFocus
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="corp-core"
            />
          </div>
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
              {t("field.description")} <span className="normal-case tracking-normal text-slate-400">({t("namespaces.optional")})</span>
            </label>
            <Input
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              placeholder="Core internal registry"
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <Button variant="secondary" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? t("common.creating") : t("common.create")}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}

export default function NamespacesPage() {
  const { locale, t } = useI18n();
  const navigate = useNavigate();
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [deletedNamespaces, setDeletedNamespaces] = useState<Namespace[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([namespacesApi.list(), namespacesApi.listDeleted()]).then(([activeResult, deletedResult]) => {
      setNamespaces(activeResult.data);
      setDeletedNamespaces(deletedResult.data);
      setLoading(false);
    });
  }, []);

  async function restoreNamespace(name: string) {
    setRestoring(name);
    try {
      const { data } = await namespacesApi.restore(name);
      setDeletedNamespaces((current) => current.filter((item) => item.name !== name));
      setNamespaces((current) => [...current, data].sort((a, b) => a.name.localeCompare(b.name)));
    } finally {
      setRestoring(null);
    }
  }

  return (
    <div className="app-page">
      {showCreate ? (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={(namespace) => {
            setNamespaces((prev) => [...prev, namespace]);
            setShowCreate(false);
          }}
        />
      ) : null}

      <PageHeader
        eyebrow={t("namespaces.title")}
        title={t("namespaces.title")}
        description={t("namespaces.subtitle")}
        actions={
          <Button onClick={() => setShowCreate(true)} icon={<Plus className="h-4 w-4" />}>
            {t("namespaces.new")}
          </Button>
        }
      />

      {loading ? (
        <div className="text-sm text-slate-500">{t("common.loading")}</div>
      ) : namespaces.length === 0 ? (
        <Card className="flex flex-col items-center justify-center px-6 py-20 text-center">
          <IconTile size="lg" tone="indigo" className="mb-4 h-14 w-14">
            <Boxes className="h-6 w-6" />
          </IconTile>
          <div className="text-lg font-semibold text-slate-900">{t("namespaces.empty")}</div>
          <p className="mt-2 max-w-md text-sm text-slate-500">{t("namespaces.subtitle")}</p>
          <Button variant="secondary" onClick={() => setShowCreate(true)} className="mt-6">
            {t("namespaces.first")}
          </Button>
        </Card>
      ) : (
        <div className="space-y-10">
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {namespaces.map((namespace) => (
              <button
                key={namespace.id}
                onClick={() => navigate(`/namespaces/${namespace.name}`)}
                className="rounded-lg border border-slate-200 bg-white p-6 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300"
              >
                <div className="mb-5 flex items-center justify-between gap-4">
                  <IconTile size="lg" tone="indigo">
                    <Boxes className="h-5 w-5" />
                  </IconTile>
                  <MonoPill>{namespace.name}</MonoPill>
                </div>
                <div className="text-lg font-semibold tracking-tight text-slate-900">{namespace.name}</div>
                <p className="mt-2 min-h-[40px] text-sm leading-6 text-slate-500">
                  {namespace.description ?? t("common.noDescription")}
                </p>
                <div className="mt-5 border-t border-slate-200 pt-4 text-xs text-slate-400">
                  {t("namespaces.created", { date: formatDate(locale, namespace.created_at) })}
                </div>
              </button>
            ))}
          </div>

          {deletedNamespaces.length ? (
            <Card
              title={t("namespaces.deleted.title")}
              description={t("namespaces.deleted.subtitle")}
              padded
            >
              <div className="space-y-3">
                {deletedNamespaces.map((namespace) => (
                  <div
                    key={namespace.id}
                    className="flex flex-col gap-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-4 md:flex-row md:items-center md:justify-between"
                  >
                    <div>
                      <div className="flex items-center gap-3">
                        <span className="font-semibold tracking-tight text-slate-900">{namespace.name}</span>
                        <Badge tone="rose">{t("common.deleted")}</Badge>
                      </div>
                      <div className="mt-2 text-sm text-slate-500">
                        {namespace.description ?? t("common.noDescription")}
                      </div>
                    </div>
                    <Button
                      variant="secondary"
                      onClick={() => restoreNamespace(namespace.name)}
                      disabled={restoring === namespace.name}
                      icon={<RotateCcw className="h-4 w-4" />}
                    >
                      {restoring === namespace.name ? t("common.loading") : t("common.restore")}
                    </Button>
                  </div>
                ))}
              </div>
            </Card>
          ) : null}
        </div>
      )}
    </div>
  );
}
