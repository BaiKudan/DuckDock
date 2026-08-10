import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PackageRegistryPage from "../pages/PackageRegistryPage";

const mocks = vi.hoisted(() => ({
  listPackages: vi.fn(),
  listSigningKeys: vi.fn(),
  listVersions: vi.fn(),
  verifyVersion: vi.fn(),
}));

const manifest = {
  schema_version: "2.0" as const,
  package_id: "pkg_test",
  package_version: "1.2.3",
  namespace_id: "production",
  agent: { asset_id: "hermes-agent", name: "Hermes Agent" },
  runtime: {
    harness: "hermes",
    entrypoint: "agents/main.yaml",
    minimum_harness_version: "1.0.0",
    capabilities: ["model_call", "tool_call"],
  },
  artifacts: [
    {
      type: "runtime_bundle" as const,
      asset_id: "runtime",
      version_id: "runtime-v1",
      uri: "s3://packages/runtime-v1.tar.gz",
      sha256: "a".repeat(64),
      media_type: "application/gzip",
    },
  ],
  provenance: {
    source_repository: "https://github.com/BaiKudan/DuckDock",
    source_revision: "b".repeat(40),
    built_at: "2026-08-04T06:00:00Z",
    builder_id: "duckdock-ci",
    build_id: "build-123",
  },
  telemetry: {
    schema_version: "1.0" as const,
    content_policy: "metadata_only" as const,
    required_attributes: [
      "duckdock.namespace_id",
      "duckdock.deployment_revision",
      "duckdock.component_digests",
    ],
  },
  evaluation_policy_id: "quality-policy-v1",
  component_graph: { edges: [] },
  sbom: {
    format: "cyclonedx-json" as const,
    spec_version: "1.6",
    document_sha256: "c".repeat(64),
    media_type: "application/vnd.cyclonedx+json" as const,
  },
};

const version = {
  public_id: "pkgv_test",
  namespace_id: 7,
  package_public_id: "pkg_test",
  package_name: "hermes-production",
  version: "1.2.3",
  status: "VERIFIED" as const,
  manifest_schema_version: "2.0",
  manifest,
  manifest_digest: "d".repeat(64),
  graph_digest: "e".repeat(64),
  evaluation_policy_id: "quality-policy-v1",
  provenance_digest: "f".repeat(64),
  signing_key_public_id: "pkey_test",
  signing_key_id: "ci-release-key",
  signing_key_fingerprint: "1".repeat(64),
  signing_key_status: "ACTIVE" as const,
  signature_algorithm: "ED25519" as const,
  signature: "signed-evidence",
  signature_digest: "2".repeat(64),
  signature_verified_at: "2026-08-04T06:00:01Z",
  idempotency_key: "test-register-1",
  created_by_user_id: 1,
  created_at: "2026-08-04T06:00:01Z",
  components: [
    {
      position: 0,
      component_ref: "runtime:runtime@runtime-v1",
      component_type: "runtime_bundle" as const,
      asset_ref: "runtime",
      version_ref: "runtime-v1",
      uri: "s3://packages/runtime-v1.tar.gz",
      sha256: "a".repeat(64),
      media_type: "application/gzip",
    },
  ],
  dependencies: [],
  sbom: {
    public_id: "psbom_test",
    format: "cyclonedx-json" as const,
    spec_version: "1.6",
    media_type: "application/vnd.cyclonedx+json",
    document_sha256: "c".repeat(64),
    size_bytes: 512,
    component_count: 1,
    stored_at: "2026-08-04T06:00:01Z",
  },
};

vi.mock("../api/client", () => ({
  namespacesApi: {
    list: vi.fn().mockResolvedValue({ data: [{ id: 7, name: "production" }] }),
  },
  controlPlaneApi: {
    listAssets: vi.fn().mockResolvedValue({
      data: [
        {
          id: 42,
          public_id: "asset_hermes",
          namespace_id: 7,
          asset_type: "agent",
          name: "Hermes Agent",
        },
      ],
    }),
  },
  packageRegistryApi: {
    listPackages: mocks.listPackages,
    listSigningKeys: mocks.listSigningKeys,
    listVersions: mocks.listVersions,
    verifyVersion: mocks.verifyVersion,
    createPackage: vi.fn(),
    createSigningKey: vi.fn(),
    revokeSigningKey: vi.fn(),
    canonicalizeManifest: vi.fn(),
    createVersion: vi.fn(),
    getSbomDownload: vi.fn(),
  },
}));

describe("PackageRegistryPage", () => {
  beforeEach(() => {
    mocks.listPackages.mockResolvedValue({
      data: [
        {
          public_id: "pkg_test",
          namespace_id: 7,
          namespace_ref: "production",
          name: "hermes-production",
          description: "Signed production Agent",
          agent_asset_id: 42,
          agent_asset_ref: "hermes-agent",
          status: "ACTIVE",
          created_by_user_id: 1,
          created_at: "2026-08-04T06:00:00Z",
          updated_at: "2026-08-04T06:00:00Z",
          version_count: 1,
        },
      ],
    });
    mocks.listSigningKeys.mockResolvedValue({
      data: [
        {
          public_id: "pkey_test",
          namespace_id: 7,
          key_id: "ci-release-key",
          algorithm: "ED25519",
          public_key_fingerprint: "1".repeat(64),
          status: "ACTIVE",
          created_by_user_id: 1,
          revoked_by_user_id: null,
          revoked_at: null,
          created_at: "2026-08-04T06:00:00Z",
        },
      ],
    });
    mocks.listVersions.mockResolvedValue({ data: [version] });
    mocks.verifyVersion.mockResolvedValue({
      data: {
        package_version_public_id: version.public_id,
        manifest_digest: version.manifest_digest,
        graph_digest: version.graph_digest,
        provenance_digest: version.provenance_digest,
        signature_valid: true,
        signature_verified_at: "2026-08-04T06:00:02Z",
        signing_key_status: "ACTIVE",
        signing_key_fingerprint: "1".repeat(64),
        sbom_digest_valid: true,
        sbom_component_coverage_valid: true,
        sbom_document_sha256: "c".repeat(64),
        verified: true,
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows immutable package evidence and re-verifies the complete evidence chain", async () => {
    const user = userEvent.setup();
    render(<PackageRegistryPage />);

    expect(await screen.findByText("hermes-production")).toBeInTheDocument();
    expect(await screen.findByText("v1.2.3")).toBeInTheDocument();
    expect(screen.getByText("runtime:runtime@runtime-v1")).toBeInTheDocument();
    expect(screen.getByText("cyclonedx-json 1.6")).toBeInTheDocument();
    expect(screen.getByText("Manifest 与 SBOM 必须来自可信构建流水线。控制台不会生成或补齐构建证据。"))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Agent Package Manifest JSON")).toHaveValue("");
    expect(screen.getByLabelText("Package SBOM JSON")).toHaveValue("");

    await user.click(screen.getByRole("button", { name: "复验全部证据" }));

    await waitFor(() => expect(mocks.verifyVersion).toHaveBeenCalledWith("pkgv_test"));
    expect(await screen.findByText("signature=true · sbom digest=true · component coverage=true · key=ACTIVE"))
      .toBeInTheDocument();
    expect(screen.getAllByText("VERIFIED").length).toBeGreaterThanOrEqual(2);
  });
});
