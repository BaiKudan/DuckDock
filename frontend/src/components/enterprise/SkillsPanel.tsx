import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, Pencil, Plus, RotateCcw, Search, Sparkles, Trash2 } from "lucide-react";
import { useNavigate } from "../../router";
import { skillsApi, type Skill, type SkillGenerateDraftResponse, type SkillPackageTemplate } from "../../api/client";
import { useI18n } from "../../i18n";
import type { Locale } from "../../store/i18n";
import { Panel, TextField } from "./Panel";

type GeneratedDraftNavigationState = {
  generatedDraft: SkillGenerateDraftResponse;
};

const AI_SKILL_NAME_RE = /^[a-zA-Z0-9_-]{1,128}$/;
const AI_TAG_RE = /^v\d+\.\d+\.\d+$/;

function getAiCopy(locale: Locale) {
  if (locale === "zh") {
    return {
      title: "AI 生成技能",
      subtitle: "使用 Qwen 生成新的 skill 草稿，并在发布前完成预检。",
      skillName: "技能名称",
      description: "描述",
      versionTag: "版本号",
      requirements: "需求说明",
      requirementsPlaceholder: "描述技能要做什么、输入输出、工作流、边界限制、示例和使用场景。",
      generating: "生成中...",
      generate: "生成草稿",
      invalidName: "技能名称必填，且只能包含字母、数字、下划线或短横线。",
      invalidTag: "版本号必须是带 v 前缀的语义化版本，例如 v1.0.0。",
      invalidPrompt: "需求说明至少需要 10 个字符。",
      failed: "AI 生成功能执行失败",
      fieldNames: {
        name: "技能名称",
        description: "描述",
        tag: "版本号",
        prompt: "需求说明",
      },
    };
  }

  return {
    title: "Generate with AI",
    subtitle: "Use Qwen to draft a new skill package and pre-validate it before publishing.",
    skillName: "Skill Name",
    description: "Description",
    versionTag: "Version Tag",
    requirements: "Requirements",
    requirementsPlaceholder:
      "Describe what the skill should do, expected inputs/outputs, workflow, guardrails, examples, and usage context.",
    generating: "Generating...",
    generate: "Generate Draft",
    invalidName: "Skill name is required and may only contain letters, numbers, underscores, or hyphens.",
    invalidTag: "Version tag must be semver with a v prefix, for example v1.0.0.",
    invalidPrompt: "Requirements must be at least 10 characters.",
    failed: "AI generation failed",
    fieldNames: {
      name: "Skill Name",
      description: "Description",
      tag: "Version Tag",
      prompt: "Requirements",
    },
  };
}

function getAiValidationError(
  form: {
    name: string;
    description: string;
    tag: string;
    prompt: string;
  },
  locale: Locale
) {
  const copy = getAiCopy(locale);
  if (!AI_SKILL_NAME_RE.test(form.name.trim())) {
    return copy.invalidName;
  }
  if (!AI_TAG_RE.test(form.tag.trim())) {
    return copy.invalidTag;
  }
  if (form.prompt.trim().length < 10) {
    return copy.invalidPrompt;
  }
  return "";
}

function formatGenerateDraftError(detail: unknown, locale: Locale) {
  const copy = getAiCopy(locale);
  const fieldNames = copy.fieldNames;

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== "object") {
          return null;
        }
        const record = item as { loc?: unknown; msg?: unknown };
        const loc = Array.isArray(record.loc) ? record.loc : [];
        const field = typeof loc[loc.length - 1] === "string" ? loc[loc.length - 1] : "";
        const label = field && field in fieldNames ? fieldNames[field as keyof typeof fieldNames] : field;
        const message = typeof record.msg === "string" ? record.msg : "";
        if (label && message) {
          return `${label}: ${message}`;
        }
        return message || null;
      })
      .filter((value): value is string => Boolean(value));
    if (messages.length) {
      return messages.join(" ");
    }
  }

  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) {
      return message;
    }
  }

  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }

  return copy.failed;
}

function SkillManageModal({
  ns,
  skill,
  mode,
  onClose,
  onUpdated,
  onDeleted,
  onPurged,
}: {
  ns: string;
  skill: Skill;
  mode: "edit" | "delete" | "purge";
  onClose: () => void;
  onUpdated: (value: Skill) => void;
  onDeleted: () => void;
  onPurged: () => void;
}) {
  const { t } = useI18n();
  const [description, setDescription] = useState(skill.description ?? "");
  const [confirmName, setConfirmName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "edit") {
        const { data } = await skillsApi.update(ns, skill.name, {
          description: description.trim() || null,
        });
        onUpdated(data);
      } else if (mode === "delete") {
        if (confirmName !== skill.name) {
          setError(t("skills.manage.confirmDelete"));
          return;
        }
        await skillsApi.delete(ns, skill.name);
        onDeleted();
      } else {
        if (confirmName !== skill.name) {
          setError(t("skills.manage.confirmPurge"));
          return;
        }
        await skillsApi.purge(ns, skill.name);
        onPurged();
      }
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      setError(
        Array.isArray(detail)
          ? detail[0]?.msg
          : detail ??
              (mode === "edit"
                ? t("skills.manage.updateFailed")
                : mode === "delete"
                  ? t("skills.manage.deleteFailed")
                  : t("skills.manage.purgeFailed"))
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/10 px-4 backdrop-blur-sm">
      <div className="surface-card w-full max-w-lg p-6">
        <h2 className="text-xl font-semibold tracking-tight text-slate-900">
          {mode === "edit"
            ? t("skills.manage.updateTitle")
            : mode === "delete"
              ? t("skills.manage.deleteTitle")
              : t("skills.manage.purgeTitle")}
        </h2>
        {error ? <div className="soft-rose mt-4 rounded-lg px-4 py-3 text-sm">{error}</div> : null}
        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
              {t("field.name")}
            </label>
            <input value={skill.name} disabled className="input-control bg-slate-100 text-slate-500" />
            <p className="mt-2 text-xs text-slate-500">{t("skills.manage.nameImmutable")}</p>
          </div>
          {mode === "edit" ? (
            <TextField label={t("field.description")} value={description} onChange={setDescription} />
          ) : (
            <div>
              <p className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-700">
                {mode === "delete" ? t("skills.manage.deleteMessage") : t("skills.manage.purgeMessage")}
              </p>
              <label className="mt-4 block">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
                  {mode === "delete" ? t("skills.manage.confirmDelete") : t("skills.manage.confirmPurge")}
                </div>
                <input value={confirmName} onChange={(event) => setConfirmName(event.target.value)} className="input-control" />
              </label>
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="button-secondary">
              {t("common.cancel")}
            </button>
            <button type="submit" disabled={loading} className={mode === "edit" ? "button-primary" : "rounded-md bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-500 disabled:opacity-50"}>
              {loading
                ? t("common.loading")
                : mode === "edit"
                  ? t("common.save")
                  : mode === "delete"
                    ? t("common.delete")
                    : t("common.purge")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function SkillsPanel({
  ns,
  skills,
  deletedSkills,
  query,
  onQueryChange,
  onSkillsChange,
  onDeletedSkillsChange,
}: {
  ns: string;
  skills: Skill[];
  deletedSkills: Skill[];
  query: string;
  onQueryChange: (value: string) => void;
  onSkillsChange: (value: Skill[]) => void;
  onDeletedSkillsChange: (value: Skill[]) => void;
}) {
  const navigate = useNavigate();
  const { locale, t } = useI18n();
  const [form, setForm] = useState({ name: "", description: "" });
  const [aiForm, setAiForm] = useState({
    name: "",
    description: "",
    tag: "v1.0.0",
    prompt: "",
    templateKey: "",
  });
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [aiError, setAiError] = useState("");
  const [templates, setTemplates] = useState<SkillPackageTemplate[]>([]);
  const [manageTarget, setManageTarget] = useState<Skill | null>(null);
  const [manageMode, setManageMode] = useState<"edit" | "delete" | "purge" | null>(null);
  const [restoringSkill, setRestoringSkill] = useState<string | null>(null);
  const aiCopy = useMemo(() => getAiCopy(locale), [locale]);
  useEffect(() => {
    skillsApi.listPackageTemplates().then(({ data }) => setTemplates(data)).catch(() => setTemplates([]));
  }, []);
  const visibleSkills = useMemo(() => {
    const lower = query.trim().toLowerCase();
    if (!lower) {
      return skills;
    }
    return skills.filter(
      (skill) =>
        skill.name.toLowerCase().includes(lower) ||
        skill.description?.toLowerCase().includes(lower)
    );
  }, [query, skills]);

  async function createSkill(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    try {
      const { data } = await skillsApi.create(ns, form);
      onSkillsChange([...skills, data]);
      setForm({ name: "", description: "" });
    } finally {
      setCreating(false);
    }
  }

  async function generateSkillDraft(event: React.FormEvent) {
    event.preventDefault();
    setAiError("");
    const validationError = getAiValidationError(aiForm, locale);
    if (validationError) {
      setAiError(validationError);
      return;
    }
    setGenerating(true);
    try {
      const { data } = await skillsApi.generateDraft(ns, {
        name: aiForm.name.trim(),
        description: aiForm.description.trim() || undefined,
        tag: aiForm.tag.trim(),
        prompt: aiForm.prompt.trim(),
        create_if_missing: true,
        template_key: aiForm.templateKey || undefined,
      });

      if (data.created_skill && !skills.some((item) => item.name === data.skill_name)) {
        const refreshed = await skillsApi.list(ns);
        onSkillsChange(refreshed.data);
      }

      navigate(`/namespaces/${ns}/${data.skill_name}/publish`, {
        state: { generatedDraft: data } satisfies GeneratedDraftNavigationState,
      });
    } catch (err: any) {
      setAiError(formatGenerateDraftError(err.response?.data?.detail, locale));
    } finally {
      setGenerating(false);
    }
  }

  async function restoreSkill(skillName: string) {
    setRestoringSkill(skillName);
    try {
      const { data } = await skillsApi.restore(ns, skillName);
      onSkillsChange([...skills, data].sort((a, b) => a.name.localeCompare(b.name)));
      onDeletedSkillsChange(deletedSkills.filter((item) => item.name !== skillName));
    } finally {
      setRestoringSkill(null);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(22rem,1fr)]">
      {manageTarget && manageMode ? (
        <SkillManageModal
          ns={ns}
          skill={manageTarget}
          mode={manageMode}
          onClose={() => {
            setManageTarget(null);
            setManageMode(null);
          }}
          onUpdated={(value) => {
            onSkillsChange(skills.map((item) => (item.id === value.id ? value : item)));
            setManageTarget(null);
            setManageMode(null);
          }}
          onDeleted={() => {
            onSkillsChange(skills.filter((item) => item.id !== manageTarget.id));
            onDeletedSkillsChange([
              ...deletedSkills,
              { ...manageTarget, deleted_at: new Date().toISOString() },
            ].sort((a, b) => a.name.localeCompare(b.name)));
            setManageTarget(null);
            setManageMode(null);
          }}
          onPurged={() => {
            onDeletedSkillsChange(deletedSkills.filter((item) => item.id !== manageTarget.id));
            setManageTarget(null);
            setManageMode(null);
          }}
        />
      ) : null}

      <Panel
        title={t("skills.inventory.title")}
        subtitle={t("skills.inventory.subtitle")}
        actions={
          <div className="relative w-full md:w-72">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              placeholder={t("skills.search")}
              className="input-control w-full pl-9"
            />
          </div>
        }
      >
        <div className="space-y-2">
          {visibleSkills.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
              {t("skills.empty")}
            </div>
          ) : (
            visibleSkills.map((skill) => (
              <button
                key={skill.id}
                onClick={() => navigate(`/namespaces/${ns}/${skill.name}`)}
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-4 text-left transition hover:border-indigo-200 hover:bg-slate-50"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-semibold tracking-tight text-slate-900">{skill.name}</span>
                      <ArrowUpRight className="h-4 w-4 text-slate-300" />
                    </div>
                    <div className="mt-1 text-sm text-slate-500">{skill.description ?? t("common.noDescription")}</div>
                  </div>
                  <div className="text-right text-xs text-slate-500">
                    <div>{t("skills.versions", { count: skill.version_count })}</div>
                    <div className="mt-2">
                      <span className="mono-pill">{skill.latest_tag ?? t("skills.noRelease")}</span>
                    </div>
                    <div className="mt-3 flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setManageTarget(skill);
                          setManageMode("edit");
                        }}
                        className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-900"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        {t("skills.manage.edit")}
                      </button>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setManageTarget(skill);
                          setManageMode("delete");
                        }}
                        className="inline-flex items-center gap-1 text-xs font-medium text-rose-600 hover:text-rose-500"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        {t("skills.manage.delete")}
                      </button>
                    </div>
                  </div>
                </div>
              </button>
            ))
          )}
        </div>
        {deletedSkills.length ? (
          <div className="mt-6 border-t border-slate-200 pt-6">
            <div className="mb-3 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
              {t("skills.deleted.title")}
            </div>
            <div className="space-y-2">
              {deletedSkills.map((skill) => (
                <div
                  key={skill.id}
                  className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-4 md:flex-row md:items-center md:justify-between"
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-semibold tracking-tight text-slate-900">{skill.name}</span>
                      <span className="mono-pill">{t("common.deleted")}</span>
                    </div>
                    <div className="mt-1 text-sm text-slate-500">{skill.description ?? t("common.noDescription")}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => restoreSkill(skill.name)}
                      disabled={restoringSkill === skill.name}
                      className="button-secondary inline-flex items-center gap-2"
                    >
                      <RotateCcw className="h-4 w-4" />
                      {restoringSkill === skill.name ? t("common.loading") : t("common.restore")}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setManageTarget(skill);
                        setManageMode("purge");
                      }}
                      className="inline-flex items-center gap-2 rounded-md border border-rose-200 px-3 py-2 text-sm font-medium text-rose-700 transition hover:bg-rose-50"
                    >
                      <Trash2 className="h-4 w-4" />
                      {t("common.purge")}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </Panel>

      <div className="space-y-6">
        <Panel title={aiCopy.title} subtitle={aiCopy.subtitle}>
          <form onSubmit={generateSkillDraft} className="space-y-4">
            <TextField
              label={aiCopy.skillName}
              value={aiForm.name}
              onChange={(value) => {
                setAiError("");
                setAiForm((current) => ({ ...current, name: value }));
              }}
            />
            <TextField
              label={aiCopy.description}
              value={aiForm.description}
              onChange={(value) => {
                setAiError("");
                setAiForm((current) => ({ ...current, description: value }));
              }}
            />
            <TextField
              label={aiCopy.versionTag}
              value={aiForm.tag}
              onChange={(value) => {
                setAiError("");
                setAiForm((current) => ({ ...current, tag: value }));
              }}
            />
            <label className="block">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Template</div>
              <select
                value={aiForm.templateKey}
                onChange={(event) => {
                  setAiError("");
                  setAiForm((current) => ({ ...current, templateKey: event.target.value }));
                }}
                className="input-control"
              >
                <option value="">Default</option>
                {templates.map((template) => (
                  <option key={template.key} value={template.key}>
                    {template.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">{aiCopy.requirements}</div>
              <textarea
                value={aiForm.prompt}
                onChange={(event) => {
                  setAiError("");
                  setAiForm((current) => ({ ...current, prompt: event.target.value }));
                }}
                rows={8}
                className="input-control min-h-40 resize-y"
                placeholder={aiCopy.requirementsPlaceholder}
              />
            </label>
            {aiError ? <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{aiError}</div> : null}
            <button type="submit" disabled={generating} className="button-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50">
              <Sparkles className="h-4 w-4" />
              {generating ? aiCopy.generating : aiCopy.generate}
            </button>
          </form>
        </Panel>

        <Panel title={t("skills.create.title")} subtitle={t("skills.create.subtitle")}>
          <form onSubmit={createSkill} className="space-y-4">
            <TextField label={t("field.name")} value={form.name} onChange={(value) => setForm((current) => ({ ...current, name: value }))} />
            <TextField
              label={t("field.description")}
              value={form.description}
              onChange={(value) => setForm((current) => ({ ...current, description: value }))}
            />
            <button type="submit" disabled={creating} className="button-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50">
              <Plus className="h-4 w-4" />
              {creating ? t("skills.create.creating") : t("skills.create.button")}
            </button>
          </form>
        </Panel>
      </div>
    </div>
  );
}
