import { useEffect, useState } from "react";
import { ArrowLeft, ExternalLink, RotateCcw } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import {
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { clinicApi, type DimensionMeta, type DimensionScore, type EvaluationFull, type Recommendation } from "../api/client";
import { AiAssistBadge } from "../components/AiAssistBadge";
import { Badge, Button } from "../components/ui";
import { useI18n } from "../i18n";

const DIM_META: DimensionMeta[] = [
  { id: "skill_completeness", name_zh: "完整度", name_en: "Completeness", weight: 15 },
  { id: "skill_quality", name_zh: "质量", name_en: "Quality", weight: 20 },
  { id: "skill_coherence", name_zh: "一致性", name_en: "Coherence", weight: 15 },
  { id: "security_health", name_zh: "安全", name_en: "Security", weight: 10 },
  { id: "documentation", name_zh: "文档", name_en: "Docs", weight: 10 },
  { id: "version_currency", name_zh: "时效性", name_en: "Currency", weight: 15 },
  { id: "style_consistency", name_zh: "风格", name_en: "Style", weight: 5 },
  { id: "interaction_quality", name_zh: "交互", name_en: "Interaction", weight: 10 },
];

function gradeColor(grade: string | null) {
  if (!grade) return "#94a3b8";
  if (grade === "A") return "#047857";
  if (grade.startsWith("B")) return "#4338ca";
  if (grade.startsWith("C")) return "#b45309";
  return "#be123c";
}

function scoreColor(score: number) {
  if (score >= 80) return "#047857";
  if (score >= 60) return "#b45309";
  return "#be123c";
}

const PRIORITY_CFG: Record<string, { badge: string; dot: string; label: string }> = {
  critical: { badge: "border-rose-200 bg-rose-50 text-rose-700", dot: "bg-rose-600", label: "Critical" },
  high: { badge: "border-orange-200 bg-orange-50 text-orange-700", dot: "bg-orange-500", label: "High" },
  medium: { badge: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500", label: "Medium" },
  low: { badge: "border-indigo-200 bg-indigo-50 text-indigo-700", dot: "bg-indigo-500", label: "Low" },
};

const TREND_TONE: Record<string, string> = {
  up: "text-emerald-700",
  down: "text-rose-700",
  stable: "text-slate-500",
};

function ScoreRing({ score, grade }: { score: number; grade: string | null }) {
  const r = 52;
  const circumference = 2 * Math.PI * r;
  const fill = (score / 100) * circumference;
  const color = gradeColor(grade);

  return (
    <div className="relative flex h-32 w-32 items-center justify-center">
      <svg width="128" height="128" className="-rotate-90 absolute inset-0">
        <circle cx="64" cy="64" r={r} fill="none" stroke="#e2e8f0" strokeWidth="8" />
        <circle
          cx="64"
          cy="64"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeDasharray={`${fill} ${circumference}`}
          strokeLinecap="round"
        />
      </svg>
      <div className="z-10 flex flex-col items-center">
        <span className="text-3xl font-bold tracking-tight text-slate-900">{score}</span>
        <span className="text-base font-bold" style={{ color }}>
          {grade}
        </span>
      </div>
    </div>
  );
}

function DimensionCard({
  meta,
  score,
  expanded,
  onClick,
  locale,
  t,
}: {
  meta: DimensionMeta;
  score: DimensionScore;
  expanded: boolean;
  onClick: () => void;
  locale: "zh" | "en";
  t: (key: string, vars?: Record<string, string | number>) => string;
}) {
  const color = scoreColor(score.score);
  const label = locale === "zh" ? meta.name_zh : meta.name_en;
  const trendLabel = score.trend ? t(`clinic.trend.${score.trend}`) : "";

  return (
    <button
      onClick={onClick}
      className={`w-full rounded-xl border bg-white text-left transition ${
        expanded ? "border-indigo-200 ring-1 ring-indigo-100" : "border-slate-200 hover:bg-slate-50"
      }`}
    >
      <div className="p-4">
        <div className="mb-3 flex items-center justify-between gap-4">
          <div>
            <div className="font-semibold tracking-tight text-slate-900">{label}</div>
            <div className="text-xs text-slate-500">{t("clinic.weight", { weight: `${meta.weight}%` })}</div>
          </div>
          <div className="text-right">
            <div className="text-xl font-bold tracking-tight text-slate-900">{Math.round(score.score)}</div>
            <div className={`text-xs font-medium ${TREND_TONE[score.trend] ?? "text-slate-500"}`}>{trendLabel}</div>
            {score.confidence != null ? (
              <div className="mt-1 text-[11px] text-slate-500">{t("clinic.report.confidence", { value: `${Math.round(score.confidence * 100)}%` })}</div>
            ) : null}
          </div>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
          <div className="h-full rounded-full" style={{ width: `${score.score}%`, backgroundColor: color }} />
        </div>
      </div>

      {expanded ? (
        <div className="border-t border-slate-200 px-4 pb-4 pt-3">
          <p className="text-sm leading-relaxed text-slate-600">{score.details}</p>
          {score.reasoning_summary ? (
            <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="text-[11px] uppercase tracking-[0.16em] text-slate-500">{t("clinic.report.reasoning")}</div>
              <p className="mt-1 text-sm leading-relaxed text-slate-700">{score.reasoning_summary}</p>
            </div>
          ) : null}
          {score.issues.length > 0 ? (
            <div className="mt-3 space-y-2">
              {score.issues.map((issue, index) => (
                <div key={index} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                  {issue}
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
              {t("clinic.report.noIssues")}
            </div>
          )}
          <div className="mt-3 rounded-lg border border-slate-200 bg-white px-3 py-3">
            <div className="text-[11px] uppercase tracking-[0.16em] text-slate-500">{t("clinic.report.evidence")}</div>
            {score.evidence && score.evidence.length > 0 ? (
              <div className="mt-2 space-y-2">
                {score.evidence.map((item, index) => (
                  <div key={index} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                    <div className="text-sm font-medium text-slate-800">
                      {item.skill ? `${item.skill}: ` : ""}
                      {item.reason}
                    </div>
                    {item.snippet ? <div className="mt-1 text-xs leading-relaxed text-slate-500">{item.snippet}</div> : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-2 text-sm text-slate-500">{t("clinic.report.noEvidence")}</div>
            )}
          </div>
        </div>
      ) : null}
    </button>
  );
}

function RecommendationCard({
  rec,
  approved,
  onApprove,
  locale,
  t,
}: {
  rec: Recommendation;
  approved: boolean;
  onApprove: () => void;
  locale: "zh" | "en";
  t: (key: string, vars?: Record<string, string | number>) => string;
}) {
  const config = PRIORITY_CFG[rec.priority];
  const actionLabel = t(`clinic.action.${rec.action}`);
  const dimension = DIM_META.find((entry) => entry.id === rec.dimension);
  const priorityLabel = t(`clinic.priority.${rec.priority}`);
  const dimensionLabel = dimension ? (locale === "zh" ? dimension.name_zh : dimension.name_en) : null;

  return (
    <div className={`rounded-xl border bg-white p-4 ${approved ? "border-emerald-200 opacity-70" : "border-slate-200"}`}>
      <div className="flex items-start gap-3">
        <div className={`mt-1 h-2 w-2 shrink-0 rounded-full ${config.dot}`} />
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className={`rounded-md border px-2 py-1 text-[11px] font-medium ${config.badge}`}>{priorityLabel}</span>
            {dimensionLabel ? <span className="text-xs text-slate-500">{dimensionLabel}</span> : null}
            <span className="ml-auto rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-700">
              {actionLabel}
            </span>
          </div>
          <div className="text-sm font-semibold tracking-tight text-slate-900">{rec.title}</div>
          <p className="mt-1 text-sm leading-relaxed text-slate-600">{rec.description}</p>
        </div>
        {!approved ? (
          <Button variant="secondary" size="sm" onClick={onApprove} className="shrink-0">
            {rec.action === "auto" ? t("clinic.report.execute") : t("clinic.report.done")}
          </Button>
        ) : (
          <Badge tone="emerald" className="shrink-0">
            {t("clinic.report.done")}
          </Badge>
        )}
      </div>
    </div>
  );
}

export default function ClinicReportPage() {
  const { evalId } = useParams<{ evalId: string }>();
  const navigate = useNavigate();
  const { locale, t } = useI18n();

  const [evaluation, setEvaluation] = useState<EvaluationFull | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedDim, setExpandedDim] = useState<string | null>(null);
  const [approved, setApproved] = useState<Set<number>>(new Set());
  const [activeTab, setActiveTab] = useState<"overview" | "dimensions" | "recommendations">("overview");

  useEffect(() => {
    if (!evalId) return;
    clinicApi.getEvaluation(Number(evalId)).then(({ data }) => {
      setEvaluation(data);
      setLoading(false);
    });
  }, [evalId]);

  if (loading) {
    return <div className="app-page max-w-5xl text-sm text-slate-500">{t("clinic.report.loading")}</div>;
  }

  if (!evaluation) {
    return <div className="app-page max-w-5xl text-sm text-slate-500">{t("clinic.report.notFound")}</div>;
  }

  const radarData = DIM_META.map((dimension) => ({
    dim: locale === "zh" ? dimension.name_zh : dimension.name_en,
    score: evaluation.dimension_scores?.[dimension.id]?.score ?? 0,
    fullMark: 100,
  }));

  const recommendations = evaluation.recommendations ?? [];
  const criticalCount = recommendations.filter((item) => item.priority === "critical").length;
  const highCount = recommendations.filter((item) => item.priority === "high").length;

  return (
    <div className="app-page max-w-6xl">
      <Button variant="secondary" onClick={() => navigate("/clinic")} icon={<ArrowLeft className="h-4 w-4" />}>
        {t("clinic.report.back")}
      </Button>

      <div className="surface-card mt-6">
        <div className="flex flex-col gap-8 lg:flex-row lg:items-center">
          <ScoreRing score={evaluation.overall_score ?? 0} grade={evaluation.grade} />
          <div className="min-w-0 flex-1">
            <div className="section-kicker">{t("clinic.report.kicker")}</div>
            <h1 className="mt-3 text-3xl font-bold tracking-tight text-slate-900">{t("clinic.report.title", { id: evaluation.id })}</h1>
            <p className="mt-2 text-sm text-slate-500">
              {evaluation.completed_at
                ? t("clinic.report.completedAt", {
                    time: new Date(evaluation.completed_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US"),
                  })
                : t("clinic.report.inProgress")}
            </p>
            <div className="mt-4 flex flex-wrap gap-3 text-sm">
              {criticalCount > 0 ? <Badge tone="rose">{t("clinic.report.criticalIssues", { count: criticalCount })}</Badge> : null}
              {highCount > 0 ? <Badge tone="amber">{t("clinic.report.highPriority", { count: highCount })}</Badge> : null}
              <Badge tone="indigo">{t("clinic.report.recommendations", { count: recommendations.length })}</Badge>
              <AiAssistBadge aiAssist={evaluation.ai_assist} />
              {evaluation.langfuse_trace_url ? (
                <a
                  href={evaluation.langfuse_trace_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  {t("clinic.report.trace")}
                </a>
              ) : null}
            </div>
          </div>

          <div className="h-56 w-full max-w-xs shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData} outerRadius={72}>
                <PolarGrid stroke="#cbd5e1" />
                <PolarAngleAxis dataKey="dim" tick={{ fill: "#64748b", fontSize: 10 }} />
                <Radar dataKey="score" stroke="#4f46e5" fill="#4f46e5" fillOpacity={0.18} strokeWidth={1.75} />
                <Tooltip
                  contentStyle={{ backgroundColor: "#ffffff", border: "1px solid #e2e8f0", borderRadius: "12px", color: "#0f172a" }}
                  formatter={(value: number) => [`${Math.round(value)}`, t("clinic.report.score")]}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap gap-2">
        {(["overview", "dimensions", "recommendations"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`rounded-md px-4 py-2 text-sm font-medium transition ${
              activeTab === tab ? "border border-indigo-200 bg-indigo-50 text-indigo-700" : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            {tab === "overview"
              ? t("clinic.report.tab.overview")
              : tab === "dimensions"
                ? t("clinic.report.tab.dimensions")
                : t("clinic.report.tab.recommendations", { count: recommendations.length })}
          </button>
        ))}
      </div>

      {activeTab === "overview" ? (
        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {DIM_META.map((dimension) => {
            const dimensionResult = evaluation.dimension_scores?.[dimension.id];
            const score = dimensionResult?.score ?? 0;
            const trend = dimensionResult?.trend;
            const label = locale === "zh" ? dimension.name_zh : dimension.name_en;
            return (
              <div key={dimension.id} className="surface-card p-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</div>
                <div className="mt-3 text-3xl font-bold tracking-tight" style={{ color: scoreColor(score) }}>
                  {Math.round(score)}
                </div>
                {dimensionResult?.confidence != null ? (
                  <div className="mt-1 text-xs text-slate-500">
                    {t("clinic.report.confidence", { value: `${Math.round(dimensionResult.confidence * 100)}%` })}
                  </div>
                ) : null}
                <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                  <div className="h-full rounded-full" style={{ width: `${score}%`, backgroundColor: scoreColor(score) }} />
                </div>
                {trend ? <div className={`mt-2 text-xs font-medium ${TREND_TONE[trend] ?? "text-slate-500"}`}>{t(`clinic.trend.${trend}`)}</div> : null}
              </div>
            );
          })}
        </div>
      ) : null}

      {activeTab === "dimensions" ? (
        <div className="mt-6 space-y-3">
          {DIM_META.map((dimension) => {
            const score = evaluation.dimension_scores?.[dimension.id];
            if (!score) return null;
            return (
              <DimensionCard
                key={dimension.id}
                meta={dimension}
                score={score}
                expanded={expandedDim === dimension.id}
                onClick={() => setExpandedDim(expandedDim === dimension.id ? null : dimension.id)}
                locale={locale}
                t={t}
              />
            );
          })}
        </div>
      ) : null}

      {activeTab === "recommendations" ? (
        <div className="mt-6 space-y-3">
          {recommendations.length === 0 ? (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-10 text-center text-sm text-emerald-700">
              {t("clinic.report.empty")}
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>{t("clinic.report.remaining", { remaining: recommendations.length - approved.size, resolved: approved.size })}</span>
                {approved.size > 0 ? (
                  <button onClick={() => setApproved(new Set())} className="inline-flex items-center gap-1.5 font-medium text-slate-600 hover:text-slate-900">
                    <RotateCcw className="h-3.5 w-3.5" />
                    {t("clinic.report.reset")}
                  </button>
                ) : null}
              </div>
              {recommendations.map((recommendation, index) => (
                <RecommendationCard
                  key={index}
                  rec={recommendation}
                  approved={approved.has(index)}
                  onApprove={() => setApproved((prev) => new Set([...prev, index]))}
                  locale={locale}
                  t={t}
                />
              ))}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
