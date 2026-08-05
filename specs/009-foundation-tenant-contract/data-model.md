# Data Model: Tenant Remediation

No new business table is required before contract DDL. The durable inputs and receipts are:

## TenantRemediationManifest v1

| Field | Type | Rule |
|---|---|---|
| schema_version | integer | exactly `1` |
| manifest_id | string | stable operator identifier |
| change_ticket | string | approved change/audit reference |
| reason | string | bounded human justification |
| approved_by_user_id | integer | active system admin |
| assignments | array | 1–1000 explicit target assignments |

## TenantRemediationAssignment

| Field | Type | Rule |
|---|---|---|
| target_type | enum | one of the five Foundation entity types |
| target_id | integer | positive primary key |
| namespace_id | integer | existing Namespace |
| reason | string/null | optional target-specific justification |

## Audit receipt

Each changed target creates `AuditLog(action="foundation.tenant.remediated")` in the same transaction with:

- manifest ID and SHA-256;
- change ticket;
- target type/ID;
- Namespace ID;
- approved administrator ID/username;
- bounded reason.

No object content, prompt, summary, credential, URL or metadata is copied into the receipt.
