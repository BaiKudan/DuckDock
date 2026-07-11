import { useEffect, useState } from "react";
import { namespacesApi, type NamespaceGovernancePolicy } from "../../api/client";
import { NumberField, Panel, SelectField, TextField } from "./Panel";

function ToggleField({
  label,
  checked,
  onChange,
  description,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  description?: string;
}) {
  return (
    <label className="flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-4">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
      />
      <div>
        <div className="text-sm font-medium tracking-tight text-slate-900">{label}</div>
        {description ? <div className="mt-1 text-xs text-slate-500">{description}</div> : null}
      </div>
    </label>
  );
}

const EMPTY_POLICY: NamespaceGovernancePolicy = {
  namespace_id: 0,
  manual_review_required: false,
  require_examples: true,
  require_validation_spec: true,
  require_sandbox_success: true,
  clinic_gate_enabled: false,
  min_clinic_score: 75,
  clinic_max_age_hours: 168,
  public_sharing_requires_approval: true,
  public_share_default_expiry_days: 30,
  require_license_attestation: true,
  allowed_public_licenses: ["MIT", "MIT-0", "Apache-2.0", "BSD-3-Clause", "Proprietary-Internal"],
  sandbox_network_mode: "registry_only",
  sandbox_workspace_mode: "ephemeral",
  sandbox_agent_smoke_enabled: false,
  sandbox_agent_smoke_timeout_seconds: 45,
};

export function GovernancePanel({ ns }: { ns: string }) {
  const [policy, setPolicy] = useState<NamespaceGovernancePolicy | null>(null);
  const [licenses, setLicenses] = useState("MIT, MIT-0, Apache-2.0, BSD-3-Clause, Proprietary-Internal");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    namespacesApi
      .getGovernance(ns)
      .then(({ data }) => {
        setPolicy(data);
        setLicenses((data.allowed_public_licenses ?? []).join(", "));
      })
      .catch(() => {
        setPolicy({ ...EMPTY_POLICY });
        setLicenses((EMPTY_POLICY.allowed_public_licenses ?? []).join(", "));
      })
      .finally(() => setLoading(false));
  }, [ns]);

  function update<K extends keyof NamespaceGovernancePolicy>(key: K, value: NamespaceGovernancePolicy[K]) {
    setPolicy((current) => (current ? { ...current, [key]: value } : current));
  }

  async function save() {
    if (!policy) {
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload: NamespaceGovernancePolicy = {
        ...policy,
        allowed_public_licenses: licenses
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      };
      const { data } = await namespacesApi.updateGovernance(ns, payload);
      setPolicy(data);
      setLicenses((data.allowed_public_licenses ?? []).join(", "));
      setMessage("治理策略已保存。");
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "治理策略保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading || !policy) {
    return <div className="text-sm text-slate-500">正在加载治理策略...</div>;
  }

  return (
    <div className="space-y-6">
      <Panel
        title="发布门禁"
        subtitle="控制版本从 scanning / review 进入 production 的条件。"
        actions={
          <button onClick={save} disabled={saving} className="button-primary disabled:opacity-50">
            {saving ? "保存中..." : "保存策略"}
          </button>
        }
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <ToggleField
            label="启用人工审核"
            checked={policy.manual_review_required}
            onChange={(value) => update("manual_review_required", value)}
            description="开启后，版本会先进入 review，由命名空间管理员审批后再进入 production。"
          />
          <ToggleField
            label="要求 examples/*"
            checked={policy.require_examples}
            onChange={(value) => update("require_examples", value)}
            description="未携带示例文件的技能包不能直接过门禁。"
          />
          <ToggleField
            label="要求 .duckdock/validation.yaml"
            checked={policy.require_validation_spec}
            onChange={(value) => update("require_validation_spec", value)}
            description="要求技能包携带显式校验规范和 smoke 定义。"
          />
          <ToggleField
            label="要求 Sandbox 通过"
            checked={policy.require_sandbox_success}
            onChange={(value) => update("require_sandbox_success", value)}
            description="静态扫描通过后，还需要 OpenClaw sandbox 验证成功。"
          />
          <ToggleField
            label="启用 Clinic 门禁"
            checked={policy.clinic_gate_enabled}
            onChange={(value) => update("clinic_gate_enabled", value)}
            description="低于阈值或评测过旧时，版本不会直接进入 production。"
          />
          <div className="grid gap-4 md:grid-cols-2">
            <NumberField
              label="Clinic 最低分"
              value={policy.min_clinic_score ?? 75}
              onChange={(value) => update("min_clinic_score", value)}
            />
            <NumberField
              label="Clinic 最长有效小时"
              value={policy.clinic_max_age_hours}
              onChange={(value) => update("clinic_max_age_hours", value)}
            />
          </div>
        </div>
      </Panel>

      <Panel
        title="公共区治理"
        subtitle="控制共享到公共区的审批、时效和许可证约束。"
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <ToggleField
            label="公开共享需要审批"
            checked={policy.public_sharing_requires_approval}
            onChange={(value) => update("public_sharing_requires_approval", value)}
          />
          <ToggleField
            label="要求许可证确认"
            checked={policy.require_license_attestation}
            onChange={(value) => update("require_license_attestation", value)}
          />
          <NumberField
            label="默认公开到期天数"
            value={policy.public_share_default_expiry_days ?? 30}
            onChange={(value) => update("public_share_default_expiry_days", value)}
          />
          <TextField
            label="允许的公开许可证"
            value={licenses}
            onChange={setLicenses}
            placeholder="MIT, Apache-2.0"
          />
        </div>
      </Panel>

      <Panel
        title="Sandbox 策略"
        subtitle="控制运行时验证的网络、工作区和 agent smoke 行为。"
      >
        <div className="grid gap-4 md:grid-cols-2">
          <SelectField
            label="网络模式"
            value={policy.sandbox_network_mode}
            onChange={(value) => update("sandbox_network_mode", value)}
            options={[
              { label: "仅安装阶段允许访问 registry", value: "registry_only" },
              { label: "完全离线", value: "offline" },
              { label: "受控网络", value: "restricted" },
            ]}
          />
          <SelectField
            label="工作区模式"
            value={policy.sandbox_workspace_mode}
            onChange={(value) => update("sandbox_workspace_mode", value)}
            options={[
              { label: "临时工作区", value: "ephemeral" },
              { label: "缓存工作区", value: "cached" },
            ]}
          />
          <ToggleField
            label="启用 Agent Smoke"
            checked={policy.sandbox_agent_smoke_enabled}
            onChange={(value) => update("sandbox_agent_smoke_enabled", value)}
            description="在 OpenClaw sandbox 中真正执行 smoke prompts，验证技能的最小行为闭环。"
          />
          <NumberField
            label="Agent Smoke 默认超时（秒）"
            value={policy.sandbox_agent_smoke_timeout_seconds ?? 45}
            onChange={(value) => update("sandbox_agent_smoke_timeout_seconds", value)}
          />
        </div>
      </Panel>

      {message ? <div className="soft-emerald rounded-xl px-4 py-3 text-sm">{message}</div> : null}
      {error ? <div className="soft-rose rounded-xl px-4 py-3 text-sm">{error}</div> : null}
    </div>
  );
}
