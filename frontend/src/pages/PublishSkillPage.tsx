import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  GitCommitHorizontal,
  PackagePlus,
  Plus,
  Sparkles,
  Tags,
  Trash2,
} from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  skillsApi,
  type PackageValidationResponse,
  type SkillGenerateDraftResponse,
  type SkillPackageTemplate,
} from "../api/client";
import { Badge, Button, Card, IconTile, Input, MonoPill, PageHeader } from "../components/ui";

type PackageFileEntry = {
  path: string;
  content: string;
};

const DEFAULT_VALIDATION_YAML = `version: 1
runner: openclaw-docker
load:
  skill_name: my_skill
checks:
  - type: file_exists
    path: SKILL.md
  - type: file_exists
    path: README.md
smoke_prompts:
  - message: Use the skill if appropriate for a simple smoke test.
`;

function buildDefaultPackage(skillName: string) {
  const normalized = skillName || "my_skill";
  return {
    "SKILL.md": `---
name: ${normalized}
version: 1.0.0
description: A short description of this skill
author: your-name
tags: productivity, writing
---

# ${normalized.replace(/[_-]/g, " ").replace(/\b\w/g, (value) => value.toUpperCase())}

Describe what this skill does and when to use it.

## Usage

Explain how to invoke this skill.
`,
    "README.md": `# ${normalized}

Describe the purpose of this skill package, any limitations, and how to use it in OpenClaw.
`,
    "examples/quickstart.md": `# Quickstart

> Use the \`${normalized}\` skill to help with this task.
`,
    ".duckdock/validation.yaml": DEFAULT_VALIDATION_YAML.replace("my_skill", normalized),
  } satisfies Record<string, string>;
}

function toEntries(files: Record<string, string>): PackageFileEntry[] {
  return Object.entries(files).map(([path, content]) => ({ path, content }));
}

function sortEntries(entries: PackageFileEntry[]) {
  return [...entries].sort((left, right) => {
    if (left.path === "SKILL.md") return -1;
    if (right.path === "SKILL.md") return 1;
    return left.path.localeCompare(right.path);
  });
}

function splitTags(value: string) {
  return value
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

function normalizeUploadedPath(rawPath: string) {
  const normalized = rawPath.replace(/\\/g, "/").replace(/^\/+/, "");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length > 1) {
    return parts.slice(1).join("/");
  }
  return parts[0] ?? normalized;
}

export default function PublishSkillPage() {
  const { ns, skill } = useParams<{ ns: string; skill: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const generatedDraft = (location.state as { generatedDraft?: SkillGenerateDraftResponse } | null)?.generatedDraft ?? null;
  const initialPackage = useMemo(
    () => generatedDraft?.package_files ?? buildDefaultPackage(skill ?? "my_skill"),
    [generatedDraft, skill]
  );
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  const [form, setForm] = useState({
    tag: generatedDraft?.tag ?? "",
    packageFiles: sortEntries(toEntries(initialPackage)),
    message: generatedDraft?.changelog ? `chore: publish ${generatedDraft.tag}` : "",
    changelog: generatedDraft?.changelog ?? "",
    publishTags: (generatedDraft?.publish_tags ?? ["latest"]).join(", "),
    share_public: false,
    licenseName: "",
    licenseAttested: false,
    riskAcknowledged: false,
    publicExpiresInDays: 30,
    templateKey: "",
  });
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [templates, setTemplates] = useState<SkillPackageTemplate[]>([]);
  const [packageValidation, setPackageValidation] = useState<PackageValidationResponse | null>(null);
  const [validatingPackage, setValidatingPackage] = useState(false);

  useEffect(() => {
    if (!generatedDraft) {
      return;
    }
    setForm((current) => ({
      ...current,
      tag: generatedDraft.tag,
      packageFiles: sortEntries(toEntries(generatedDraft.package_files)),
      message: current.message || `chore: publish ${generatedDraft.tag}`,
      changelog: generatedDraft.changelog ?? current.changelog,
      publishTags: (generatedDraft.publish_tags ?? ["latest"]).join(", "),
    }));
  }, [generatedDraft]);

  useEffect(() => {
    skillsApi.listPackageTemplates().then(({ data }) => setTemplates(data)).catch(() => setTemplates([]));
  }, []);

  function updateFile(index: number, patch: Partial<PackageFileEntry>) {
    setForm((current) => ({
      ...current,
      packageFiles: current.packageFiles.map((entry, entryIndex) =>
        entryIndex === index ? { ...entry, ...patch } : entry
      ),
    }));
  }

  function addFile() {
    setForm((current) => ({
      ...current,
      packageFiles: [...current.packageFiles, { path: "", content: "" }],
    }));
  }

  function removeFile(index: number) {
    setForm((current) => ({
      ...current,
      packageFiles: current.packageFiles.filter((_, entryIndex) => entryIndex !== index),
    }));
  }

  function applyTemplate() {
    if (!form.templateKey) {
      return;
    }
    const selected = templates.find((item) => item.key === form.templateKey);
    if (!selected) {
      return;
    }
    setForm((current) => ({
      ...current,
      packageFiles: sortEntries(
        toEntries(
          Object.fromEntries(
              Object.entries(selected.package_files).map(([path, content]) => [
              path,
              path === "SKILL.md"
                ? content
                    .replace("name: my_skill", `name: ${skill ?? "my_skill"}`)
                    .replace("version: 1.0.0", `version: ${(form.tag || "v1.0.0").replace(/^v/, "")}`)
                : content,
            ])
          )
        )
      ),
    }));
    setPackageValidation(null);
  }

  async function handleFolderUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const fileList = Array.from(event.target.files ?? []);
    if (!fileList.length) {
      return;
    }
    const pairs = await Promise.all(
      fileList.map(async (file) => {
        const content = await file.text();
        return [normalizeUploadedPath((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name), content] as const;
      })
    );
    setForm((current) => ({
      ...current,
      packageFiles: sortEntries(toEntries(Object.fromEntries(pairs))),
    }));
    setPackageValidation(null);
  }

  async function validatePackage() {
    setValidatingPackage(true);
    setErrors([]);
    try {
      const packageFiles = Object.fromEntries(
        form.packageFiles
          .map((entry) => [entry.path.trim(), entry.content] as const)
          .filter(([path]) => Boolean(path))
      );
      const { data } = await skillsApi.validatePackage({
        package_files: packageFiles,
        expected_tag: form.tag || undefined,
      });
      setPackageValidation(data);
      if (!data.ok) {
        setErrors(data.errors);
      }
    } catch (err: any) {
      setErrors([err.response?.data?.detail ?? "Package validation failed"]);
    } finally {
      setValidatingPackage(false);
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setErrors([]);

    const packageFiles = Object.fromEntries(
      form.packageFiles
        .map((entry) => [entry.path.trim(), entry.content] as const)
        .filter(([path]) => Boolean(path))
    );

    if (!packageFiles["SKILL.md"]) {
      setErrors(["SKILL.md is required at the package root."]);
      return;
    }

    setLoading(true);
    try {
      await skillsApi.publishVersion(ns!, skill!, {
        tag: form.tag,
        package_files: packageFiles,
        message: form.message || undefined,
        changelog: form.changelog || undefined,
        publish_tags: splitTags(form.publishTags),
        share_public: form.share_public,
        license_name: form.share_public ? form.licenseName || undefined : undefined,
        license_attested: form.share_public ? form.licenseAttested : undefined,
        risk_acknowledged: form.share_public ? form.riskAcknowledged : undefined,
        public_expires_in_days: form.share_public ? form.publicExpiresInDays || null : undefined,
      });
      navigate(`/namespaces/${ns}/${skill}`);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      if (detail?.errors) {
        setErrors(detail.errors);
      } else if (Array.isArray(detail)) {
        setErrors(detail.map((entry: any) => entry.msg));
      } else {
        setErrors([typeof detail === "string" ? detail : "Publish failed"]);
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-page max-w-[1180px]">
      <Button
        variant="secondary"
        size="sm"
        icon={<ArrowLeft className="h-4 w-4" />}
        onClick={() => navigate(`/namespaces/${ns}/${skill}`)}
      >
        Back to skill
      </Button>

      <PageHeader
        eyebrow="Release"
        title="Publish version"
        description={
          <>
            Prepare a package-first release for <MonoPill>{skill}</MonoPill> inside <MonoPill>{ns}</MonoPill>.
          </>
        }
      />

      <form onSubmit={handleSubmit} className="mt-2 space-y-6">
        {generatedDraft ? (
          <Card padded className="space-y-4">
            <div className="flex items-center gap-3 text-slate-900">
              <IconTile size="sm" tone="indigo">
                <Sparkles className="h-4 w-4" />
              </IconTile>
              <div>
                <div className="text-sm font-semibold tracking-tight">AI package draft generated</div>
                <div className="text-xs text-slate-500">
                  Model: <MonoPill>{generatedDraft.model}</MonoPill>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Badge
                tone={
                  generatedDraft.validation_status === "passed"
                    ? "emerald"
                    : generatedDraft.validation_status === "warned"
                      ? "amber"
                      : generatedDraft.validation_status === "failed"
                        ? "rose"
                        : "indigo"
                }
              >
                validation:{generatedDraft.validation_status}
              </Badge>
              <MonoPill>critical:{generatedDraft.critical_count}</MonoPill>
              <MonoPill>high:{generatedDraft.high_count}</MonoPill>
              <MonoPill>medium:{generatedDraft.medium_count}</MonoPill>
              <MonoPill>low:{generatedDraft.low_count}</MonoPill>
              <Badge tone={generatedDraft.ready_to_publish ? "emerald" : "rose"}>
                {generatedDraft.ready_to_publish ? "ready to publish" : "needs review"}
              </Badge>
            </div>

            {generatedDraft.validation_errors.length > 0 ? (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3">
                <div className="text-sm font-medium text-rose-700">Package validation errors</div>
                <ul className="mt-2 space-y-1">
                  {generatedDraft.validation_errors.map((error) => (
                    <li key={error} className="text-xs text-rose-700">
                      - {error}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </Card>
        ) : null}

        {errors.length > 0 ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-4">
            <div className="text-sm font-medium text-rose-700">Validation errors</div>
            <ul className="mt-2 space-y-1">
              {errors.map((error, index) => (
                <li key={index} className="flex items-start gap-2 text-xs text-rose-700">
                  <span className="mt-0.5 shrink-0">-</span>
                  {error}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
          <div className="space-y-6">
            <Card padded className="space-y-4">
              <div className="flex items-center gap-3 text-slate-900">
                <IconTile size="sm" tone="indigo">
                  <PackagePlus className="h-4 w-4" />
                </IconTile>
                <h2 className="table-label">Package files</h2>
              </div>
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
                <select
                  value={form.templateKey}
                  onChange={(event) => setForm((current) => ({ ...current, templateKey: event.target.value }))}
                  className="input-control"
                >
                  <option value="">Select a package template</option>
                  {templates.map((template) => (
                    <option key={template.key} value={template.key}>
                      {template.name}
                    </option>
                  ))}
                </select>
                <Button type="button" variant="secondary" className="whitespace-nowrap" onClick={applyTemplate}>
                  Apply template
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  className="whitespace-nowrap"
                  onClick={() => folderInputRef.current?.click()}
                >
                  Upload folder
                </Button>
                <input
                  ref={folderInputRef}
                  type="file"
                  multiple
                  onChange={handleFolderUpload}
                  className="hidden"
                  {...({ webkitdirectory: "true", directory: "" } as any)}
                />
              </div>
              <div className="space-y-4">
                {form.packageFiles.map((entry, index) => (
                  <div key={`${entry.path}-${index}`} className="overflow-hidden rounded-lg border border-slate-200">
                    <div className="flex items-center gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3">
                      <Input
                        value={entry.path}
                        onChange={(event) => updateFile(index, { path: event.target.value })}
                        placeholder="SKILL.md"
                        className="h-10 flex-1 font-mono text-xs"
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        danger
                        icon={<Trash2 className="h-4 w-4" />}
                        onClick={() => removeFile(index)}
                        disabled={form.packageFiles.length === 1}
                        className="px-3 py-2"
                      />
                    </div>
                    <textarea
                      value={entry.content}
                      onChange={(event) => updateFile(index, { content: event.target.value })}
                      rows={entry.path === "SKILL.md" ? 16 : 10}
                      className="min-h-[12rem] w-full resize-y bg-slate-50 px-4 py-4 font-mono text-xs leading-relaxed text-slate-800 focus:outline-none"
                      spellCheck={false}
                    />
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap gap-3">
                <Button type="button" variant="secondary" icon={<Plus className="h-4 w-4" />} onClick={addFile}>
                  Add file
                </Button>
                <Button type="button" variant="secondary" onClick={validatePackage} disabled={validatingPackage}>
                  {validatingPackage ? "Validating..." : "Validate package"}
                </Button>
              </div>
              {packageValidation ? (
                <div className={`rounded-lg border px-4 py-4 text-sm ${packageValidation.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
                  <div className="font-medium">
                    {packageValidation.ok ? "Package validation passed" : "Package validation found issues"}
                  </div>
                  <div className="mt-2 text-xs">
                    files:{packageValidation.file_count} examples:{packageValidation.example_file_count} validation.yaml:
                    {packageValidation.validation_spec_present ? "yes" : "no"}
                  </div>
                  {packageValidation.warnings.length ? (
                    <ul className="mt-2 space-y-1 text-xs">
                      {packageValidation.warnings.map((warning) => (
                        <li key={warning}>- {warning}</li>
                      ))}
                    </ul>
                  ) : null}
                  {packageValidation.errors.length ? (
                    <ul className="mt-2 space-y-1 text-xs">
                      {packageValidation.errors.map((error) => (
                        <li key={error}>- {error}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
            </Card>
          </div>

          <div className="space-y-6">
            <Card padded className="space-y-4">
              <div className="flex items-center gap-3 text-slate-900">
                <IconTile size="sm" tone="indigo">
                  <PackagePlus className="h-4 w-4" />
                </IconTile>
                <h2 className="table-label">Release metadata</h2>
              </div>
              <div className="space-y-1.5">
                <label className="table-label">Version tag</label>
                <Input
                  required
                  value={form.tag}
                  onChange={(event) => setForm({ ...form, tag: event.target.value })}
                  placeholder="v1.0.0"
                  className="font-mono"
                />
              </div>
              <div className="space-y-1.5">
                <label className="table-label">
                  Commit message <span className="text-slate-400">(optional)</span>
                </label>
                <Input
                  icon={<GitCommitHorizontal className="h-4 w-4" />}
                  value={form.message}
                  onChange={(event) => setForm({ ...form, message: event.target.value })}
                  placeholder={`chore: publish ${form.tag || "v1.0.0"}`}
                />
              </div>
              <div className="space-y-1.5">
                <label className="table-label">Changelog</label>
                <textarea
                  value={form.changelog}
                  onChange={(event) => setForm({ ...form, changelog: event.target.value })}
                  rows={5}
                  className="input-control min-h-[8rem] resize-y font-mono text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <label className="table-label flex items-center gap-2">
                  <Tags className="h-3.5 w-3.5" />
                  Publish tags
                </label>
                <Input
                  value={form.publishTags}
                  onChange={(event) => setForm({ ...form, publishTags: event.target.value })}
                  placeholder="latest, stable"
                  className="font-mono text-xs"
                />
              </div>
              <label className="flex items-start gap-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-4">
                <input
                  type="checkbox"
                  checked={form.share_public}
                  onChange={(event) => setForm({ ...form, share_public: event.target.checked })}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                />
                <div>
                  <div className="text-sm font-medium tracking-tight text-slate-900">Share this version to the public/common area</div>
                  <div className="mt-1 text-xs leading-relaxed text-slate-500">
                    Anonymous users can fetch this package through the ClawHub-compatible registry once verification promotes it to{" "}
                    <MonoPill>production</MonoPill>.
                  </div>
                </div>
              </label>
              {form.share_public ? (
                <div className="space-y-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-1.5">
                      <label className="table-label">License</label>
                      <Input
                        value={form.licenseName}
                        onChange={(event) => setForm({ ...form, licenseName: event.target.value })}
                        placeholder="MIT / Apache-2.0 / Proprietary-Internal"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <label className="table-label">Public expiry (days)</label>
                      <Input
                        type="number"
                        value={form.publicExpiresInDays}
                        onChange={(event) => setForm({ ...form, publicExpiresInDays: Number(event.target.value) })}
                      />
                    </div>
                  </div>
                  <label className="flex items-start gap-3 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={form.licenseAttested}
                      onChange={(event) => setForm({ ...form, licenseAttested: event.target.checked })}
                      className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                    />
                    I confirm this package can be shared under the selected license.
                  </label>
                  <label className="flex items-start gap-3 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={form.riskAcknowledged}
                      onChange={(event) => setForm({ ...form, riskAcknowledged: event.target.checked })}
                      className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                    />
                    I acknowledge the operational and compliance risk of public/common distribution.
                  </label>
                </div>
              ) : null}
            </Card>

            <Card title="Package requirements">
              <div className="space-y-3 px-5 py-4 text-xs text-slate-600 sm:px-6">
                <div>- <MonoPill>SKILL.md</MonoPill> is required at the package root.</div>
                <div>
                  - Additional files such as <MonoPill>README.md</MonoPill>, <MonoPill>examples/*</MonoPill>, and{" "}
                  <MonoPill>.duckdock/validation.yaml</MonoPill> are recommended.
                </div>
                <div>- Published metadata is stored separately from package files, closer to ClawHub semantics.</div>
              </div>
            </Card>
          </div>
        </div>

        <div className="flex gap-3">
          <Button type="button" variant="secondary" onClick={() => navigate(`/namespaces/${ns}/${skill}`)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={loading}>
            {loading ? "Publishing..." : "Publish Version"}
          </Button>
        </div>
      </form>
    </div>
  );
}
