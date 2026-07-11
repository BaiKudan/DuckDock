import { CircleCheck, Copy, Download, Globe2, Inbox, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { publicRegistryApi, type PublicRegistryManifestResponse, type PublicRegistryVersionEntry } from "../api/client";
import { Badge, Button, Card, IconTile, MonoPill, PageHeader, EmptyState } from "../components/ui";
import { useI18n } from "../i18n";

export default function PublicRegistryPage() {
  const { t } = useI18n();
  const [items, setItems] = useState<PublicRegistryVersionEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<PublicRegistryVersionEntry | null>(null);
  const [manifest, setManifest] = useState<PublicRegistryManifestResponse | null>(null);
  const [loadingManifest, setLoadingManifest] = useState(false);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      const { data } = await publicRegistryApi.index({ latest_only: false, include_urls: false });
      if (!active) return;
      setItems(data.items);
      setSelected((current) => current ?? data.items[0] ?? null);
      setLoading(false);
    }
    load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selected) {
      setManifest(null);
      return;
    }
    const current = selected;
    let active = true;
    async function loadManifest() {
      setLoadingManifest(true);
      try {
        const { data } = await publicRegistryApi.manifest(current.namespace, current.skill, current.tag);
        if (active) {
          setManifest(data);
        }
      } finally {
        if (active) {
          setLoadingManifest(false);
        }
      }
    }
    loadManifest();
    return () => {
      active = false;
    };
  }, [selected]);

  async function handleCopy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopyMessage(t("public.copied", { label }));
    } catch {
      setCopyMessage(t("public.copyFailed", { label }));
    } finally {
      window.setTimeout(() => setCopyMessage(null), 1800);
    }
  }

  function getStatusLabel(status: string) {
    if (status === "production") {
      return t("public.status.production");
    }
    return status;
  }

  return (
    <div className="app-page max-w-7xl">
      <PageHeader
        eyebrow={t("public.badge")}
        title={t("public.title")}
        description={t("public.subtitle")}
      />
      <div className="mb-10 mt-4 max-w-3xl rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-600">
        {t("public.note")}
      </div>

      <div className="grid gap-6 xl:grid-cols-[24rem_minmax(0,1fr)]">
        <Card
          title={t("public.sharedVersions")}
          action={
            <Button
              variant="secondary"
              size="sm"
              icon={<RefreshCw className="h-3.5 w-3.5" />}
              onClick={async () => {
                setLoading(true);
                const { data } = await publicRegistryApi.index({ latest_only: false, include_urls: false });
                setItems(data.items);
                setSelected((current) =>
                  current
                    ? data.items.find(
                        (item) =>
                          item.namespace === current.namespace &&
                          item.skill === current.skill &&
                          item.tag === current.tag
                      ) ?? data.items[0] ?? null
                    : data.items[0] ?? null
                );
                setLoading(false);
              }}
            >
              {t("common.refresh")}
            </Button>
          }
        >
          {loading ? (
            <div className="px-5 py-8 text-sm text-slate-500 sm:px-6">{t("public.loading")}</div>
          ) : items.length === 0 ? (
            <EmptyState text={t("public.empty")} icon={Inbox} />
          ) : (
            <div className="divide-y divide-slate-200">
              {items.map((item) => {
                const key = `${item.namespace}/${item.skill}/${item.tag}`;
                const isActive = selected?.namespace === item.namespace && selected?.skill === item.skill && selected?.tag === item.tag;
                return (
                  <button
                    key={key}
                    onClick={() => setSelected(item)}
                    className={`w-full px-5 py-4 text-left transition sm:px-6 ${isActive ? "bg-indigo-50/70" : "hover:bg-slate-50/70"}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className={`text-sm font-semibold tracking-tight ${isActive ? "text-indigo-700" : "text-slate-900"}`}>
                          {item.skill}
                        </div>
                        <div className="mt-1 break-all font-mono text-xs text-slate-500">{item.namespace}--{item.skill}</div>
                      </div>
                      <Badge tone="emerald">{item.tag}</Badge>
                    </div>
                    <div className="mt-3 text-sm text-slate-600">{item.description ?? t("common.noDescription")}</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <MonoPill>{getStatusLabel(item.status)}</MonoPill>
                      {item.artifact_size_bytes ? <MonoPill>{formatBytes(item.artifact_size_bytes)}</MonoPill> : null}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </Card>

        <div className="space-y-6">
          {selected ? (
            <Card padded>
              <div className="flex items-start gap-3">
                <IconTile size="lg" tone="indigo">
                  <Globe2 className="h-5 w-5" />
                </IconTile>
                <div className="min-w-0">
                  <div className="text-sm font-semibold tracking-tight text-slate-900">{t("public.distribution.title")}</div>
                  <div className="mt-1 text-sm text-slate-600">{t("public.distribution.subtitle")}</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge tone="emerald" icon={<CircleCheck className="h-3.5 w-3.5" />}>{t("public.status.live")}</Badge>
                    <MonoPill>{selected.namespace}--{selected.skill}</MonoPill>
                    <MonoPill>{selected.tag}</MonoPill>
                  </div>
                </div>
              </div>

              <div className="mt-5 grid gap-3 xl:grid-cols-2">
                <InfoCard
                  label={t("public.slug")}
                  value={`${selected.namespace}--${selected.skill}`}
                  actionLabel={t("public.copySlug")}
                  onAction={() => handleCopy(`${selected.namespace}--${selected.skill}`, t("public.slug"))}
                />
                <InfoCard
                  label={t("public.commitSha")}
                  value={selected.commit_sha}
                  actionLabel={t("public.copySha")}
                  onAction={() => handleCopy(selected.commit_sha, t("public.commitSha"))}
                />
              </div>
            </Card>
          ) : null}

          <Card padded>
            <div className="mb-4 flex items-center gap-2 text-slate-900">
              <Download className="h-4 w-4 text-indigo-600" />
              <span className="text-sm font-semibold tracking-tight">{t("public.clientManifest")}</span>
            </div>

            {!selected ? (
              <div className="text-sm text-slate-500">{t("public.selectPrompt")}</div>
            ) : loadingManifest ? (
              <div className="text-sm text-slate-500">{t("public.loadingManifest")}</div>
            ) : manifest ? (
              <div className="space-y-4">
                <InfoCard
                  label={t("public.artifactUrl")}
                  value={manifest.artifact_url ?? t("public.unavailable")}
                  actionLabel={t("public.copyUrl")}
                  onAction={() => manifest.artifact_url && handleCopy(manifest.artifact_url, t("public.artifactUrl"))}
                />
                <InfoCard
                  label={t("public.manifestUrl")}
                  value={manifest.manifest_url ?? t("public.unavailable")}
                  actionLabel={t("public.copyUrl")}
                  onAction={() => manifest.manifest_url && handleCopy(manifest.manifest_url, t("public.manifestUrl"))}
                />
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4">
                  <div className="table-label">{t("public.manifestPreview")}</div>
                  <pre className="mt-3 max-h-[22rem] overflow-x-auto rounded-md border border-slate-200 bg-white px-3 py-3 font-mono text-xs leading-relaxed text-slate-700">
                    {JSON.stringify(manifest.manifest, null, 2)}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="text-sm text-slate-500">{t("public.manifestUnavailable")}</div>
            )}
          </Card>

          {copyMessage ? <div className="text-xs text-slate-500">{copyMessage}</div> : null}
        </div>
      </div>
    </div>
  );
}

function InfoCard({
  label,
  value,
  actionLabel,
  onAction,
}: {
  label: string;
  value: string;
  actionLabel: string;
  onAction: () => void;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4">
      <div className="table-label">{label}</div>
      <div className="mt-2 break-all font-mono text-sm text-slate-800">{value}</div>
      <Button
        variant="link"
        onClick={onAction}
        className="mt-3 text-xs"
        icon={<Copy className="h-3.5 w-3.5" />}
      >
        {actionLabel}
      </Button>
    </div>
  );
}

function formatBytes(bytes: number) {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}
