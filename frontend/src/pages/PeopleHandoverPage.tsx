import { Archive, CheckCircle2, ClipboardCheck, FileClock, RefreshCw, UserRound, Users2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  peopleApi,
  type EmploymentStatus,
  type PersonHandoverProfile,
  type PersonHandoverProfileUpdate,
} from "../api/client";
import {
  Badge,
  Button,
  Card,
  IconTile,
  Input,
  MetricCard,
  MonoPill,
  PageHeader,
  type Tone,
} from "../components/ui";

interface ProfileForm {
  position_title: string;
  employee_no: string;
  manager_user_id: string;
  handover_receiver_user_id: string;
  note: string;
}

const emptyForm: ProfileForm = {
  position_title: "",
  employee_no: "",
  manager_user_id: "",
  handover_receiver_user_id: "",
  note: "",
};

const statusLabels: Record<EmploymentStatus, string> = {
  active: "在职",
  onboarding: "入职中",
  leave: "离职交接中",
  offboarded: "已离职",
};

export default function PeopleHandoverPage() {
  const [people, setPeople] = useState<PersonHandoverProfile[]>([]);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const selected = useMemo(
    () => people.find((person) => person.id === selectedUserId) ?? people[0] ?? null,
    [people, selectedUserId]
  );

  const metrics = useMemo(() => {
    const leaving = people.filter((person) => person.employment_status === "leave").length;
    const assets = people.reduce((sum, person) => sum + person.asset_count, 0);
    const handovers = people.reduce((sum, person) => sum + person.handover_count, 0);
    return { users: people.length, leaving, assets, handovers };
  }, [people]);

  async function loadPeople() {
    setLoading(true);
    setError("");
    try {
      const { data } = await peopleApi.listPeople();
      setPeople(data);
      setSelectedUserId((current) => current ?? data[0]?.id ?? null);
    } catch (err: unknown) {
      setError(getErrorDetail(err) ?? "人员列表加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function updateSelected(update: PersonHandoverProfileUpdate) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await peopleApi.updatePerson(selected.id, update);
      setPeople((current) => current.map((person) => (person.id === data.id ? data : person)));
    } catch (err: unknown) {
      setError(getErrorDetail(err) ?? "保存人员交接档案失败");
    } finally {
      setBusy(false);
    }
  }

  function saveProfile() {
    void updateSelected({
      position_title: form.position_title.trim() || null,
      employee_no: form.employee_no.trim() || null,
      manager_user_id: form.manager_user_id ? Number(form.manager_user_id) : null,
      handover_receiver_user_id: form.handover_receiver_user_id ? Number(form.handover_receiver_user_id) : null,
      note: form.note.trim() || null,
    });
  }

  useEffect(() => {
    void loadPeople();
  }, []);

  useEffect(() => {
    if (!selected) {
      setForm(emptyForm);
      return;
    }
    setForm({
      position_title: selected.position_title ?? "",
      employee_no: selected.employee_no ?? "",
      manager_user_id: selected.manager_user_id ? String(selected.manager_user_id) : "",
      handover_receiver_user_id: selected.handover_receiver_user_id ? String(selected.handover_receiver_user_id) : "",
      note: selected.note ?? "",
    });
  }, [selected?.id, selected?.profile_updated_at]);

  return (
    <div className="app-page max-w-[1180px] space-y-6">
      <PageHeader
        eyebrow="PEOPLE HANDOVER"
        title="人员与交接"
        description="只维护 DuckDock 需要的用户岗位与交接状态，不管理企业组织架构、RBAC 或 SSO。"
        actions={
          <Button
            variant="secondary"
            disabled={loading}
            onClick={() => void loadPeople()}
            icon={<RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />}
          >
            刷新
          </Button>
        }
      />

      {error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-4">
        <MetricCard label="用户" value={metrics.users} icon={Users2} loading={loading} />
        <MetricCard label="离职交接中" value={metrics.leaving} icon={ClipboardCheck} loading={loading} />
        <MetricCard label="关联 AI 资产" value={metrics.assets} icon={Archive} loading={loading} />
        <MetricCard label="交接单" value={metrics.handovers} icon={FileClock} loading={loading} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[330px,minmax(0,1fr)]">
        <Card title="用户" description="选择一个用户维护岗位与交接状态。">
          <div className="max-h-[660px] divide-y divide-slate-200 overflow-y-auto">
            {loading ? <div className="px-5 py-8 text-sm text-slate-500">加载中...</div> : null}
            {!loading && people.length === 0 ? <div className="px-5 py-8 text-sm text-slate-500">暂无用户</div> : null}
            {people.map((person) => (
              <button
                key={person.id}
                type="button"
                onClick={() => setSelectedUserId(person.id)}
                className={`block w-full px-5 py-4 text-left transition ${
                  selected?.id === person.id ? "bg-indigo-50" : "hover:bg-slate-50/70"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-900">{person.username}</div>
                    <div className="mt-1 truncate text-xs text-slate-500">{person.position_title ?? "未填写岗位"}</div>
                  </div>
                  <Badge tone={statusTone(person.employment_status)} className="shrink-0">
                    {statusLabels[person.employment_status]}
                  </Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <MonoPill>{person.asset_count} 资产</MonoPill>
                  <MonoPill>{person.work_trace_count} 历程</MonoPill>
                  <MonoPill>{person.handover_count} 交接单</MonoPill>
                </div>
              </button>
            ))}
          </div>
        </Card>

        <Card>
          {selected ? (
            <>
              <div className="flex flex-col items-start justify-between gap-4 border-b border-slate-200 px-5 py-4 sm:px-6 lg:flex-row lg:items-center">
                <div className="flex min-w-0 items-center gap-3">
                  <IconTile size="lg">
                    <UserRound className="h-5 w-5" />
                  </IconTile>
                  <div className="min-w-0">
                    <div className="truncate text-xl font-bold tracking-tight text-slate-950">{selected.username}</div>
                    <div className="mt-1 text-sm text-slate-500">{selected.email}</div>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={busy || selected.employment_status === "leave"}
                    onClick={() => void updateSelected({ employment_status: "leave" })}
                  >
                    标记离职交接
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={busy || selected.employment_status === "active"}
                    onClick={() => void updateSelected({ employment_status: "active" })}
                  >
                    恢复在职
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    danger
                    disabled={busy || selected.employment_status === "offboarded"}
                    onClick={() => void updateSelected({ employment_status: "offboarded" })}
                  >
                    标记已离职
                  </Button>
                </div>
              </div>

              <div className="grid gap-5 p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_280px]">
                <div className="space-y-5">
                  <div className="grid gap-4 md:grid-cols-2">
                    <InfoField label="姓名" value={selected.full_name ?? "-"} />
                    <InfoField label="系统角色" value={selected.system_role} />
                    <InfoField label="认证来源" value={selected.auth_source} />
                    <InfoField label="最近登录" value={formatDate(selected.last_login_at)} />
                  </div>

                  <div className="rounded-lg border border-slate-200 p-5">
                    <div className="text-sm font-semibold text-slate-900">岗位与交接</div>
                    <div className="mt-4 grid gap-4 md:grid-cols-2">
                      <label className="block">
                        <div className="table-label mb-2">岗位</div>
                        <Input
                          value={form.position_title}
                          onChange={(event) => setForm((current) => ({ ...current, position_title: event.target.value }))}
                          placeholder="例如：售前解决方案工程师"
                        />
                      </label>
                      <label className="block">
                        <div className="table-label mb-2">工号</div>
                        <Input
                          value={form.employee_no}
                          onChange={(event) => setForm((current) => ({ ...current, employee_no: event.target.value }))}
                          placeholder="可选"
                        />
                      </label>
                      <label className="block">
                        <div className="table-label mb-2">直属负责人</div>
                        <select
                          value={form.manager_user_id}
                          onChange={(event) => setForm((current) => ({ ...current, manager_user_id: event.target.value }))}
                          className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition-colors focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100"
                        >
                          <option value="">未指定</option>
                          {people
                            .filter((person) => person.id !== selected.id)
                            .map((person) => (
                              <option key={person.id} value={person.id}>
                                {person.username}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label className="block">
                        <div className="table-label mb-2">交接接收人</div>
                        <select
                          value={form.handover_receiver_user_id}
                          onChange={(event) =>
                            setForm((current) => ({ ...current, handover_receiver_user_id: event.target.value }))
                          }
                          className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition-colors focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100"
                        >
                          <option value="">未指定</option>
                          {people
                            .filter((person) => person.id !== selected.id)
                            .map((person) => (
                              <option key={person.id} value={person.id}>
                                {person.username}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label className="block md:col-span-2">
                        <div className="table-label mb-2">备注</div>
                        <textarea
                          value={form.note}
                          onChange={(event) => setForm((current) => ({ ...current, note: event.target.value }))}
                          rows={4}
                          className="min-h-28 w-full resize-y rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition-colors placeholder:text-slate-400 focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100"
                          placeholder="只记录与 AI 资产交接有关的信息。"
                        />
                      </label>
                    </div>
                    <div className="mt-5 flex items-center justify-between gap-3">
                      <Badge
                        tone={statusTone(selected.employment_status)}
                        icon={<CheckCircle2 className="h-3.5 w-3.5" />}
                      >
                        {statusLabels[selected.employment_status]}
                      </Badge>
                      <Button size="sm" disabled={busy} onClick={saveProfile}>
                        保存岗位信息
                      </Button>
                    </div>
                  </div>
                </div>

                <aside className="space-y-4">
                  <SideCard label="AI 资产" value={selected.asset_count} detail="归属到该用户的 Skills、Agents、Prompts 等资产。" />
                  <SideCard label="工作历程" value={selected.work_trace_count} detail="Reporter 或运行时采集到的会话与任务摘要。" />
                  <SideCard label="交接单" value={selected.handover_count} detail="与该用户作为交接对象或接收人相关的交接流程。" />
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4 text-sm leading-6 text-slate-500">
                    这里不维护部门、组织树或角色权限。DuckDock 只关心这个岗位产生了哪些 AI 资产，以及状态变化后谁来接。
                  </div>
                </aside>
              </div>
            </>
          ) : (
            <div className="px-6 py-12 text-sm text-slate-500">请选择用户。</div>
          )}
        </Card>
      </div>
    </div>
  );
}

function InfoField({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="table-label">{label}</div>
      <div className="mt-2 truncate text-sm font-medium text-slate-900">{value}</div>
    </div>
  );
}

function SideCard({ label, value, detail }: { label: string; value: number; detail: string }) {
  return (
    <div className="rounded-lg border border-slate-200 px-4 py-4">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="mt-2 text-2xl font-bold tracking-tight text-slate-950">{value}</div>
      <div className="mt-2 text-xs leading-5 text-slate-500">{detail}</div>
    </div>
  );
}

function statusTone(status: EmploymentStatus): Tone {
  if (status === "leave") return "amber";
  if (status === "offboarded") return "rose";
  if (status === "onboarding") return "indigo";
  return "emerald";
}

function formatDate(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getErrorDetail(err: unknown) {
  if (!isRecord(err)) {
    return null;
  }
  const response = err.response;
  if (!isRecord(response)) {
    return null;
  }
  const data = response.data;
  if (!isRecord(data)) {
    return null;
  }
  return typeof data.detail === "string" ? data.detail : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
