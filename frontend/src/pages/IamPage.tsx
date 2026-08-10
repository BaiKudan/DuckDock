import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import {
  CheckCircle2,
  Eye,
  KeyRound,
  Link2,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import {
  iamApi,
  namespacesApi,
  type DirectoryCredential,
  type DirectoryLifecycleEvent,
  type IdentityLink,
  type Namespace,
  type OrgUnit,
  type Role,
  type SSOProviderConfig,
  type SSORoleMapping,
  type SSORoleMappingCreate,
  type SSORoleMappingUpdate,
  type UserProfile,
} from "../api/client";
import { EmptyState, MetricCard, PageHeader, SectionCard } from "../components/PageShell";
import { Badge, Button, Input, MonoPill, type Tone } from "../components/ui";

interface MappingDraft {
  provider_id: string;
  claim_name: string;
  claim_value: string;
  role_id: string;
  namespace_id: string;
  org_unit_id: string;
  enabled: boolean;
  priority: string;
  description: string;
}

const selectClass =
  "w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition-colors focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400";

const emptyDraft: MappingDraft = {
  provider_id: "",
  claim_name: "groups",
  claim_value: "",
  role_id: "",
  namespace_id: "",
  org_unit_id: "",
  enabled: true,
  priority: "100",
  description: "",
};

const scopeTone: Record<Role["scope"], Tone> = {
  system: "indigo",
  org: "emerald",
  namespace: "violet",
};

function formatDate(value: string | null | undefined) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function providerName(providers: SSOProviderConfig[], id: number) {
  const provider = providers.find((item) => item.id === id);
  return provider ? `${provider.name} · ${provider.provider_type.toUpperCase()}` : `Provider #${id}`;
}

function roleName(roles: Role[], id: number) {
  const role = roles.find((item) => item.id === id);
  return role ? `${role.name} (${role.key})` : `Role #${id}`;
}

function userName(users: UserProfile[], id: number) {
  const user = users.find((item) => item.id === id);
  return user ? `${user.username}${user.enterprise_uid ? ` · ${user.enterprise_uid}` : ""}` : `User #${id}`;
}

function namespaceName(namespaces: Namespace[], id: number | null) {
  if (!id) return "-";
  const namespace = namespaces.find((item) => item.id === id);
  return namespace?.name ?? `Namespace #${id}`;
}

function orgName(orgUnits: OrgUnit[], id: number | null) {
  if (!id) return "-";
  const org = orgUnits.find((item) => item.id === id);
  return org ? `${org.name}${org.code ? ` · ${org.code}` : ""}` : `Org #${id}`;
}

function mappingToDraft(mapping: SSORoleMapping): MappingDraft {
  return {
    provider_id: String(mapping.provider_id),
    claim_name: mapping.claim_name,
    claim_value: mapping.claim_value,
    role_id: String(mapping.role_id),
    namespace_id: mapping.namespace_id ? String(mapping.namespace_id) : "",
    org_unit_id: mapping.org_unit_id ? String(mapping.org_unit_id) : "",
    enabled: mapping.enabled,
    priority: String(mapping.priority),
    description: mapping.description ?? "",
  };
}

function targetLabel(mapping: SSORoleMapping, roles: Role[], namespaces: Namespace[], orgUnits: OrgUnit[]) {
  const role = roles.find((item) => item.id === mapping.role_id);
  if (role?.scope === "namespace") return namespaceName(namespaces, mapping.namespace_id);
  if (role?.scope === "org") return orgName(orgUnits, mapping.org_unit_id);
  return "全局";
}

export default function IamPage() {
  const { message, modal } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [providerFilter, setProviderFilter] = useState<string>("");
  const [draftOpen, setDraftOpen] = useState(false);
  const [editing, setEditing] = useState<SSORoleMapping | null>(null);
  const [draft, setDraft] = useState<MappingDraft>(emptyDraft);
  const [saving, setSaving] = useState(false);
  const [issuingDirectoryCredential, setIssuingDirectoryCredential] = useState(false);

  const providers = useQuery({
    queryKey: ["iam", "sso-providers"],
    queryFn: async () => (await iamApi.listSSOProviderConfigs()).data,
  });
  const roles = useQuery({
    queryKey: ["iam", "roles"],
    queryFn: async () => (await iamApi.listRoles()).data,
  });
  const users = useQuery({
    queryKey: ["iam", "users"],
    queryFn: async () => (await iamApi.listUsers()).data,
  });
  const orgUnits = useQuery({
    queryKey: ["iam", "org-units"],
    queryFn: async () => (await iamApi.listOrgUnits()).data,
  });
  const namespaces = useQuery({
    queryKey: ["iam", "namespaces"],
    queryFn: async () => (await namespacesApi.list()).data,
  });
  const mappings = useQuery({
    queryKey: ["iam", "sso-role-mappings", providerFilter],
    queryFn: async () =>
      (await iamApi.listSSORoleMappings(providerFilter ? { provider_id: Number(providerFilter) } : undefined)).data,
  });
  const identityLinks = useQuery({
    queryKey: ["iam", "identity-links", providerFilter],
    queryFn: async () =>
      (await iamApi.listIdentityLinks(providerFilter ? { provider_id: Number(providerFilter) } : undefined)).data,
  });
  const directoryCredentials = useQuery({
    queryKey: ["iam", "directory-credentials", providerFilter],
    queryFn: async () =>
      (await iamApi.listDirectoryCredentials(providerFilter ? { provider_id: Number(providerFilter) } : undefined)).data,
  });
  const directoryEvents = useQuery({
    queryKey: ["iam", "directory-events", providerFilter],
    queryFn: async () =>
      (await iamApi.listDirectoryEvents(providerFilter ? { provider_id: Number(providerFilter) } : undefined)).data,
  });

  const providerRows = useMemo(() => providers.data ?? [], [providers.data]);
  const roleRows = useMemo(() => roles.data ?? [], [roles.data]);
  const userRows = useMemo(() => users.data ?? [], [users.data]);
  const orgRows = useMemo(() => orgUnits.data ?? [], [orgUnits.data]);
  const namespaceRows = useMemo(() => namespaces.data ?? [], [namespaces.data]);
  const mappingRows = useMemo(() => mappings.data ?? [], [mappings.data]);
  const linkRows = useMemo(() => identityLinks.data ?? [], [identityLinks.data]);
  const directoryCredentialRows = useMemo(() => directoryCredentials.data ?? [], [directoryCredentials.data]);
  const directoryEventRows = useMemo(() => directoryEvents.data ?? [], [directoryEvents.data]);
  const loading =
    providers.isLoading ||
    roles.isLoading ||
    users.isLoading ||
    orgUnits.isLoading ||
    namespaces.isLoading ||
    mappings.isLoading ||
    identityLinks.isLoading ||
    directoryCredentials.isLoading ||
    directoryEvents.isLoading;

  const metrics = useMemo(() => {
    const linkedUsers = new Set(linkRows.map((item) => item.user_id)).size;
    return {
      providers: providerRows.length,
      enabledProviders: providerRows.filter((item) => item.enabled).length,
      mappings: mappingRows.length,
      enabledMappings: mappingRows.filter((item) => item.enabled).length,
      links: linkRows.length,
      linkedUsers,
    };
  }, [linkRows, mappingRows, providerRows]);

  const selectedRole = roleRows.find((item) => item.id === Number(draft.role_id)) ?? null;

  function refetchAll() {
    void queryClient.invalidateQueries({ queryKey: ["iam"] });
  }

  function startCreate() {
    setEditing(null);
    setDraft({
      ...emptyDraft,
      provider_id: providerFilter || (providerRows[0] ? String(providerRows[0].id) : ""),
    });
    setDraftOpen(true);
  }

  function startEdit(mapping: SSORoleMapping) {
    setEditing(mapping);
    setDraft(mappingToDraft(mapping));
    setDraftOpen(true);
  }

  function closeDraft() {
    setDraftOpen(false);
    setEditing(null);
    setDraft(emptyDraft);
  }

  function normalizedTarget(role: Role | null) {
    const namespace_id = role?.scope === "namespace" && draft.namespace_id ? Number(draft.namespace_id) : null;
    const org_unit_id = role?.scope === "org" && draft.org_unit_id ? Number(draft.org_unit_id) : null;
    return { namespace_id, org_unit_id };
  }

  async function saveMapping() {
    if (!draft.provider_id || !draft.role_id || !draft.claim_name.trim() || !draft.claim_value.trim()) {
      message.error("Provider、Role、claim name 和 claim value 必填");
      return;
    }
    const role = roleRows.find((item) => item.id === Number(draft.role_id)) ?? null;
    if (role?.scope === "namespace" && !draft.namespace_id) {
      message.error("Namespace role 必须选择 namespace");
      return;
    }
    if (role?.scope === "org" && !draft.org_unit_id) {
      message.error("Org role 必须选择组织单元");
      return;
    }

    setSaving(true);
    try {
      const target = normalizedTarget(role);
      if (editing) {
        const payload: SSORoleMappingUpdate = {
          claim_name: draft.claim_name.trim(),
          claim_value: draft.claim_value.trim(),
          role_id: Number(draft.role_id),
          namespace_id: target.namespace_id,
          org_unit_id: target.org_unit_id,
          enabled: draft.enabled,
          priority: Number(draft.priority) || 100,
          description: draft.description.trim() || null,
        };
        await iamApi.updateSSORoleMapping(editing.id, payload);
        message.success("SSO role mapping 已更新");
      } else {
        const payload: SSORoleMappingCreate = {
          provider_id: Number(draft.provider_id),
          claim_name: draft.claim_name.trim(),
          claim_value: draft.claim_value.trim(),
          role_id: Number(draft.role_id),
          namespace_id: target.namespace_id,
          org_unit_id: target.org_unit_id,
          enabled: draft.enabled,
          priority: Number(draft.priority) || 100,
          description: draft.description.trim() || null,
        };
        await iamApi.createSSORoleMapping(payload);
        message.success("SSO role mapping 已创建");
      }
      closeDraft();
      refetchAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  function deleteMapping(mapping: SSORoleMapping) {
    modal.confirm({
      title: "删除 SSO role mapping",
      content: `${providerName(providerRows, mapping.provider_id)} · ${mapping.claim_name}=${mapping.claim_value}`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      async onOk() {
        await iamApi.deleteSSORoleMapping(mapping.id);
        message.success("SSO role mapping 已删除");
        refetchAll();
      },
    });
  }

  async function toggleMapping(mapping: SSORoleMapping) {
    await iamApi.updateSSORoleMapping(mapping.id, { enabled: !mapping.enabled });
    message.success(mapping.enabled ? "SSO role mapping 已停用" : "SSO role mapping 已启用");
    refetchAll();
  }

  async function issueDirectoryCredential() {
    const providerId = providerFilter ? Number(providerFilter) : providerRows[0]?.id;
    if (!providerId) {
      message.error("请先创建并选择一个 SSO provider");
      return;
    }
    setIssuingDirectoryCredential(true);
    try {
      const { data } = await iamApi.createDirectoryCredential({
        provider_id: providerId,
        name: `SCIM lifecycle · ${providerName(providerRows, providerId)}`,
      });
      modal.info({
        title: "SCIM 凭证仅显示一次",
        width: 680,
        content: (
          <div className="space-y-3 pt-2 text-sm text-slate-600">
            <p>立即复制到目录连接器。DuckDock 只保存哈希，关闭后无法再次查看。</p>
            <code className="block break-all rounded-md bg-slate-950 p-3 text-xs text-emerald-300">{data.token}</code>
          </div>
        ),
      });
      refetchAll();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "SCIM 凭证签发失败");
    } finally {
      setIssuingDirectoryCredential(false);
    }
  }

  function revokeDirectoryCredential(credential: DirectoryCredential) {
    modal.confirm({
      title: "撤销 SCIM 凭证",
      content: `${credential.name} · dkr_scim_${credential.token_prefix}_…`,
      okText: "撤销",
      okButtonProps: { danger: true },
      cancelText: "取消",
      async onOk() {
        await iamApi.revokeDirectoryCredential(credential.public_id, "Revoked from IAM console");
        message.success("SCIM 凭证已撤销");
        refetchAll();
      },
    });
  }

  return (
    <div className="app-page page-stack">
      <PageHeader
        eyebrow="ENTERPRISE IAM"
        title="身份与 SSO"
        description="企业 UID、身份源链接、SCIM 生命周期和 IdP group 到 DuckDock RBAC 的 JIT 映射。"
        actions={
          <>
            <select
              value={providerFilter}
              onChange={(event) => setProviderFilter(event.target.value)}
              className={`${selectClass} w-[220px]`}
              aria-label="Provider filter"
            >
              <option value="">全部 provider</option>
              {providerRows.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.name} · {provider.provider_type.toUpperCase()}
                </option>
              ))}
            </select>
            <Button
              variant="secondary"
              onClick={refetchAll}
              disabled={loading}
              icon={<RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />}
            >
              刷新
            </Button>
            <Button onClick={startCreate} icon={<Plus className="h-4 w-4" />} disabled={!providerRows.length || !roleRows.length}>
              新建映射
            </Button>
          </>
        }
      />

      <div className="grid gap-4 md:grid-cols-3">
        <MetricCard
          label="SSO Providers"
          value={String(metrics.providers)}
          detail={`${metrics.enabledProviders} 个启用`}
          icon={ShieldCheck}
          loading={loading}
        />
        <MetricCard
          label="Role Mappings"
          value={String(metrics.mappings)}
          detail={`${metrics.enabledMappings} 条启用`}
          icon={KeyRound}
          loading={loading}
        />
        <MetricCard
          label="Identity Links"
          value={String(metrics.links)}
          detail={`${metrics.linkedUsers} 个用户已绑定身份源`}
          icon={Link2}
          loading={loading}
        />
      </div>

      {draftOpen ? (
        <SectionCard
          title={editing ? "编辑 SSO Role Mapping" : "新建 SSO Role Mapping"}
          action={
            <Button variant="ghost" onClick={closeDraft} icon={<X className="h-4 w-4" />}>
              关闭
            </Button>
          }
        >
          <div className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-4">
            <Field label="Provider">
              <select
                value={draft.provider_id}
                onChange={(event) => setDraft((current) => ({ ...current, provider_id: event.target.value }))}
                className={selectClass}
                disabled={Boolean(editing)}
              >
                <option value="">选择 provider</option>
                {providerRows.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.name} · {provider.provider_type.toUpperCase()}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Role">
              <select
                value={draft.role_id}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, role_id: event.target.value, namespace_id: "", org_unit_id: "" }))
                }
                className={selectClass}
              >
                <option value="">选择 role</option>
                {roleRows.map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name} · {role.scope}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Claim Name">
              <Input
                value={draft.claim_name}
                onChange={(event) => setDraft((current) => ({ ...current, claim_name: event.target.value }))}
                placeholder="groups"
              />
            </Field>
            <Field label="Claim Value">
              <Input
                value={draft.claim_value}
                onChange={(event) => setDraft((current) => ({ ...current, claim_value: event.target.value }))}
                placeholder="duckdock-admins"
              />
            </Field>

            <Field label="Namespace Target">
              <select
                value={draft.namespace_id}
                onChange={(event) => setDraft((current) => ({ ...current, namespace_id: event.target.value }))}
                className={selectClass}
                disabled={selectedRole?.scope !== "namespace"}
              >
                <option value="">无</option>
                {namespaceRows.map((namespace) => (
                  <option key={namespace.id} value={namespace.id}>
                    {namespace.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Org Target">
              <select
                value={draft.org_unit_id}
                onChange={(event) => setDraft((current) => ({ ...current, org_unit_id: event.target.value }))}
                className={selectClass}
                disabled={selectedRole?.scope !== "org"}
              >
                <option value="">无</option>
                {orgRows.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}{org.code ? ` · ${org.code}` : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Priority">
              <Input
                type="number"
                min={1}
                value={draft.priority}
                onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value }))}
              />
            </Field>
            <Field label="Status">
              <label className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-700 shadow-sm">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
                  className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                />
                启用
              </label>
            </Field>
            <div className="md:col-span-2 xl:col-span-3">
              <Field label="Description">
                <Input
                  value={draft.description}
                  onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                  placeholder="IdP cutover batch"
                />
              </Field>
            </div>
            <div className="flex items-end justify-end">
              <Button onClick={() => void saveMapping()} disabled={saving} icon={<Save className="h-4 w-4" />}>
                保存
              </Button>
            </div>
          </div>
        </SectionCard>
      ) : null}

      <SectionCard title="SSO Providers" description="OIDC / LDAP provider 状态。">
        <div className="divide-y divide-slate-200">
          {providerRows.length ? (
            providerRows.map((provider) => (
              <div key={provider.id} className="data-row grid gap-3 text-sm md:grid-cols-[1.1fr_96px_96px_1.4fr]">
                <div className="min-w-0">
                  <div className="font-semibold text-slate-900">{provider.name}</div>
                  <div className="mt-1 truncate text-xs text-slate-500">
                    {provider.issuer_url || provider.ldap_server_url || "-"}
                  </div>
                </div>
                <Badge tone={provider.provider_type === "oidc" ? "indigo" : "emerald"}>{provider.provider_type.toUpperCase()}</Badge>
                <Badge tone={provider.enabled ? "emerald" : "neutral"} icon={provider.enabled ? <CheckCircle2 className="h-3.5 w-3.5" /> : null}>
                  {provider.enabled ? "Enabled" : "Disabled"}
                </Badge>
                <div className="min-w-0 text-xs text-slate-500">
                  mapping {mappingRows.filter((item) => item.provider_id === provider.id).length} · links{" "}
                  {linkRows.filter((item) => item.provider_id === provider.id).length}
                </div>
              </div>
            ))
          ) : (
            <EmptyState text="暂无 SSO provider" icon={ShieldCheck} />
          )}
        </div>
      </SectionCard>

      <SectionCard title="SSO Role Mappings" description="IdP claim/group 到 DuckDock RoleBinding 的 JIT 规则。">
        <div className="overflow-x-auto">
          {mappingRows.length ? (
            <table className="w-full min-w-[980px] text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-semibold">Provider</th>
                  <th className="px-4 py-3 font-semibold">Claim</th>
                  <th className="px-4 py-3 font-semibold">Role</th>
                  <th className="px-4 py-3 font-semibold">Target</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Priority</th>
                  <th className="px-4 py-3 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {mappingRows.map((mapping) => {
                  const role = roleRows.find((item) => item.id === mapping.role_id);
                  return (
                    <tr key={mapping.id} className="align-top">
                      <td className="px-4 py-3 text-slate-700">{providerName(providerRows, mapping.provider_id)}</td>
                      <td className="px-4 py-3">
                        <MonoPill>{mapping.claim_name}</MonoPill>
                        <div className="mt-1 text-slate-700">{mapping.claim_value}</div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-slate-900">{roleName(roleRows, mapping.role_id)}</div>
                        {role ? <Badge className="mt-1" tone={scopeTone[role.scope]}>{role.scope}</Badge> : null}
                      </td>
                      <td className="px-4 py-3 text-slate-700">{targetLabel(mapping, roleRows, namespaceRows, orgRows)}</td>
                      <td className="px-4 py-3">
                        <Badge tone={mapping.enabled ? "emerald" : "neutral"}>{mapping.enabled ? "Enabled" : "Disabled"}</Badge>
                      </td>
                      <td className="px-4 py-3 text-slate-700">{mapping.priority}</td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => startEdit(mapping)}
                            icon={<Eye className="h-4 w-4" />}
                          >
                            编辑
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => void toggleMapping(mapping)}
                          >
                            {mapping.enabled ? "停用" : "启用"}
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            danger
                            onClick={() => deleteMapping(mapping)}
                            icon={<Trash2 className="h-4 w-4" />}
                          >
                            删除
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <EmptyState text="暂无 SSO role mapping" icon={KeyRound} />
          )}
        </div>
      </SectionCard>

      <SectionCard
        title="Identity Lifecycle 2.0"
        description="Provider-bound SCIM 凭证、目录停用回执和自动交接结果；凭证明文仅签发时显示一次。"
        action={
          <Button
            onClick={() => void issueDirectoryCredential()}
            disabled={issuingDirectoryCredential || !providerRows.length}
            icon={<KeyRound className="h-4 w-4" />}
          >
            签发 SCIM 凭证
          </Button>
        }
      >
        <div className="grid gap-0 xl:grid-cols-2 xl:divide-x xl:divide-slate-200">
          <div className="min-w-0">
            <div className="border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Directory credentials
            </div>
            <div className="divide-y divide-slate-200">
              {directoryCredentialRows.length ? (
                directoryCredentialRows.map((credential: DirectoryCredential) => (
                  <div key={credential.public_id} className="data-row flex items-center justify-between gap-3 text-sm">
                    <div className="min-w-0">
                      <div className="truncate font-medium text-slate-900">{credential.name}</div>
                      <div className="mt-1 text-xs text-slate-500">
                        {providerName(providerRows, credential.provider_id)} · dkr_scim_{credential.token_prefix}_… · used {formatDate(credential.last_used_at)}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Badge tone={credential.is_active ? "emerald" : "neutral"}>
                        {credential.is_active ? "ACTIVE" : "REVOKED"}
                      </Badge>
                      {credential.is_active ? (
                        <Button size="sm" variant="secondary" danger onClick={() => revokeDirectoryCredential(credential)}>
                          撤销
                        </Button>
                      ) : null}
                    </div>
                  </div>
                ))
              ) : (
                <EmptyState text="暂无 SCIM credential" icon={KeyRound} />
              )}
            </div>
          </div>
          <div className="min-w-0">
            <div className="border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Directory lifecycle receipts
            </div>
            <div className="divide-y divide-slate-200">
              {directoryEventRows.length ? (
                directoryEventRows.map((event: DirectoryLifecycleEvent) => (
                  <div key={event.public_id} className="data-row text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-xs font-medium text-slate-800">{event.public_id}</div>
                        <div className="mt-1 text-xs text-slate-500">
                          user #{event.user_id} · {formatDate(event.occurred_at)} · {event.source}
                        </div>
                      </div>
                      <Badge tone={event.action === "DISABLE" ? "rose" : "emerald"}>{event.action}</Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-600">
                      <MonoPill>{event.credentials_revoked} credentials revoked</MonoPill>
                      <MonoPill>{event.memberships_removed} memberships removed</MonoPill>
                      <MonoPill>{event.handover_case_ids_json.length} handovers</MonoPill>
                    </div>
                  </div>
                ))
              ) : (
                <EmptyState text="暂无 directory lifecycle receipt" icon={ShieldCheck} />
              )}
            </div>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Identity Links" description="OIDC / LDAP subject 与 DuckDock 企业 UID 的绑定记录。">
        <div className="overflow-x-auto">
          {linkRows.length ? (
            <table className="w-full min-w-[920px] text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-semibold">User</th>
                  <th className="px-4 py-3 font-semibold">Provider</th>
                  <th className="px-4 py-3 font-semibold">External UID</th>
                  <th className="px-4 py-3 font-semibold">Subject</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Last Seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {linkRows.map((link: IdentityLink) => (
                  <tr key={link.id}>
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-900">{userName(userRows, link.user_id)}</div>
                      <div className="mt-1 text-xs text-slate-500">{link.email ?? link.username ?? "-"}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-slate-700">{providerName(providerRows, link.provider_id)}</div>
                      <Badge className="mt-1" tone={link.source === "oidc" ? "indigo" : "emerald"}>{link.source}</Badge>
                    </td>
                    <td className="px-4 py-3 text-slate-700">{link.external_uid ?? "-"}</td>
                    <td className="max-w-[280px] px-4 py-3">
                      <div className="truncate font-mono text-xs text-slate-600" title={link.external_subject}>
                        {link.external_subject}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={link.is_active ? "emerald" : "rose"}>{link.is_active ? "ACTIVE" : "DISABLED"}</Badge>
                    </td>
                    <td className="px-4 py-3 text-slate-700">{formatDate(link.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState text="暂无 identity link" icon={Link2} />
          )}
        </div>
      </SectionCard>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}
