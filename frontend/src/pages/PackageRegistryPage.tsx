import {
  Box,
  CheckCircle2,
  Download,
  FileJson,
  KeyRound,
  Link2,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  controlPlaneApi,
  namespacesApi,
  packageRegistryApi,
  type AgentPackage,
  type AgentPackageManifest,
  type AgentPackageVerification,
  type AgentPackageVersion,
  type AIAsset,
  type Namespace,
  type PackageSigningKey,
} from "../api/client";
import { Badge, Button, Card, EmptyState, MetricCard, MonoPill, PageHeader } from "../components/ui";

function errorDetail(error: unknown): string {
  if (typeof error === "object" && error !== null) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === "string") return response.data.detail;
    if (Array.isArray(response?.data?.detail)) return "提交内容不符合 Package v2 契约。";
  }
  return "操作失败，请检查服务状态与输入。";
}

function shortDigest(value: string): string {
  return `${value.slice(0, 10)}…${value.slice(-8)}`;
}

function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export default function PackageRegistryPage() {
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [namespaceId, setNamespaceId] = useState<number | null>(null);
  const [assets, setAssets] = useState<AIAsset[]>([]);
  const [packages, setPackages] = useState<AgentPackage[]>([]);
  const [keys, setKeys] = useState<PackageSigningKey[]>([]);
  const [selectedPackageId, setSelectedPackageId] = useState("");
  const [versions, setVersions] = useState<AgentPackageVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<AgentPackageVersion | null>(null);
  const [verification, setVerification] = useState<AgentPackageVerification | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [packageName, setPackageName] = useState("");
  const [packageDescription, setPackageDescription] = useState("");
  const [agentAssetId, setAgentAssetId] = useState("");
  const [keyId, setKeyId] = useState("");
  const [publicKeyPem, setPublicKeyPem] = useState("");
  const [signingKeyId, setSigningKeyId] = useState("");
  const [signature, setSignature] = useState("");
  const [manifestText, setManifestText] = useState("");
  const [sbomText, setSbomText] = useState("");
  const [canonicalDigest, setCanonicalDigest] = useState("");

  const selectedPackage = useMemo(
    () => packages.find((item) => item.public_id === selectedPackageId) ?? null,
    [packages, selectedPackageId],
  );
  const visibleAssets = useMemo(
    () => assets.filter((asset) => asset.namespace_id === namespaceId && asset.asset_type === "agent"),
    [assets, namespaceId],
  );
  const activeKeys = keys.filter((key) => key.status === "ACTIVE");

  async function loadNamespacesAndAssets() {
    const [{ data: namespaceRows }, { data: assetRows }] = await Promise.all([
      namespacesApi.list(),
      controlPlaneApi.listAssets(),
    ]);
    setNamespaces(namespaceRows);
    setAssets(assetRows);
    setNamespaceId((current) => current ?? namespaceRows[0]?.id ?? null);
  }

  const loadRegistry = useCallback(async (targetNamespaceId = namespaceId) => {
    if (!targetNamespaceId) return;
    setLoading(true);
    setError("");
    try {
      const [{ data: packageRows }, { data: keyRows }] = await Promise.all([
        packageRegistryApi.listPackages(targetNamespaceId),
        packageRegistryApi.listSigningKeys(targetNamespaceId),
      ]);
      setPackages(packageRows);
      setKeys(keyRows);
      const nextPackageId = packageRows.some((item) => item.public_id === selectedPackageId)
        ? selectedPackageId
        : packageRows[0]?.public_id ?? "";
      setSelectedPackageId(nextPackageId);
      setSigningKeyId((current) => (keyRows.some((item) => item.public_id === current) ? current : keyRows.find((item) => item.status === "ACTIVE")?.public_id ?? ""));
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setLoading(false);
    }
  }, [namespaceId, selectedPackageId]);

  const loadVersions = useCallback(async (packagePublicId = selectedPackageId) => {
    if (!packagePublicId) {
      setVersions([]);
      setSelectedVersion(null);
      return;
    }
    try {
      const { data } = await packageRegistryApi.listVersions(packagePublicId);
      setVersions(data);
      setSelectedVersion((current) => data.find((item) => item.public_id === current?.public_id) ?? data[0] ?? null);
    } catch (err) {
      setError(errorDetail(err));
    }
  }, [selectedPackageId]);

  useEffect(() => {
    void loadNamespacesAndAssets().catch((err) => {
      setError(errorDetail(err));
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    if (namespaceId) void loadRegistry(namespaceId);
  }, [namespaceId, loadRegistry]);

  useEffect(() => {
    void loadVersions(selectedPackageId);
    setManifestText("");
    setSbomText("");
    setCanonicalDigest("");
  }, [selectedPackageId, loadVersions]);

  async function createPackage() {
    if (!namespaceId || !packageName || !agentAssetId) return;
    setBusy("package"); setError(""); setNotice("");
    try {
      const { data } = await packageRegistryApi.createPackage({
        namespace_id: namespaceId,
        name: packageName,
        description: packageDescription || null,
        agent_asset_id: Number(agentAssetId),
      });
      setNotice(`Package ${data.name} 已创建。`);
      setPackageName(""); setPackageDescription("");
      await loadRegistry(namespaceId);
      setSelectedPackageId(data.public_id);
    } catch (err) { setError(errorDetail(err)); } finally { setBusy(""); }
  }

  async function createKey() {
    if (!namespaceId || !keyId || !publicKeyPem) return;
    setBusy("key"); setError(""); setNotice("");
    try {
      const { data } = await packageRegistryApi.createSigningKey({
        namespace_id: namespaceId, key_id: keyId, algorithm: "ED25519", public_key_pem: publicKeyPem,
      });
      setNotice(`可信签名密钥 ${data.key_id} 已登记。`);
      setKeyId(""); setPublicKeyPem("");
      await loadRegistry(namespaceId);
      setSigningKeyId(data.public_id);
    } catch (err) { setError(errorDetail(err)); } finally { setBusy(""); }
  }

  async function revokeKey(publicId: string) {
    setBusy(publicId); setError(""); setNotice("");
    try {
      await packageRegistryApi.revokeSigningKey(publicId);
      setNotice("签名密钥已吊销；历史验证记录保持不变。");
      await loadRegistry(namespaceId);
    } catch (err) { setError(errorDetail(err)); } finally { setBusy(""); }
  }

  function parseDocuments(): { manifest: AgentPackageManifest; sbom: Record<string, unknown> } {
    const manifest = JSON.parse(manifestText) as AgentPackageManifest;
    const sbom = JSON.parse(sbomText) as Record<string, unknown>;
    return { manifest, sbom };
  }

  async function canonicalize() {
    if (!namespaceId) return;
    setBusy("canonicalize"); setError(""); setNotice("");
    try {
      const { manifest } = parseDocuments();
      const { data } = await packageRegistryApi.canonicalizeManifest(namespaceId, manifest);
      setCanonicalDigest(data.manifest_digest);
      await navigator.clipboard.writeText(data.canonical_manifest);
      setNotice("规范化 Manifest 已复制；请在 CI/本地私钥侧对该 UTF-8 内容签名。");
    } catch (err) { setError(err instanceof SyntaxError ? "Manifest/SBOM JSON 无效。" : errorDetail(err)); } finally { setBusy(""); }
  }

  async function registerVersion() {
    if (!namespaceId || !selectedPackage || !signingKeyId || !signature) return;
    setBusy("version"); setError(""); setNotice("");
    try {
      const { manifest, sbom } = parseDocuments();
      const { data } = await packageRegistryApi.createVersion({
        namespace_id: namespaceId,
        package_public_id: selectedPackage.public_id,
        signing_key_public_id: signingKeyId,
        signature,
        idempotency_key: `ui-${selectedPackage.public_id}-${manifest.package_version}`,
        manifest,
        sbom_document: sbom,
      });
      setNotice(`PackageVersion ${data.version} 已通过签名与 SBOM 验证。`);
      setSignature("");
      await loadRegistry(namespaceId);
      await loadVersions(selectedPackage.public_id);
      setSelectedVersion(data);
    } catch (err) { setError(err instanceof SyntaxError ? "Manifest/SBOM JSON 无效。" : errorDetail(err)); } finally { setBusy(""); }
  }

  async function verifyVersion(publicId: string) {
    setBusy(publicId); setError(""); setVerification(null);
    try {
      const { data } = await packageRegistryApi.verifyVersion(publicId);
      setVerification(data);
      setNotice(data.verified ? "Manifest、签名、组件图、Provenance 与 MinIO SBOM 全部复验通过。" : "复验发现证据不一致。" );
    } catch (err) { setError(errorDetail(err)); } finally { setBusy(""); }
  }

  async function downloadSbom(publicId: string) {
    setBusy(`download-${publicId}`); setError("");
    try {
      const { data } = await packageRegistryApi.getSbomDownload(publicId);
      window.open(data.download_url, "_blank", "noopener,noreferrer");
    } catch (err) { setError(errorDetail(err)); } finally { setBusy(""); }
  }

  return (
    <div className="app-page max-w-7xl space-y-6">
      <PageHeader
        eyebrow="PACKAGE REGISTRY"
        title="Agent Package Registry"
        description="把 Agent manifest、组件 DAG、SBOM、Ed25519 签名和构建 Provenance 固定为不可变版本，为 Release Control 提供精确候选主体。"
        actions={<Button variant="secondary" icon={<RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />} onClick={() => void loadRegistry()} disabled={loading}>刷新</Button>}
      />

      <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <label className="text-sm font-medium text-slate-700" htmlFor="package-namespace">命名空间</label>
        <select id="package-namespace" aria-label="Package 命名空间" className="field-control max-w-xs" value={namespaceId ?? ""} onChange={(event) => setNamespaceId(Number(event.target.value))}>
          {namespaces.map((namespace) => <option key={namespace.id} value={namespace.id}>{namespace.name}</option>)}
        </select>
      </div>
      {error ? <div className="soft-rose rounded-lg px-4 py-3 text-sm">{error}</div> : null}
      {notice ? <div className="soft-emerald rounded-lg px-4 py-3 text-sm">{notice}</div> : null}

      <div className="grid gap-4 md:grid-cols-4">
        <MetricCard label="Packages" value={packages.length} detail="Namespace-scoped identity" icon={Box} />
        <MetricCard label="Verified versions" value={packages.reduce((sum, item) => sum + item.version_count, 0)} detail="签名 + SBOM 不可变版本" icon={PackageCheck} tone="indigo" />
        <MetricCard label="Active keys" value={activeKeys.length} detail="Ed25519 公钥信任" icon={KeyRound} />
        <MetricCard label="Agent assets" value={visibleAssets.length} detail="可绑定的 Agent 根资产" icon={ShieldCheck} />
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="创建 Agent Package" description="身份只绑定同 Namespace 的 Agent 资产。" padded>
          <div className="space-y-3">
            <input aria-label="Package 名称" className="field-control" placeholder="research-agent" value={packageName} onChange={(event) => setPackageName(event.target.value)} />
            <input aria-label="Package 描述" className="field-control" placeholder="用途与边界" value={packageDescription} onChange={(event) => setPackageDescription(event.target.value)} />
            <select aria-label="Package Agent 资产" className="field-control" value={agentAssetId} onChange={(event) => setAgentAssetId(event.target.value)}>
              <option value="">选择 Agent 资产</option>
              {visibleAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name} · {asset.external_id ?? `asset:${asset.id}`}</option>)}
            </select>
            <Button icon={<Box className="h-4 w-4" />} onClick={() => void createPackage()} disabled={busy === "package" || !packageName || !agentAssetId}>创建 Package</Button>
          </div>
        </Card>

        <Card title="可信签名密钥" description="只登记 Ed25519 公钥；私钥必须留在 CI/构建机。" padded>
          <div className="space-y-3">
            <input aria-label="签名 Key ID" className="field-control" placeholder="ci-release-key-2026" value={keyId} onChange={(event) => setKeyId(event.target.value)} />
            <textarea aria-label="Ed25519 公钥 PEM" className="field-control min-h-28 font-mono text-xs" placeholder="-----BEGIN PUBLIC KEY-----" value={publicKeyPem} onChange={(event) => setPublicKeyPem(event.target.value)} />
            <Button icon={<KeyRound className="h-4 w-4" />} onClick={() => void createKey()} disabled={busy === "key" || !keyId || !publicKeyPem}>登记公钥</Button>
            <div className="divide-y divide-slate-100 rounded-md border border-slate-200">
              {keys.map((key) => (
                <div key={key.public_id} className="flex items-center justify-between gap-3 p-3 text-sm">
                  <div className="min-w-0"><div className="font-medium text-slate-800">{key.key_id}</div><div className="mt-1 font-mono text-xs text-slate-500">{shortDigest(key.public_key_fingerprint)}</div></div>
                  <div className="flex items-center gap-2"><Badge tone={key.status === "ACTIVE" ? "emerald" : "neutral"}>{key.status}</Badge>{key.status === "ACTIVE" ? <Button size="sm" variant="secondary" danger onClick={() => void revokeKey(key.public_id)} disabled={busy === key.public_id}>吊销</Button> : null}</div>
                </div>
              ))}
              {!keys.length ? <EmptyState text="尚未登记可信公钥。" /> : null}
            </div>
          </div>
        </Card>
      </div>

      <Card title="Package identities" description="选择一个 Package 查看并登记不可变版本。" padded>
        <div className="grid gap-3 md:grid-cols-3">
          {packages.map((pkg) => (
            <button key={pkg.public_id} onClick={() => setSelectedPackageId(pkg.public_id)} className={`rounded-lg border p-4 text-left ${selectedPackageId === pkg.public_id ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-white"}`}>
              <div className="flex items-center justify-between"><span className="font-semibold text-slate-900">{pkg.name}</span><Badge tone="emerald">{pkg.status}</Badge></div>
              <div className="mt-2 text-sm text-slate-500">{pkg.agent_asset_ref}</div>
              <div className="mt-3 flex items-center justify-between"><MonoPill>{pkg.public_id}</MonoPill><span className="text-xs text-slate-500">{pkg.version_count} versions</span></div>
            </button>
          ))}
          {!loading && !packages.length ? <EmptyState text="当前 Namespace 还没有 Agent Package。" /> : null}
        </div>
      </Card>

      {selectedPackage ? (
        <Card title={`登记 ${selectedPackage.name} 的签名版本`} description="先规范化 Manifest，在外部私钥侧签名，再连同完整 SBOM 提交。" padded>
          <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            Manifest 与 SBOM 必须来自可信构建流水线。控制台不会生成或补齐构建证据。
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            <div className="space-y-3">
              <label className="table-label" htmlFor="package-manifest">Agent Package v2 Manifest</label>
              <textarea id="package-manifest" aria-label="Agent Package Manifest JSON" className="field-control min-h-[420px] font-mono text-xs" placeholder="粘贴构建流水线导出的 Agent Package v2 Manifest JSON" value={manifestText} onChange={(event) => setManifestText(event.target.value)} />
            </div>
            <div className="space-y-3">
              <label className="table-label" htmlFor="package-sbom">CycloneDX / SPDX SBOM</label>
              <textarea id="package-sbom" aria-label="Package SBOM JSON" className="field-control min-h-48 font-mono text-xs" placeholder="粘贴与该 Manifest 对应的 CycloneDX 或 SPDX JSON" value={sbomText} onChange={(event) => setSbomText(event.target.value)} />
              <select aria-label="Package 签名密钥" className="field-control" value={signingKeyId} onChange={(event) => setSigningKeyId(event.target.value)}><option value="">选择 ACTIVE 公钥</option>{activeKeys.map((key) => <option key={key.public_id} value={key.public_id}>{key.key_id}</option>)}</select>
              <textarea aria-label="Manifest Ed25519 签名" className="field-control min-h-24 font-mono text-xs" placeholder="Base64 Ed25519 signature" value={signature} onChange={(event) => setSignature(event.target.value)} />
              {canonicalDigest ? <div className="soft-indigo rounded-md p-3 text-sm">Manifest digest：<span className="font-mono">{canonicalDigest}</span></div> : null}
              <div className="flex flex-wrap gap-2"><Button variant="secondary" icon={<FileJson className="h-4 w-4" />} onClick={() => void canonicalize()} disabled={busy === "canonicalize"}>规范化并复制</Button><Button icon={<PackageCheck className="h-4 w-4" />} onClick={() => void registerVersion()} disabled={busy === "version" || !signature || !signingKeyId}>验证并登记版本</Button></div>
            </div>
          </div>
        </Card>
      ) : null}

      <Card title="不可变 PackageVersions" description="签名、组件图、SBOM 与 Provenance 均绑定到同一 Manifest digest。" padded>
        <div className="space-y-3">
          {versions.map((version) => (
            <button key={version.public_id} onClick={() => { setSelectedVersion(version); setVerification(null); }} className={`w-full rounded-lg border p-4 text-left ${selectedVersion?.public_id === version.public_id ? "border-indigo-300 bg-indigo-50" : "border-slate-200"}`}>
              <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><span className="font-semibold">v{version.version}</span><Badge tone="emerald">{version.status}</Badge><Badge tone={version.signing_key_status === "ACTIVE" ? "indigo" : "amber"}>{version.signing_key_id} · {version.signing_key_status}</Badge></div><span className="text-xs text-slate-500">{formatDate(version.created_at)}</span></div>
              <div className="mt-3 grid gap-2 text-xs text-slate-500 md:grid-cols-4"><span>manifest {shortDigest(version.manifest_digest)}</span><span>graph {shortDigest(version.graph_digest)}</span><span>provenance {shortDigest(version.provenance_digest)}</span><span>SBOM {shortDigest(version.sbom.document_sha256)}</span></div>
            </button>
          ))}
          {!versions.length ? <EmptyState text="该 Package 还没有已验证版本。" /> : null}
        </div>
      </Card>

      {selectedVersion ? (
        <Card title={`v${selectedVersion.version} 证据详情`} description="此版本可被 Deployment 与 ReleaseCandidate 精确引用。" padded>
          <div className="space-y-5">
            <div className="flex flex-wrap gap-2"><Button icon={<ShieldCheck className="h-4 w-4" />} onClick={() => void verifyVersion(selectedVersion.public_id)} disabled={busy === selectedVersion.public_id}>复验全部证据</Button><Button variant="secondary" icon={<Download className="h-4 w-4" />} onClick={() => void downloadSbom(selectedVersion.public_id)} disabled={busy === `download-${selectedVersion.public_id}`}>下载 SBOM</Button></div>
            {verification ? <div className={verification.verified ? "soft-emerald rounded-lg p-4" : "soft-rose rounded-lg p-4"}><div className="flex items-center gap-2 font-semibold">{verification.verified ? <CheckCircle2 className="h-4 w-4" /> : null}{verification.verified ? "VERIFIED" : "EVIDENCE MISMATCH"}</div><div className="mt-2 text-sm">signature={String(verification.signature_valid)} · sbom digest={String(verification.sbom_digest_valid)} · component coverage={String(verification.sbom_component_coverage_valid)} · key={verification.signing_key_status}</div></div> : null}
            <div className="grid gap-5 lg:grid-cols-2">
              <div><div className="table-label mb-2">组件节点</div><div className="space-y-2">{selectedVersion.components.map((component) => <div key={component.component_ref} className="rounded-md border border-slate-200 p-3"><div className="flex items-center gap-2"><Box className="h-4 w-4 text-indigo-500" /><span className="font-mono text-xs">{component.component_ref}</span></div><div className="mt-1 text-xs text-slate-500">{component.media_type} · {shortDigest(component.sha256)}</div></div>)}</div></div>
              <div><div className="table-label mb-2">依赖边</div><div className="space-y-2">{selectedVersion.dependencies.map((edge, index) => <div key={`${edge.from_component}-${edge.to_component}-${index}`} className="rounded-md border border-slate-200 p-3 text-xs"><div className="flex items-center gap-2"><Link2 className="h-4 w-4 text-indigo-500" /><Badge tone="indigo">{edge.relationship}</Badge></div><div className="mt-2 font-mono">{edge.from_component}<br />→ {edge.to_component}</div></div>)}{!selectedVersion.dependencies.length ? <EmptyState text="该版本没有组件依赖边。" /> : null}</div></div>
            </div>
            <div className="grid gap-4 text-sm md:grid-cols-3"><div className="surface-muted p-4"><div className="table-label">SBOM</div><div className="mt-2">{selectedVersion.sbom.format} {selectedVersion.sbom.spec_version}</div><div className="mt-1 text-slate-500">{selectedVersion.sbom.component_count} components · {selectedVersion.sbom.size_bytes} bytes</div></div><div className="surface-muted p-4"><div className="table-label">Build provenance</div><div className="mt-2">{selectedVersion.manifest.provenance.builder_id}</div><div className="mt-1 text-slate-500">{selectedVersion.manifest.provenance.build_id} · {selectedVersion.manifest.provenance.source_revision.slice(0, 12)}</div></div><div className="surface-muted p-4"><div className="table-label">Signature</div><div className="mt-2">{selectedVersion.signature_algorithm}</div><div className="mt-1 text-slate-500">{shortDigest(selectedVersion.signature_digest)}</div></div></div>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
