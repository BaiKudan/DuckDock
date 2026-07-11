import { useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  BookOpenText,
  CheckCircle2,
  CircleDashed,
  FlaskConical,
  LoaderCircle,
  MessageSquareMore,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Workflow,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { clinicApi, namespacesApi, type EvaluationSummary, type Namespace } from "../api/client";
import { useI18n } from "../i18n";
import { Badge, Button, Card, IconTile, MonoPill, PageHeader } from "../components/ui";
import type { Tone } from "../components/ui";

const STATUS_CONFIG = {
  pending: { labelKey: "clinic.status.pending", tone: "amber", Icon: CircleDashed },
  running: { labelKey: "clinic.status.running", tone: "indigo", Icon: LoaderCircle },
  completed: { labelKey: "clinic.status.completed", tone: "emerald", Icon: CheckCircle2 },
  failed: { labelKey: "clinic.status.failed", tone: "rose", Icon: RefreshCw },
} as const satisfies Record<string, { labelKey: string; tone: Tone; Icon: typeof CircleDashed }>;

const DIMENSIONS = [
  { labelKey: "clinic.dimension.completeness", weight: "15%", Icon: FlaskConical },
  { labelKey: "clinic.dimension.quality", weight: "20%", Icon: Sparkles },
  { labelKey: "clinic.dimension.coherence", weight: "15%", Icon: Workflow },
  { labelKey: "clinic.dimension.security", weight: "10%", Icon: ShieldCheck },
  { labelKey: "clinic.dimension.docs", weight: "10%", Icon: BookOpenText },
  { labelKey: "clinic.dimension.currency", weight: "15%", Icon: Activity },
  { labelKey: "clinic.dimension.style", weight: "5%", Icon: ScanSearch },
  { labelKey: "clinic.dimension.interaction", weight: "10%", Icon: MessageSquareMore },
];

function gradeTone(grade: string | null) {
  if (!grade) return "text-slate-400";
  if (grade === "A") return "text-emerald-700";
  if (grade.startsWith("B")) return "text-indigo-700";
  if (grade.startsWith("C")) return "text-amber-700";
  return "text-rose-700";
}

function scoreStroke(score: number) {
  if (score >= 80) return "#047857";
  if (score >= 60) return "#b45309";
  return "#be123c";
}

function ScoreRing({ score }: { score: number }) {
  const r = 28;
  const circumference = 2 * Math.PI * r;
  const fill = (score / 100) * circumference;

  return (
    <svg width="72" height="72" className="-rotate-90">
      <circle cx="36" cy="36" r={r} fill="none" stroke="#e2e8f0" strokeWidth="5" />
      <circle
        cx="36"
        cy="36"
        r={r}
        fill="none"
        stroke={scoreStroke(score)}
        strokeWidth="5"
        strokeDasharray={`${fill} ${circumference}`}
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function ClinicPage() {
  const navigate = useNavigate();
  const { locale, t } = useI18n();
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [selectedNs, setSelectedNs] = useState("");
  const [evaluations, setEvaluations] = useState<EvaluationSummary[]>([]);
  const [triggering, setTriggering] = useState(false);
  const [loadingEvals, setLoadingEvals] = useState(false);

  useEffect(() => {
    namespacesApi.list().then(({ data }) => {
      setNamespaces(data);
      if (data.length > 0) setSelectedNs(data[0].name);
    });
  }, []);

  useEffect(() => {
    if (!selectedNs) return;
    setLoadingEvals(true);
    clinicApi.listEvaluations(selectedNs).then(({ data }) => {
      setEvaluations(data);
      setLoadingEvals(false);
    });
  }, [selectedNs]);

  useEffect(() => {
    const hasActive = evaluations.some((evaluation) => evaluation.status === "pending" || evaluation.status === "running");
    if (!hasActive || !selectedNs) return;
    const timer = setTimeout(() => {
      clinicApi.listEvaluations(selectedNs).then(({ data }) => setEvaluations(data));
    }, 3000);
    return () => clearTimeout(timer);
  }, [evaluations, selectedNs]);

  async function handleTrigger() {
    if (!selectedNs) return;
    setTriggering(true);
    try {
      const { data } = await clinicApi.triggerEvaluation(selectedNs);
      setEvaluations((prev) => [data, ...prev]);
    } finally {
      setTriggering(false);
    }
  }

  return (
    <div className="app-page max-w-6xl">
      <PageHeader
        eyebrow={t("clinic.badge")}
        title={t("clinic.title")}
        description={t("clinic.subtitle")}
        actions={
          <>
            <select
              value={selectedNs}
              onChange={(event) => setSelectedNs(event.target.value)}
              className="min-w-52 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition-colors focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100"
            >
              {namespaces.map((namespace) => (
                <option key={namespace.name} value={namespace.name}>
                  {namespace.name}
                </option>
              ))}
            </select>
            <Button onClick={handleTrigger} disabled={triggering || !selectedNs} icon={<FlaskConical className="h-4 w-4" />}>
              {triggering ? t("clinic.queuing") : t("clinic.run")}
            </Button>
          </>
        }
      />

      <div className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {DIMENSIONS.map(({ labelKey, weight, Icon }) => (
          <Card key={labelKey} padded className="flex items-center gap-4">
            <IconTile size="lg">
              <Icon className="h-5 w-5" />
            </IconTile>
            <div className="min-w-0">
              <div className="font-semibold tracking-tight text-slate-900">{t(labelKey)}</div>
              <div className="text-sm text-slate-500">{t("clinic.weight", { weight })}</div>
            </div>
          </Card>
        ))}
      </div>

      <Card
        className="mt-8"
        title={t("clinic.history.title")}
        description={t("clinic.history.subtitle", { namespace: selectedNs || "namespace" })}
        action={<MonoPill>{t("clinic.history.runs", { count: evaluations.length })}</MonoPill>}
      >
        {loadingEvals ? (
          <div className="flex items-center justify-center gap-2 px-6 py-12 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-slate-400" />
            {t("clinic.loading")}
          </div>
        ) : evaluations.length === 0 ? (
          <div className="px-6 py-16 text-center">
            <IconTile size="lg" className="mx-auto">
              <FlaskConical className="h-5 w-5" />
            </IconTile>
            <p className="mt-4 text-sm text-slate-500">{t("clinic.empty", { namespace: selectedNs })}</p>
            <Button variant="secondary" onClick={handleTrigger} disabled={triggering} className="mt-4">
              {t("clinic.firstRun")}
            </Button>
          </div>
        ) : (
          <div className="divide-y divide-slate-200">
            {evaluations.map((evaluation) => {
              const config = STATUS_CONFIG[evaluation.status];
              const isActive = evaluation.status === "pending" || evaluation.status === "running";
              const StatusIcon = config.Icon;

              return (
                <button
                  key={evaluation.id}
                  onClick={() => evaluation.status === "completed" && navigate(`/clinic/${evaluation.id}`)}
                  disabled={evaluation.status !== "completed"}
                  className="flex w-full items-center justify-between px-5 py-5 text-left transition-colors hover:bg-slate-50/70 disabled:cursor-default disabled:hover:bg-transparent sm:px-6"
                >
                  <div className="flex items-center gap-4">
                    <div className="relative flex h-[72px] w-[72px] shrink-0 items-center justify-center">
                      {evaluation.overall_score != null ? (
                        <>
                          <ScoreRing score={evaluation.overall_score} />
                          <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <span className={`text-sm font-bold ${gradeTone(evaluation.grade)}`}>{evaluation.grade}</span>
                            <span className="text-xs text-slate-500">{evaluation.overall_score}</span>
                          </div>
                        </>
                      ) : (
                        <div className="flex h-[72px] w-[72px] items-center justify-center rounded-full border border-slate-200 bg-slate-50 text-slate-500">
                          <StatusIcon className={`h-6 w-6 ${isActive ? "animate-spin" : ""}`} />
                        </div>
                      )}
                    </div>

                    <div>
                      <div className="mb-2 flex items-center gap-2">
                        <Badge tone={config.tone} icon={<StatusIcon className={`h-3.5 w-3.5 ${isActive ? "animate-spin" : ""}`} />}>
                          {t(config.labelKey)}
                        </Badge>
                        <MonoPill>#{evaluation.id}</MonoPill>
                      </div>
                      <div className="text-sm text-slate-500">
                        {new Date(evaluation.created_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US")}
                        {evaluation.completed_at ? (
                          <span className="ml-2">
                            {t("clinic.durationSeconds", {
                              count: Math.round((new Date(evaluation.completed_at).getTime() - new Date(evaluation.created_at).getTime()) / 1000),
                            })}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </div>

                  {evaluation.status === "completed" ? (
                    <span className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600">
                      {t("clinic.viewReport")}
                      <ArrowRight className="h-4 w-4" />
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
