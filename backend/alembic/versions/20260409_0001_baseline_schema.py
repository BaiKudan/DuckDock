"""baseline schema

Revision ID: 20260409_0001
Revises:
Create Date: 2026-04-09 12:10:00

Static baseline DDL for the current DuckDock schema. This intentionally avoids
dynamic metadata bootstrapping so migration drift checks can reason about the
baseline without importing live models.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260409_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('org_units',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=True),
    sa.Column('unit_type', sa.Enum('COMPANY', 'SUBSIDIARY', 'DIVISION', 'DEPARTMENT', 'TEAM', 'BRANCH', name='orgunittype'), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('path', sa.String(length=512), nullable=False),
    sa.Column('legal_entity', sa.String(length=128), nullable=True),
    sa.Column('region', sa.String(length=64), nullable=True),
    sa.Column('cost_center', sa.String(length=64), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['org_units.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_org_units_code'), 'org_units', ['code'], unique=True)
    op.create_index(op.f('ix_org_units_cost_center'), 'org_units', ['cost_center'], unique=False)
    op.create_index(op.f('ix_org_units_parent_id'), 'org_units', ['parent_id'], unique=False)
    op.create_index(op.f('ix_org_units_unit_type'), 'org_units', ['unit_type'], unique=False)
    op.create_table('permissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('scope', sa.Enum('SYSTEM', 'ORG', 'NAMESPACE', name='rolescope'), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_permissions_key'), 'permissions', ['key'], unique=True)
    op.create_index(op.f('ix_permissions_scope'), 'permissions', ['scope'], unique=False)
    op.create_table('roles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('scope', sa.Enum('SYSTEM', 'ORG', 'NAMESPACE', name='rolescope'), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_system', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_roles_key'), 'roles', ['key'], unique=True)
    op.create_index(op.f('ix_roles_scope'), 'roles', ['scope'], unique=False)
    op.create_table('runtime_instances',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('base_url', sa.String(length=512), nullable=True),
    sa.Column('deploy_type', sa.Enum('SAAS', 'PRIVATE', 'ON_PREM', 'OFFLINE', name='runtimedeploytype'), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'DEGRADED', 'DISABLED', name='runtimestatus'), nullable=False),
    sa.Column('credential_ref', sa.String(length=255), nullable=True),
    sa.Column('capabilities', sa.JSON(), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_runtime_instances_deploy_type'), 'runtime_instances', ['deploy_type'], unique=False)
    op.create_index(op.f('ix_runtime_instances_name'), 'runtime_instances', ['name'], unique=False)
    op.create_index(op.f('ix_runtime_instances_provider'), 'runtime_instances', ['provider'], unique=False)
    op.create_index(op.f('ix_runtime_instances_status'), 'runtime_instances', ['status'], unique=False)
    op.create_table('sso_provider_configs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider_type', sa.Enum('OIDC', 'LDAP', name='ssoprovidertype'), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('issuer_url', sa.String(length=512), nullable=True),
    sa.Column('client_id', sa.String(length=255), nullable=True),
    sa.Column('client_secret', sa.Text(), nullable=True),
    sa.Column('ldap_server_url', sa.String(length=512), nullable=True),
    sa.Column('ldap_bind_dn', sa.String(length=512), nullable=True),
    sa.Column('ldap_bind_password', sa.Text(), nullable=True),
    sa.Column('ldap_user_search_base', sa.String(length=512), nullable=True),
    sa.Column('ldap_user_search_filter', sa.String(length=512), nullable=True),
    sa.Column('ldap_group_search_base', sa.String(length=512), nullable=True),
    sa.Column('attribute_mapping', sa.JSON(), nullable=True),
    sa.Column('extra_config', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sso_provider_configs_name'), 'sso_provider_configs', ['name'], unique=True)
    op.create_index(op.f('ix_sso_provider_configs_provider_type'), 'sso_provider_configs', ['provider_type'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=True),
    sa.Column('hashed_password', sa.String(length=255), nullable=False),
    sa.Column('system_role', sa.Enum('ADMIN', 'USER', name='systemrole'), nullable=False),
    sa.Column('auth_source', sa.Enum('LOCAL', 'OIDC', 'LDAP', name='authsource'), nullable=False),
    sa.Column('external_subject', sa.String(length=255), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_external_subject'), 'users', ['external_subject'], unique=True)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('adapter_cursors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=False),
    sa.Column('adapter_name', sa.String(length=128), nullable=False),
    sa.Column('stream', sa.Enum('CAPABILITY', 'PRINCIPAL', 'ASSET', 'WORKTRACE', 'ARTIFACT', 'EVIDENCE', 'BACKUP_MANIFEST', 'UNKNOWN', name='rawrecordstream'), nullable=False),
    sa.Column('cursor_json', sa.JSON(), nullable=True),
    sa.Column('high_watermark', sa.String(length=255), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('runtime_id', 'stream', name='uq_adapter_cursors_runtime_stream')
    )
    op.create_index(op.f('ix_adapter_cursors_adapter_name'), 'adapter_cursors', ['adapter_name'], unique=False)
    op.create_index(op.f('ix_adapter_cursors_high_watermark'), 'adapter_cursors', ['high_watermark'], unique=False)
    op.create_index(op.f('ix_adapter_cursors_last_success_at'), 'adapter_cursors', ['last_success_at'], unique=False)
    op.create_index(op.f('ix_adapter_cursors_provider'), 'adapter_cursors', ['provider'], unique=False)
    op.create_index(op.f('ix_adapter_cursors_runtime_id'), 'adapter_cursors', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_adapter_cursors_stream'), 'adapter_cursors', ['stream'], unique=False)
    op.create_table('ai_assets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_type', sa.Enum('SKILL', 'AGENT', 'PROMPT', 'WORKFLOW', 'MCP', 'TOOL', 'KNOWLEDGE_BASE', 'SCHEDULED_TASK', 'CREDENTIAL_REF', 'WORKSPACE', 'OTHER', name='assettype'), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('source_provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=False),
    sa.Column('source_runtime_id', sa.Integer(), nullable=True),
    sa.Column('external_id', sa.String(length=255), nullable=True),
    sa.Column('status', sa.Enum('ACTIVE', 'INACTIVE', 'ARCHIVED', 'ORPHANED', 'RISKY', 'TRANSFERRED', name='assetstatus'), nullable=False),
    sa.Column('criticality', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='criticality'), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_runtime_id'], ['runtime_instances.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_provider', 'source_runtime_id', 'external_id', name='uq_ai_assets_source_external')
    )
    op.create_index(op.f('ix_ai_assets_asset_type'), 'ai_assets', ['asset_type'], unique=False)
    op.create_index(op.f('ix_ai_assets_content_hash'), 'ai_assets', ['content_hash'], unique=False)
    op.create_index(op.f('ix_ai_assets_external_id'), 'ai_assets', ['external_id'], unique=False)
    op.create_index(op.f('ix_ai_assets_last_seen_at'), 'ai_assets', ['last_seen_at'], unique=False)
    op.create_index(op.f('ix_ai_assets_name'), 'ai_assets', ['name'], unique=False)
    op.create_index(op.f('ix_ai_assets_source_provider'), 'ai_assets', ['source_provider'], unique=False)
    op.create_index(op.f('ix_ai_assets_source_runtime_id'), 'ai_assets', ['source_runtime_id'], unique=False)
    op.create_index(op.f('ix_ai_assets_status'), 'ai_assets', ['status'], unique=False)
    op.create_table('analysis_workers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('worker_key', sa.String(length=64), nullable=False),
    sa.Column('token_prefix', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.String(length=255), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'DISABLED', 'STALE', name='analysisworkerstatus'), nullable=False),
    sa.Column('capabilities_json', sa.JSON(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_prefix', name='uq_analysis_workers_token_prefix'),
    sa.UniqueConstraint('worker_key', name='uq_analysis_workers_worker_key')
    )
    op.create_index(op.f('ix_analysis_workers_created_by'), 'analysis_workers', ['created_by'], unique=False)
    op.create_index(op.f('ix_analysis_workers_last_seen_at'), 'analysis_workers', ['last_seen_at'], unique=False)
    op.create_index(op.f('ix_analysis_workers_name'), 'analysis_workers', ['name'], unique=False)
    op.create_index(op.f('ix_analysis_workers_status'), 'analysis_workers', ['status'], unique=False)
    op.create_index(op.f('ix_analysis_workers_token_prefix'), 'analysis_workers', ['token_prefix'], unique=False)
    op.create_index(op.f('ix_analysis_workers_worker_key'), 'analysis_workers', ['worker_key'], unique=False)
    op.create_table('collection_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=True),
    sa.Column('trigger_type', sa.Enum('MANUAL', 'SCHEDULED', 'OFFBOARDING', 'PROJECT_HANDOVER', 'WEBHOOK', name='collectiontriggertype'), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'PARTIAL_FAILED', 'CANCELLED', name='jobstatus'), nullable=False),
    sa.Column('scope_json', sa.JSON(), nullable=True),
    sa.Column('summary_json', sa.JSON(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_collection_jobs_runtime_id'), 'collection_jobs', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_collection_jobs_status'), 'collection_jobs', ['status'], unique=False)
    op.create_index(op.f('ix_collection_jobs_trigger_type'), 'collection_jobs', ['trigger_type'], unique=False)
    op.create_table('credential_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=True),
    sa.Column('ciphertext', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('namespaces',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_namespaces_deleted_at'), 'namespaces', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_namespaces_name'), 'namespaces', ['name'], unique=True)
    op.create_table('provider_principals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('principal_type', sa.Enum('USER', 'BOT', 'SERVICE_ACCOUNT', 'GROUP', 'UNKNOWN', name='providerprincipaltype'), nullable=False),
    sa.Column('display_name', sa.String(length=255), nullable=True),
    sa.Column('username', sa.String(length=128), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('runtime_id', 'external_id', name='uq_provider_principals_runtime_external')
    )
    op.create_index(op.f('ix_provider_principals_email'), 'provider_principals', ['email'], unique=False)
    op.create_index(op.f('ix_provider_principals_external_id'), 'provider_principals', ['external_id'], unique=False)
    op.create_index(op.f('ix_provider_principals_last_seen_at'), 'provider_principals', ['last_seen_at'], unique=False)
    op.create_index(op.f('ix_provider_principals_principal_type'), 'provider_principals', ['principal_type'], unique=False)
    op.create_index(op.f('ix_provider_principals_provider'), 'provider_principals', ['provider'], unique=False)
    op.create_index(op.f('ix_provider_principals_runtime_id'), 'provider_principals', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_provider_principals_user_id'), 'provider_principals', ['user_id'], unique=False)
    op.create_index(op.f('ix_provider_principals_username'), 'provider_principals', ['username'], unique=False)
    op.create_table('role_permissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.Column('permission_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('role_id', 'permission_id', name='uq_role_permissions_role_permission')
    )
    op.create_index(op.f('ix_role_permissions_permission_id'), 'role_permissions', ['permission_id'], unique=False)
    op.create_index(op.f('ix_role_permissions_role_id'), 'role_permissions', ['role_id'], unique=False)
    op.create_table('runtime_capability_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=False),
    sa.Column('adapter_name', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('version', sa.String(length=128), nullable=True),
    sa.Column('capabilities_json', sa.JSON(), nullable=False),
    sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_runtime_capability_snapshots_adapter_name'), 'runtime_capability_snapshots', ['adapter_name'], unique=False)
    op.create_index(op.f('ix_runtime_capability_snapshots_collected_at'), 'runtime_capability_snapshots', ['collected_at'], unique=False)
    op.create_index(op.f('ix_runtime_capability_snapshots_provider'), 'runtime_capability_snapshots', ['provider'], unique=False)
    op.create_index(op.f('ix_runtime_capability_snapshots_runtime_id'), 'runtime_capability_snapshots', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_runtime_capability_snapshots_source'), 'runtime_capability_snapshots', ['source'], unique=False)
    op.create_index(op.f('ix_runtime_capability_snapshots_status'), 'runtime_capability_snapshots', ['status'], unique=False)
    op.create_table('runtime_report_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('token_prefix', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_prefix', name='uq_runtime_report_tokens_prefix')
    )
    op.create_index(op.f('ix_runtime_report_tokens_created_by'), 'runtime_report_tokens', ['created_by'], unique=False)
    op.create_index(op.f('ix_runtime_report_tokens_expires_at'), 'runtime_report_tokens', ['expires_at'], unique=False)
    op.create_index(op.f('ix_runtime_report_tokens_is_active'), 'runtime_report_tokens', ['is_active'], unique=False)
    op.create_index(op.f('ix_runtime_report_tokens_last_used_at'), 'runtime_report_tokens', ['last_used_at'], unique=False)
    op.create_index(op.f('ix_runtime_report_tokens_runtime_id'), 'runtime_report_tokens', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_runtime_report_tokens_token_prefix'), 'runtime_report_tokens', ['token_prefix'], unique=False)
    op.create_table('user_affiliations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('org_unit_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=128), nullable=True),
    sa.Column('employee_no', sa.String(length=64), nullable=True),
    sa.Column('manager_user_id', sa.Integer(), nullable=True),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('employment_status', sa.Enum('ACTIVE', 'ONBOARDING', 'LEAVE', 'OFFBOARDED', name='employmentstatus'), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cost_center_override', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['manager_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_unit_id'], ['org_units.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'org_unit_id', name='uq_user_affiliations_user_org')
    )
    op.create_index(op.f('ix_user_affiliations_cost_center_override'), 'user_affiliations', ['cost_center_override'], unique=False)
    op.create_index(op.f('ix_user_affiliations_employee_no'), 'user_affiliations', ['employee_no'], unique=False)
    op.create_index(op.f('ix_user_affiliations_employment_status'), 'user_affiliations', ['employment_status'], unique=False)
    op.create_index(op.f('ix_user_affiliations_manager_user_id'), 'user_affiliations', ['manager_user_id'], unique=False)
    op.create_index(op.f('ix_user_affiliations_org_unit_id'), 'user_affiliations', ['org_unit_id'], unique=False)
    op.create_index(op.f('ix_user_affiliations_user_id'), 'user_affiliations', ['user_id'], unique=False)
    op.create_table('user_handover_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('position_title', sa.String(length=128), nullable=True),
    sa.Column('employee_no', sa.String(length=64), nullable=True),
    sa.Column('manager_user_id', sa.Integer(), nullable=True),
    sa.Column('handover_receiver_user_id', sa.Integer(), nullable=True),
    sa.Column('employment_status', sa.Enum('ACTIVE', 'ONBOARDING', 'LEAVE', 'OFFBOARDED', name='employmentstatus'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['handover_receiver_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['manager_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_handover_profiles_employee_no'), 'user_handover_profiles', ['employee_no'], unique=False)
    op.create_index(op.f('ix_user_handover_profiles_employment_status'), 'user_handover_profiles', ['employment_status'], unique=False)
    op.create_index(op.f('ix_user_handover_profiles_handover_receiver_user_id'), 'user_handover_profiles', ['handover_receiver_user_id'], unique=False)
    op.create_index(op.f('ix_user_handover_profiles_manager_user_id'), 'user_handover_profiles', ['manager_user_id'], unique=False)
    op.create_index(op.f('ix_user_handover_profiles_user_id'), 'user_handover_profiles', ['user_id'], unique=True)
    op.create_table('adapter_errors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('collection_job_id', sa.Integer(), nullable=True),
    sa.Column('runtime_id', sa.Integer(), nullable=True),
    sa.Column('provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=True),
    sa.Column('adapter_name', sa.String(length=128), nullable=False),
    sa.Column('step_name', sa.String(length=96), nullable=True),
    sa.Column('error_code', sa.String(length=96), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('retryable', sa.Boolean(), nullable=False),
    sa.Column('context_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['collection_job_id'], ['collection_jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_adapter_errors_adapter_name'), 'adapter_errors', ['adapter_name'], unique=False)
    op.create_index(op.f('ix_adapter_errors_collection_job_id'), 'adapter_errors', ['collection_job_id'], unique=False)
    op.create_index(op.f('ix_adapter_errors_created_at'), 'adapter_errors', ['created_at'], unique=False)
    op.create_index(op.f('ix_adapter_errors_error_code'), 'adapter_errors', ['error_code'], unique=False)
    op.create_index(op.f('ix_adapter_errors_provider'), 'adapter_errors', ['provider'], unique=False)
    op.create_index(op.f('ix_adapter_errors_runtime_id'), 'adapter_errors', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_adapter_errors_step_name'), 'adapter_errors', ['step_name'], unique=False)
    op.create_table('adapter_run_steps',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('collection_job_id', sa.Integer(), nullable=False),
    sa.Column('step_name', sa.String(length=96), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', name='adapterstepstatus'), nullable=False),
    sa.Column('summary_json', sa.JSON(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['collection_job_id'], ['collection_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_adapter_run_steps_collection_job_id'), 'adapter_run_steps', ['collection_job_id'], unique=False)
    op.create_index(op.f('ix_adapter_run_steps_finished_at'), 'adapter_run_steps', ['finished_at'], unique=False)
    op.create_index(op.f('ix_adapter_run_steps_started_at'), 'adapter_run_steps', ['started_at'], unique=False)
    op.create_index(op.f('ix_adapter_run_steps_status'), 'adapter_run_steps', ['status'], unique=False)
    op.create_index(op.f('ix_adapter_run_steps_step_name'), 'adapter_run_steps', ['step_name'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('resource_type', sa.String(length=64), nullable=True),
    sa.Column('resource_id', sa.Integer(), nullable=True),
    sa.Column('namespace_id', sa.Integer(), nullable=True),
    sa.Column('details', sa.JSON(), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_namespace_id'), 'audit_logs', ['namespace_id'], unique=False)
    op.create_table('clinic_evaluations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', name='evalstatus'), nullable=False),
    sa.Column('overall_score', sa.Float(), nullable=True),
    sa.Column('grade', sa.String(length=4), nullable=True),
    sa.Column('dimension_scores', sa.JSON(), nullable=True),
    sa.Column('recommendations', sa.JSON(), nullable=True),
    sa.Column('ai_assist', sa.JSON(), nullable=True),
    sa.Column('trace_id', sa.String(length=128), nullable=True),
    sa.Column('triggered_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.String(length=512), nullable=True),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_clinic_evaluations_namespace_id'), 'clinic_evaluations', ['namespace_id'], unique=False)
    op.create_index(op.f('ix_clinic_evaluations_trace_id'), 'clinic_evaluations', ['trace_id'], unique=False)
    op.create_table('evidence_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_type', sa.Enum('API', 'BACKUP_PACKAGE', 'BROWSER_SNAPSHOT', 'AUDIT_LOG', 'USER_CONFIRM', 'LLM_ANALYSIS', name='evidencesourcetype'), nullable=False),
    sa.Column('source_provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=False),
    sa.Column('collection_job_id', sa.Integer(), nullable=True),
    sa.Column('object_uri', sa.String(length=1024), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('visibility', sa.Enum('NORMAL', 'SENSITIVE', 'RESTRICTED', name='evidencevisibility'), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['collection_job_id'], ['collection_jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_evidence_items_collection_job_id'), 'evidence_items', ['collection_job_id'], unique=False)
    op.create_index(op.f('ix_evidence_items_created_by'), 'evidence_items', ['created_by'], unique=False)
    op.create_index(op.f('ix_evidence_items_sha256'), 'evidence_items', ['sha256'], unique=False)
    op.create_index(op.f('ix_evidence_items_source_provider'), 'evidence_items', ['source_provider'], unique=False)
    op.create_index(op.f('ix_evidence_items_source_type'), 'evidence_items', ['source_type'], unique=False)
    op.create_index(op.f('ix_evidence_items_visibility'), 'evidence_items', ['visibility'], unique=False)
    op.create_table('handover_cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('case_type', sa.Enum('EMPLOYEE_OFFBOARDING', 'PROJECT_HANDOVER', 'VENDOR_EXIT', 'INCIDENT_TAKEOVER', name='handovercasetype'), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('subject_user_id', sa.Integer(), nullable=True),
    sa.Column('namespace_id', sa.Integer(), nullable=True),
    sa.Column('receiver_user_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('DRAFT', 'COLLECTING', 'ANALYZING', 'PENDING_APPROVAL', 'APPROVED', 'EXECUTING', 'VERIFYING', 'COMPLETED', 'REJECTED', 'CANCELLED', name='handoverstatus'), nullable=False),
    sa.Column('risk_level', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='criticality'), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('summary_json', sa.JSON(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['receiver_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['subject_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_handover_cases_case_type'), 'handover_cases', ['case_type'], unique=False)
    op.create_index(op.f('ix_handover_cases_created_by'), 'handover_cases', ['created_by'], unique=False)
    op.create_index(op.f('ix_handover_cases_due_at'), 'handover_cases', ['due_at'], unique=False)
    op.create_index(op.f('ix_handover_cases_namespace_id'), 'handover_cases', ['namespace_id'], unique=False)
    op.create_index(op.f('ix_handover_cases_receiver_user_id'), 'handover_cases', ['receiver_user_id'], unique=False)
    op.create_index(op.f('ix_handover_cases_risk_level'), 'handover_cases', ['risk_level'], unique=False)
    op.create_index(op.f('ix_handover_cases_status'), 'handover_cases', ['status'], unique=False)
    op.create_index(op.f('ix_handover_cases_subject_user_id'), 'handover_cases', ['subject_user_id'], unique=False)
    op.create_index(op.f('ix_handover_cases_title'), 'handover_cases', ['title'], unique=False)
    op.create_table('namespace_governance_policies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('manual_review_required', sa.Boolean(), nullable=False),
    sa.Column('require_examples', sa.Boolean(), nullable=False),
    sa.Column('require_validation_spec', sa.Boolean(), nullable=False),
    sa.Column('require_sandbox_success', sa.Boolean(), nullable=False),
    sa.Column('clinic_gate_enabled', sa.Boolean(), nullable=False),
    sa.Column('min_clinic_score', sa.Float(), nullable=True),
    sa.Column('clinic_max_age_hours', sa.Integer(), nullable=False),
    sa.Column('public_sharing_requires_approval', sa.Boolean(), nullable=False),
    sa.Column('public_share_default_expiry_days', sa.Integer(), nullable=True),
    sa.Column('require_license_attestation', sa.Boolean(), nullable=False),
    sa.Column('allowed_public_licenses', sa.JSON(), nullable=True),
    sa.Column('sandbox_network_mode', sa.String(length=32), nullable=False),
    sa.Column('sandbox_workspace_mode', sa.String(length=32), nullable=False),
    sa.Column('sandbox_agent_smoke_enabled', sa.Boolean(), nullable=False),
    sa.Column('sandbox_agent_smoke_timeout_seconds', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('namespace_id', name='uq_namespace_governance_policy_namespace')
    )
    op.create_index(op.f('ix_namespace_governance_policies_namespace_id'), 'namespace_governance_policies', ['namespace_id'], unique=False)
    op.create_table('namespace_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('ADMIN', 'DEVELOPER', 'READONLY', name='namespacerole'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('namespace_id', 'user_id')
    )
    op.create_table('namespace_quotas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('max_skills', sa.Integer(), nullable=False),
    sa.Column('max_versions_per_skill', sa.Integer(), nullable=False),
    sa.Column('max_total_versions', sa.Integer(), nullable=False),
    sa.Column('max_storage_bytes', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('namespace_id')
    )
    op.create_table('raw_collection_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('collection_job_id', sa.Integer(), nullable=True),
    sa.Column('runtime_id', sa.Integer(), nullable=True),
    sa.Column('provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=False),
    sa.Column('adapter_name', sa.String(length=128), nullable=False),
    sa.Column('stream', sa.Enum('CAPABILITY', 'PRINCIPAL', 'ASSET', 'WORKTRACE', 'ARTIFACT', 'EVIDENCE', 'BACKUP_MANIFEST', 'UNKNOWN', name='rawrecordstream'), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=True),
    sa.Column('record_hash', sa.String(length=64), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=False),
    sa.Column('normalized_type', sa.String(length=64), nullable=True),
    sa.Column('normalized_ref_id', sa.Integer(), nullable=True),
    sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['collection_job_id'], ['collection_jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('runtime_id', 'stream', 'external_id', 'record_hash', name='uq_raw_collection_records_dedupe')
    )
    op.create_index(op.f('ix_raw_collection_records_adapter_name'), 'raw_collection_records', ['adapter_name'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_collected_at'), 'raw_collection_records', ['collected_at'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_collection_job_id'), 'raw_collection_records', ['collection_job_id'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_external_id'), 'raw_collection_records', ['external_id'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_normalized_ref_id'), 'raw_collection_records', ['normalized_ref_id'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_normalized_type'), 'raw_collection_records', ['normalized_type'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_provider'), 'raw_collection_records', ['provider'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_record_hash'), 'raw_collection_records', ['record_hash'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_runtime_id'), 'raw_collection_records', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_raw_collection_records_stream'), 'raw_collection_records', ['stream'], unique=False)
    op.create_table('registry_sync_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=True),
    sa.Column('namespace_name', sa.String(length=128), nullable=False),
    sa.Column('skill_name', sa.String(length=128), nullable=False),
    sa.Column('tag', sa.String(length=64), nullable=True),
    sa.Column('entity', sa.String(length=32), nullable=False),
    sa.Column('operation', sa.String(length=32), nullable=False),
    sa.Column('sync_state', sa.String(length=32), nullable=False),
    sa.Column('event', sa.String(length=96), nullable=False),
    sa.Column('is_public', sa.Boolean(), nullable=False),
    sa.Column('version_status', sa.String(length=32), nullable=True),
    sa.Column('commit_sha', sa.String(length=40), nullable=True),
    sa.Column('reason', sa.String(length=96), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_registry_sync_events_created_at'), 'registry_sync_events', ['created_at'], unique=False)
    op.create_index(op.f('ix_registry_sync_events_is_public'), 'registry_sync_events', ['is_public'], unique=False)
    op.create_index(op.f('ix_registry_sync_events_namespace_id'), 'registry_sync_events', ['namespace_id'], unique=False)
    op.create_index(op.f('ix_registry_sync_events_namespace_name'), 'registry_sync_events', ['namespace_name'], unique=False)
    op.create_index(op.f('ix_registry_sync_events_operation'), 'registry_sync_events', ['operation'], unique=False)
    op.create_index(op.f('ix_registry_sync_events_skill_name'), 'registry_sync_events', ['skill_name'], unique=False)
    op.create_index(op.f('ix_registry_sync_events_sync_state'), 'registry_sync_events', ['sync_state'], unique=False)
    op.create_index(op.f('ix_registry_sync_events_tag'), 'registry_sync_events', ['tag'], unique=False)
    op.create_table('replication_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('src_namespace_id', sa.Integer(), nullable=False),
    sa.Column('dst_namespace_id', sa.Integer(), nullable=False),
    sa.Column('filter_pattern', sa.String(length=256), nullable=True),
    sa.Column('trigger', sa.Enum('MANUAL', 'ON_PUBLISH', name='replicationtrigger'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['dst_namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['src_namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('report_upload_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('report_id', sa.String(length=64), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=False),
    sa.Column('collection_job_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'UPLOADED', 'VERIFYING', 'INGESTING', 'SUCCEEDED', 'FAILED', 'EXPIRED', name='reportuploadstatus'), nullable=False),
    sa.Column('schema_version', sa.String(length=64), nullable=False),
    sa.Column('report_type', sa.String(length=64), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('bucket', sa.String(length=255), nullable=False),
    sa.Column('object_key', sa.String(length=512), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=128), nullable=False),
    sa.Column('expected_size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('expected_sha256', sa.String(length=64), nullable=True),
    sa.Column('actual_size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('actual_sha256', sa.String(length=64), nullable=True),
    sa.Column('manifest_json', sa.JSON(), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('upload_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_via', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['collection_job_id'], ['collection_jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('object_key', name='uq_report_upload_sessions_object_key'),
    sa.UniqueConstraint('report_id', name='uq_report_upload_sessions_report_id')
    )
    op.create_index(op.f('ix_report_upload_sessions_actual_sha256'), 'report_upload_sessions', ['actual_sha256'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_collection_job_id'), 'report_upload_sessions', ['collection_job_id'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_created_by'), 'report_upload_sessions', ['created_by'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_created_via'), 'report_upload_sessions', ['created_via'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_expected_sha256'), 'report_upload_sessions', ['expected_sha256'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_finalized_at'), 'report_upload_sessions', ['finalized_at'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_idempotency_key'), 'report_upload_sessions', ['idempotency_key'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_period_end'), 'report_upload_sessions', ['period_end'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_period_start'), 'report_upload_sessions', ['period_start'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_report_id'), 'report_upload_sessions', ['report_id'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_report_type'), 'report_upload_sessions', ['report_type'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_runtime_id'), 'report_upload_sessions', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_schema_version'), 'report_upload_sessions', ['schema_version'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_status'), 'report_upload_sessions', ['status'], unique=False)
    op.create_index(op.f('ix_report_upload_sessions_upload_expires_at'), 'report_upload_sessions', ['upload_expires_at'], unique=False)
    op.create_table('retention_policies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('keep_last_n', sa.Integer(), nullable=True),
    sa.Column('keep_days', sa.Integer(), nullable=True),
    sa.Column('delete_rejected', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('namespace_id')
    )
    op.create_table('robot_accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.String(length=512), nullable=True),
    sa.Column('token_hash', sa.String(length=256), nullable=False),
    sa.Column('token_prefix', sa.String(length=16), nullable=False),
    sa.Column('role', sa.Enum('ADMIN', 'DEVELOPER', 'READONLY', name='namespacerole'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_robot_accounts_namespace_id'), 'robot_accounts', ['namespace_id'], unique=False)
    op.create_table('role_bindings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=True),
    sa.Column('org_unit_id', sa.Integer(), nullable=True),
    sa.Column('granted_by', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('permission_cache', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_unit_id'], ['org_units.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_role_bindings_expires_at'), 'role_bindings', ['expires_at'], unique=False)
    op.create_index(op.f('ix_role_bindings_namespace_id'), 'role_bindings', ['namespace_id'], unique=False)
    op.create_index(op.f('ix_role_bindings_org_unit_id'), 'role_bindings', ['org_unit_id'], unique=False)
    op.create_index(op.f('ix_role_bindings_role_id'), 'role_bindings', ['role_id'], unique=False)
    op.create_index(op.f('ix_role_bindings_user_id'), 'role_bindings', ['user_id'], unique=False)
    op.create_table('runtime_bindings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=False),
    sa.Column('external_ref', sa.String(length=255), nullable=True),
    sa.Column('environment', sa.String(length=32), nullable=False),
    sa.Column('usage_status', sa.String(length=32), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['ai_assets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_runtime_bindings_asset_id'), 'runtime_bindings', ['asset_id'], unique=False)
    op.create_index(op.f('ix_runtime_bindings_environment'), 'runtime_bindings', ['environment'], unique=False)
    op.create_index(op.f('ix_runtime_bindings_last_used_at'), 'runtime_bindings', ['last_used_at'], unique=False)
    op.create_index(op.f('ix_runtime_bindings_runtime_id'), 'runtime_bindings', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_runtime_bindings_usage_status'), 'runtime_bindings', ['usage_status'], unique=False)
    op.create_table('scanner_rule_suppressions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('file_pattern', sa.String(length=255), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('namespace_id', 'rule_id', 'file_pattern', name='uq_scanner_suppression')
    )
    op.create_index(op.f('ix_scanner_rule_suppressions_namespace_id'), 'scanner_rule_suppressions', ['namespace_id'], unique=False)
    op.create_table('skills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('git_repo_path', sa.String(length=512), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_skills_deleted_at'), 'skills', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_skills_name'), 'skills', ['name'], unique=False)
    op.create_table('webhooks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('namespace_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('url', sa.String(length=512), nullable=False),
    sa.Column('secret', sa.String(length=256), nullable=True),
    sa.Column('events', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_webhooks_namespace_id'), 'webhooks', ['namespace_id'], unique=False)
    op.create_table('work_traces',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=True),
    sa.Column('asset_id', sa.Integer(), nullable=True),
    sa.Column('external_session_id', sa.String(length=255), nullable=True),
    sa.Column('actor_user_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('trace_type', sa.Enum('SESSION', 'TASK_RUN', 'AUTOMATION_RUN', 'FILE_CHANGE', 'APPROVAL', 'DEPLOYMENT', name='tracetype'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sensitivity', sa.Enum('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', name='sensitivity'), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['asset_id'], ['ai_assets.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_work_traces_actor_user_id'), 'work_traces', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_work_traces_asset_id'), 'work_traces', ['asset_id'], unique=False)
    op.create_index(op.f('ix_work_traces_external_session_id'), 'work_traces', ['external_session_id'], unique=False)
    op.create_index(op.f('ix_work_traces_runtime_id'), 'work_traces', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_work_traces_sensitivity'), 'work_traces', ['sensitivity'], unique=False)
    op.create_index(op.f('ix_work_traces_started_at'), 'work_traces', ['started_at'], unique=False)
    op.create_index(op.f('ix_work_traces_title'), 'work_traces', ['title'], unique=False)
    op.create_index(op.f('ix_work_traces_trace_type'), 'work_traces', ['trace_type'], unique=False)
    op.create_table('approval_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('handover_case_id', sa.Integer(), nullable=False),
    sa.Column('approver_user_id', sa.Integer(), nullable=False),
    sa.Column('approval_type', sa.Enum('MANAGER', 'RECEIVER', 'SECURITY', 'PLATFORM_ADMIN', name='approvaltype'), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'DELEGATED', name='approvalstatus'), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['approver_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['handover_case_id'], ['handover_cases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approval_tasks_approval_type'), 'approval_tasks', ['approval_type'], unique=False)
    op.create_index(op.f('ix_approval_tasks_approver_user_id'), 'approval_tasks', ['approver_user_id'], unique=False)
    op.create_index(op.f('ix_approval_tasks_handover_case_id'), 'approval_tasks', ['handover_case_id'], unique=False)
    op.create_index(op.f('ix_approval_tasks_status'), 'approval_tasks', ['status'], unique=False)
    op.create_table('asset_ownerships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('owner_type', sa.Enum('CREATOR', 'MAINTAINER', 'BUSINESS_OWNER', 'STEWARD', 'RECEIVER', name='ownertype'), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('org_unit_id', sa.Integer(), nullable=True),
    sa.Column('namespace_id', sa.Integer(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('evidence_id', sa.Integer(), nullable=True),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['ai_assets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['evidence_id'], ['evidence_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['namespace_id'], ['namespaces.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_unit_id'], ['org_units.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_asset_ownerships_asset_id'), 'asset_ownerships', ['asset_id'], unique=False)
    op.create_index(op.f('ix_asset_ownerships_evidence_id'), 'asset_ownerships', ['evidence_id'], unique=False)
    op.create_index(op.f('ix_asset_ownerships_namespace_id'), 'asset_ownerships', ['namespace_id'], unique=False)
    op.create_index(op.f('ix_asset_ownerships_org_unit_id'), 'asset_ownerships', ['org_unit_id'], unique=False)
    op.create_index(op.f('ix_asset_ownerships_owner_type'), 'asset_ownerships', ['owner_type'], unique=False)
    op.create_index(op.f('ix_asset_ownerships_user_id'), 'asset_ownerships', ['user_id'], unique=False)
    op.create_table('handover_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('handover_case_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('recommended_action', sa.Enum('TRANSFER_OWNER', 'ARCHIVE', 'DISABLE', 'ROTATE_SECRET', 'EXPORT_PACKAGE', 'MANUAL_REVIEW', 'IGNORE', name='handoveraction'), nullable=False),
    sa.Column('receiver_user_id', sa.Integer(), nullable=True),
    sa.Column('risk_reason', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), server_default=sa.text("'0'"), nullable=False),
    sa.Column('evidence_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('PROPOSED', 'APPROVED', 'EXECUTING', 'DONE', 'FAILED', 'SKIPPED', name='handoveritemstatus'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['ai_assets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['evidence_id'], ['evidence_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['handover_case_id'], ['handover_cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['receiver_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_handover_items_asset_id'), 'handover_items', ['asset_id'], unique=False)
    op.create_index(op.f('ix_handover_items_evidence_id'), 'handover_items', ['evidence_id'], unique=False)
    op.create_index(op.f('ix_handover_items_handover_case_id'), 'handover_items', ['handover_case_id'], unique=False)
    op.create_index(op.f('ix_handover_items_receiver_user_id'), 'handover_items', ['receiver_user_id'], unique=False)
    op.create_index(op.f('ix_handover_items_recommended_action'), 'handover_items', ['recommended_action'], unique=False)
    op.create_index(op.f('ix_handover_items_status'), 'handover_items', ['status'], unique=False)
    op.create_table('replication_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('rule_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', name='replicationjobstatus'), nullable=False),
    sa.Column('skills_copied', sa.Integer(), nullable=False),
    sa.Column('skills_skipped', sa.Integer(), nullable=False),
    sa.Column('skills_failed', sa.Integer(), nullable=False),
    sa.Column('log', sa.JSON(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.String(length=512), nullable=True),
    sa.Column('triggered_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['rule_id'], ['replication_rules.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_replication_jobs_rule_id'), 'replication_jobs', ['rule_id'], unique=False)
    op.create_table('report_analysis_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('report_upload_session_id', sa.Integer(), nullable=False),
    sa.Column('runtime_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'LEASED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', name='analysisjobstatus'), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('worker_id', sa.Integer(), nullable=True),
    sa.Column('lease_owner', sa.String(length=128), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('input_bucket', sa.String(length=255), nullable=False),
    sa.Column('input_object_key', sa.String(length=512), nullable=False),
    sa.Column('input_sha256', sa.String(length=64), nullable=True),
    sa.Column('input_size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('result_bucket', sa.String(length=255), nullable=True),
    sa.Column('result_object_key', sa.String(length=512), nullable=True),
    sa.Column('result_sha256', sa.String(length=64), nullable=True),
    sa.Column('result_size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('result_content_type', sa.String(length=128), nullable=True),
    sa.Column('summary_json', sa.JSON(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['report_upload_session_id'], ['report_upload_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['worker_id'], ['analysis_workers.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('report_upload_session_id', name='uq_report_analysis_jobs_session')
    )
    op.create_index(op.f('ix_report_analysis_jobs_created_at'), 'report_analysis_jobs', ['created_at'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_finished_at'), 'report_analysis_jobs', ['finished_at'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_input_sha256'), 'report_analysis_jobs', ['input_sha256'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_lease_expires_at'), 'report_analysis_jobs', ['lease_expires_at'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_lease_owner'), 'report_analysis_jobs', ['lease_owner'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_priority'), 'report_analysis_jobs', ['priority'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_report_upload_session_id'), 'report_analysis_jobs', ['report_upload_session_id'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_result_object_key'), 'report_analysis_jobs', ['result_object_key'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_result_sha256'), 'report_analysis_jobs', ['result_sha256'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_runtime_id'), 'report_analysis_jobs', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_started_at'), 'report_analysis_jobs', ['started_at'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_status'), 'report_analysis_jobs', ['status'], unique=False)
    op.create_index(op.f('ix_report_analysis_jobs_worker_id'), 'report_analysis_jobs', ['worker_id'], unique=False)
    op.create_table('skill_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('skill_id', sa.Integer(), nullable=False),
    sa.Column('tag', sa.String(length=64), nullable=False),
    sa.Column('commit_sha', sa.String(length=40), nullable=False),
    sa.Column('status', sa.Enum('QUARANTINE', 'SCANNING', 'REVIEW', 'PRODUCTION', 'REJECTED', name='skillversionstatus'), nullable=False),
    sa.Column('review_status', sa.Enum('NOT_REQUIRED', 'PENDING', 'APPROVED', 'REJECTED', name='skillversionreviewstatus'), nullable=False),
    sa.Column('review_required', sa.Boolean(), nullable=False),
    sa.Column('review_notes', sa.Text(), nullable=True),
    sa.Column('review_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('gate_result', sa.JSON(), nullable=True),
    sa.Column('skill_metadata', sa.JSON(), nullable=True),
    sa.Column('changelog', sa.Text(), nullable=True),
    sa.Column('publish_tags', sa.JSON(), nullable=True),
    sa.Column('content_fingerprint', sa.String(length=64), nullable=True),
    sa.Column('file_count', sa.Integer(), nullable=False),
    sa.Column('published_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_skill_versions_content_fingerprint'), 'skill_versions', ['content_fingerprint'], unique=False)
    op.create_index(op.f('ix_skill_versions_review_status'), 'skill_versions', ['review_status'], unique=False)
    op.create_index(op.f('ix_skill_versions_status'), 'skill_versions', ['status'], unique=False)
    op.create_table('webhook_deliveries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('webhook_id', sa.Integer(), nullable=False),
    sa.Column('event', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_body', sa.Text(), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['webhook_id'], ['webhooks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_webhook_deliveries_webhook_id'), 'webhook_deliveries', ['webhook_id'], unique=False)
    op.create_table('work_artifacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trace_id', sa.Integer(), nullable=True),
    sa.Column('asset_id', sa.Integer(), nullable=True),
    sa.Column('artifact_type', sa.Enum('FILE', 'TRANSCRIPT', 'DIFF', 'REPORT', 'PACKAGE', 'SCREENSHOT', 'LOG', name='artifacttype'), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('object_uri', sa.String(length=1024), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sensitivity', sa.Enum('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', name='sensitivity'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['ai_assets.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['trace_id'], ['work_traces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_work_artifacts_artifact_type'), 'work_artifacts', ['artifact_type'], unique=False)
    op.create_index(op.f('ix_work_artifacts_asset_id'), 'work_artifacts', ['asset_id'], unique=False)
    op.create_index(op.f('ix_work_artifacts_sensitivity'), 'work_artifacts', ['sensitivity'], unique=False)
    op.create_index(op.f('ix_work_artifacts_sha256'), 'work_artifacts', ['sha256'], unique=False)
    op.create_index(op.f('ix_work_artifacts_trace_id'), 'work_artifacts', ['trace_id'], unique=False)
    op.create_table('analysis_result_artifacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('analysis_job_id', sa.Integer(), nullable=False),
    sa.Column('report_upload_session_id', sa.Integer(), nullable=True),
    sa.Column('runtime_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.Enum('ANALYSIS_RESULT', 'ASSET_CARDS', 'WORKTRACE_SUMMARY', 'MEMORY_CANDIDATES', 'HANDOVER_SIGNALS', 'REDACTION_REPORT', 'OTHER', name='analysisresultartifactkind'), nullable=False),
    sa.Column('bucket', sa.String(length=255), nullable=False),
    sa.Column('object_key', sa.String(length=512), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=128), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('summary_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['analysis_job_id'], ['report_analysis_jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['report_upload_session_id'], ['report_upload_sessions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('analysis_job_id', 'object_key', name='uq_analysis_result_artifacts_job_object')
    )
    op.create_index(op.f('ix_analysis_result_artifacts_analysis_job_id'), 'analysis_result_artifacts', ['analysis_job_id'], unique=False)
    op.create_index(op.f('ix_analysis_result_artifacts_created_at'), 'analysis_result_artifacts', ['created_at'], unique=False)
    op.create_index(op.f('ix_analysis_result_artifacts_kind'), 'analysis_result_artifacts', ['kind'], unique=False)
    op.create_index(op.f('ix_analysis_result_artifacts_object_key'), 'analysis_result_artifacts', ['object_key'], unique=False)
    op.create_index(op.f('ix_analysis_result_artifacts_report_upload_session_id'), 'analysis_result_artifacts', ['report_upload_session_id'], unique=False)
    op.create_index(op.f('ix_analysis_result_artifacts_runtime_id'), 'analysis_result_artifacts', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_analysis_result_artifacts_sha256'), 'analysis_result_artifacts', ['sha256'], unique=False)
    op.create_table('execution_actions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('handover_case_id', sa.Integer(), nullable=False),
    sa.Column('handover_item_id', sa.Integer(), nullable=True),
    sa.Column('action_type', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.Enum('OPENCLAW', 'ARKCLAW', 'WORKBUDDY', 'JVS', 'CUSTOM', name='runtimeprovider'), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'REQUIRES_MANUAL', name='executionstatus'), nullable=False),
    sa.Column('execution_mode', sa.Enum('MANUAL', 'AUTO', name='executionmode'), server_default='MANUAL', nullable=False),
    sa.Column('request_json', sa.JSON(), nullable=True),
    sa.Column('result_json', sa.JSON(), nullable=True),
    sa.Column('evidence_ids', sa.JSON(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['handover_case_id'], ['handover_cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['handover_item_id'], ['handover_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('handover_case_id', 'idempotency_key', name='uq_execution_actions_case_idempotency_key')
    )
    op.create_index(op.f('ix_execution_actions_action_type'), 'execution_actions', ['action_type'], unique=False)
    op.create_index(op.f('ix_execution_actions_execution_mode'), 'execution_actions', ['execution_mode'], unique=False)
    op.create_index(op.f('ix_execution_actions_handover_case_id'), 'execution_actions', ['handover_case_id'], unique=False)
    op.create_index(op.f('ix_execution_actions_handover_item_id'), 'execution_actions', ['handover_item_id'], unique=False)
    op.create_index(op.f('ix_execution_actions_idempotency_key'), 'execution_actions', ['idempotency_key'], unique=False)
    op.create_index(op.f('ix_execution_actions_provider'), 'execution_actions', ['provider'], unique=False)
    op.create_index(op.f('ix_execution_actions_status'), 'execution_actions', ['status'], unique=False)
    op.create_table('memory_candidates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('analysis_job_id', sa.Integer(), nullable=True),
    sa.Column('report_upload_session_id', sa.Integer(), nullable=True),
    sa.Column('runtime_id', sa.Integer(), nullable=True),
    sa.Column('candidate_type', sa.Enum('ASSET_SUMMARY', 'WORKTRACE_SUMMARY', 'PROJECT_CONTEXT', 'OWNERSHIP_SIGNAL', 'HANDOVER_SIGNAL', 'RISK_SIGNAL', 'KNOWLEDGE_NOTE', name='memorycandidatetype'), nullable=False),
    sa.Column('status', sa.Enum('CANDIDATE', 'CONFIRMED', 'REJECTED', 'SUPERSEDED', name='memorycandidatestatus'), nullable=False),
    sa.Column('subject_type', sa.String(length=64), nullable=False),
    sa.Column('subject_key', sa.String(length=255), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('sensitivity', sa.Enum('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', name='sensitivity'), nullable=False),
    sa.Column('source_object_uri', sa.String(length=1024), nullable=True),
    sa.Column('source_sha256', sa.String(length=64), nullable=True),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['analysis_job_id'], ['report_analysis_jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['report_upload_session_id'], ['report_upload_sessions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['runtime_id'], ['runtime_instances.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_memory_candidates_analysis_job_id'), 'memory_candidates', ['analysis_job_id'], unique=False)
    op.create_index(op.f('ix_memory_candidates_candidate_type'), 'memory_candidates', ['candidate_type'], unique=False)
    op.create_index(op.f('ix_memory_candidates_created_at'), 'memory_candidates', ['created_at'], unique=False)
    op.create_index(op.f('ix_memory_candidates_report_upload_session_id'), 'memory_candidates', ['report_upload_session_id'], unique=False)
    op.create_index(op.f('ix_memory_candidates_reviewed_at'), 'memory_candidates', ['reviewed_at'], unique=False)
    op.create_index(op.f('ix_memory_candidates_reviewed_by'), 'memory_candidates', ['reviewed_by'], unique=False)
    op.create_index(op.f('ix_memory_candidates_runtime_id'), 'memory_candidates', ['runtime_id'], unique=False)
    op.create_index(op.f('ix_memory_candidates_sensitivity'), 'memory_candidates', ['sensitivity'], unique=False)
    op.create_index(op.f('ix_memory_candidates_source_sha256'), 'memory_candidates', ['source_sha256'], unique=False)
    op.create_index(op.f('ix_memory_candidates_status'), 'memory_candidates', ['status'], unique=False)
    op.create_index(op.f('ix_memory_candidates_subject_key'), 'memory_candidates', ['subject_key'], unique=False)
    op.create_index(op.f('ix_memory_candidates_subject_type'), 'memory_candidates', ['subject_type'], unique=False)
    op.create_index(op.f('ix_memory_candidates_title'), 'memory_candidates', ['title'], unique=False)
    op.create_table('public_skill_releases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('version_id', sa.Integer(), nullable=False),
    sa.Column('shared_by', sa.Integer(), nullable=True),
    sa.Column('approval_status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', name='publicreleaseapprovalstatus'), nullable=False),
    sa.Column('approval_notes', sa.Text(), nullable=True),
    sa.Column('approved_by', sa.Integer(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('license_name', sa.String(length=128), nullable=True),
    sa.Column('license_attested', sa.Boolean(), nullable=False),
    sa.Column('license_attested_by', sa.Integer(), nullable=True),
    sa.Column('license_attested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('risk_acknowledged', sa.Boolean(), nullable=False),
    sa.Column('risk_acknowledged_by', sa.Integer(), nullable=True),
    sa.Column('risk_acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['license_attested_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['risk_acknowledged_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['shared_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['version_id'], ['skill_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id')
    )
    op.create_index(op.f('ix_public_skill_releases_approval_status'), 'public_skill_releases', ['approval_status'], unique=False)
    op.create_index(op.f('ix_public_skill_releases_expires_at'), 'public_skill_releases', ['expires_at'], unique=False)
    op.create_index(op.f('ix_public_skill_releases_version_id'), 'public_skill_releases', ['version_id'], unique=False)
    op.create_table('sandbox_validation_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('version_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'PASSED', 'FAILED', 'SKIPPED', name='sandboxvalidationstatus'), nullable=False),
    sa.Column('engine', sa.String(length=64), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('checks', sa.JSON(), nullable=True),
    sa.Column('logs', sa.JSON(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['version_id'], ['skill_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sandbox_validation_runs_status'), 'sandbox_validation_runs', ['status'], unique=False)
    op.create_index(op.f('ix_sandbox_validation_runs_version_id'), 'sandbox_validation_runs', ['version_id'], unique=True)
    op.create_table('scan_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('version_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'PASSED', 'WARNED', 'FAILED', name='scanstatus'), nullable=False),
    sa.Column('issues', sa.JSON(), nullable=True),
    sa.Column('critical_count', sa.Integer(), nullable=False),
    sa.Column('high_count', sa.Integer(), nullable=False),
    sa.Column('medium_count', sa.Integer(), nullable=False),
    sa.Column('low_count', sa.Integer(), nullable=False),
    sa.Column('scanner_version', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['version_id'], ['skill_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scan_results_version_id'), 'scan_results', ['version_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_scan_results_version_id'), table_name='scan_results')
    op.drop_table('scan_results')
    op.drop_index(op.f('ix_sandbox_validation_runs_version_id'), table_name='sandbox_validation_runs')
    op.drop_index(op.f('ix_sandbox_validation_runs_status'), table_name='sandbox_validation_runs')
    op.drop_table('sandbox_validation_runs')
    op.drop_index(op.f('ix_public_skill_releases_version_id'), table_name='public_skill_releases')
    op.drop_index(op.f('ix_public_skill_releases_expires_at'), table_name='public_skill_releases')
    op.drop_index(op.f('ix_public_skill_releases_approval_status'), table_name='public_skill_releases')
    op.drop_table('public_skill_releases')
    op.drop_index(op.f('ix_memory_candidates_title'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_subject_type'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_subject_key'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_status'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_source_sha256'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_sensitivity'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_runtime_id'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_reviewed_by'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_reviewed_at'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_report_upload_session_id'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_created_at'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_candidate_type'), table_name='memory_candidates')
    op.drop_index(op.f('ix_memory_candidates_analysis_job_id'), table_name='memory_candidates')
    op.drop_table('memory_candidates')
    op.drop_index(op.f('ix_execution_actions_status'), table_name='execution_actions')
    op.drop_index(op.f('ix_execution_actions_provider'), table_name='execution_actions')
    op.drop_index(op.f('ix_execution_actions_idempotency_key'), table_name='execution_actions')
    op.drop_index(op.f('ix_execution_actions_handover_item_id'), table_name='execution_actions')
    op.drop_index(op.f('ix_execution_actions_handover_case_id'), table_name='execution_actions')
    op.drop_index(op.f('ix_execution_actions_execution_mode'), table_name='execution_actions')
    op.drop_index(op.f('ix_execution_actions_action_type'), table_name='execution_actions')
    op.drop_table('execution_actions')
    op.drop_index(op.f('ix_analysis_result_artifacts_sha256'), table_name='analysis_result_artifacts')
    op.drop_index(op.f('ix_analysis_result_artifacts_runtime_id'), table_name='analysis_result_artifacts')
    op.drop_index(op.f('ix_analysis_result_artifacts_report_upload_session_id'), table_name='analysis_result_artifacts')
    op.drop_index(op.f('ix_analysis_result_artifacts_object_key'), table_name='analysis_result_artifacts')
    op.drop_index(op.f('ix_analysis_result_artifacts_kind'), table_name='analysis_result_artifacts')
    op.drop_index(op.f('ix_analysis_result_artifacts_created_at'), table_name='analysis_result_artifacts')
    op.drop_index(op.f('ix_analysis_result_artifacts_analysis_job_id'), table_name='analysis_result_artifacts')
    op.drop_table('analysis_result_artifacts')
    op.drop_index(op.f('ix_work_artifacts_trace_id'), table_name='work_artifacts')
    op.drop_index(op.f('ix_work_artifacts_sha256'), table_name='work_artifacts')
    op.drop_index(op.f('ix_work_artifacts_sensitivity'), table_name='work_artifacts')
    op.drop_index(op.f('ix_work_artifacts_asset_id'), table_name='work_artifacts')
    op.drop_index(op.f('ix_work_artifacts_artifact_type'), table_name='work_artifacts')
    op.drop_table('work_artifacts')
    op.drop_index(op.f('ix_webhook_deliveries_webhook_id'), table_name='webhook_deliveries')
    op.drop_table('webhook_deliveries')
    op.drop_index(op.f('ix_skill_versions_status'), table_name='skill_versions')
    op.drop_index(op.f('ix_skill_versions_review_status'), table_name='skill_versions')
    op.drop_index(op.f('ix_skill_versions_content_fingerprint'), table_name='skill_versions')
    op.drop_table('skill_versions')
    op.drop_index(op.f('ix_report_analysis_jobs_worker_id'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_status'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_started_at'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_runtime_id'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_result_sha256'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_result_object_key'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_report_upload_session_id'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_priority'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_lease_owner'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_lease_expires_at'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_input_sha256'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_finished_at'), table_name='report_analysis_jobs')
    op.drop_index(op.f('ix_report_analysis_jobs_created_at'), table_name='report_analysis_jobs')
    op.drop_table('report_analysis_jobs')
    op.drop_index(op.f('ix_replication_jobs_rule_id'), table_name='replication_jobs')
    op.drop_table('replication_jobs')
    op.drop_index(op.f('ix_handover_items_status'), table_name='handover_items')
    op.drop_index(op.f('ix_handover_items_recommended_action'), table_name='handover_items')
    op.drop_index(op.f('ix_handover_items_receiver_user_id'), table_name='handover_items')
    op.drop_index(op.f('ix_handover_items_handover_case_id'), table_name='handover_items')
    op.drop_index(op.f('ix_handover_items_evidence_id'), table_name='handover_items')
    op.drop_index(op.f('ix_handover_items_asset_id'), table_name='handover_items')
    op.drop_table('handover_items')
    op.drop_index(op.f('ix_asset_ownerships_user_id'), table_name='asset_ownerships')
    op.drop_index(op.f('ix_asset_ownerships_owner_type'), table_name='asset_ownerships')
    op.drop_index(op.f('ix_asset_ownerships_org_unit_id'), table_name='asset_ownerships')
    op.drop_index(op.f('ix_asset_ownerships_namespace_id'), table_name='asset_ownerships')
    op.drop_index(op.f('ix_asset_ownerships_evidence_id'), table_name='asset_ownerships')
    op.drop_index(op.f('ix_asset_ownerships_asset_id'), table_name='asset_ownerships')
    op.drop_table('asset_ownerships')
    op.drop_index(op.f('ix_approval_tasks_status'), table_name='approval_tasks')
    op.drop_index(op.f('ix_approval_tasks_handover_case_id'), table_name='approval_tasks')
    op.drop_index(op.f('ix_approval_tasks_approver_user_id'), table_name='approval_tasks')
    op.drop_index(op.f('ix_approval_tasks_approval_type'), table_name='approval_tasks')
    op.drop_table('approval_tasks')
    op.drop_index(op.f('ix_work_traces_trace_type'), table_name='work_traces')
    op.drop_index(op.f('ix_work_traces_title'), table_name='work_traces')
    op.drop_index(op.f('ix_work_traces_started_at'), table_name='work_traces')
    op.drop_index(op.f('ix_work_traces_sensitivity'), table_name='work_traces')
    op.drop_index(op.f('ix_work_traces_runtime_id'), table_name='work_traces')
    op.drop_index(op.f('ix_work_traces_external_session_id'), table_name='work_traces')
    op.drop_index(op.f('ix_work_traces_asset_id'), table_name='work_traces')
    op.drop_index(op.f('ix_work_traces_actor_user_id'), table_name='work_traces')
    op.drop_table('work_traces')
    op.drop_index(op.f('ix_webhooks_namespace_id'), table_name='webhooks')
    op.drop_table('webhooks')
    op.drop_index(op.f('ix_skills_name'), table_name='skills')
    op.drop_index(op.f('ix_skills_deleted_at'), table_name='skills')
    op.drop_table('skills')
    op.drop_index(op.f('ix_scanner_rule_suppressions_namespace_id'), table_name='scanner_rule_suppressions')
    op.drop_table('scanner_rule_suppressions')
    op.drop_index(op.f('ix_runtime_bindings_usage_status'), table_name='runtime_bindings')
    op.drop_index(op.f('ix_runtime_bindings_runtime_id'), table_name='runtime_bindings')
    op.drop_index(op.f('ix_runtime_bindings_last_used_at'), table_name='runtime_bindings')
    op.drop_index(op.f('ix_runtime_bindings_environment'), table_name='runtime_bindings')
    op.drop_index(op.f('ix_runtime_bindings_asset_id'), table_name='runtime_bindings')
    op.drop_table('runtime_bindings')
    op.drop_index(op.f('ix_role_bindings_user_id'), table_name='role_bindings')
    op.drop_index(op.f('ix_role_bindings_role_id'), table_name='role_bindings')
    op.drop_index(op.f('ix_role_bindings_org_unit_id'), table_name='role_bindings')
    op.drop_index(op.f('ix_role_bindings_namespace_id'), table_name='role_bindings')
    op.drop_index(op.f('ix_role_bindings_expires_at'), table_name='role_bindings')
    op.drop_table('role_bindings')
    op.drop_index(op.f('ix_robot_accounts_namespace_id'), table_name='robot_accounts')
    op.drop_table('robot_accounts')
    op.drop_table('retention_policies')
    op.drop_index(op.f('ix_report_upload_sessions_upload_expires_at'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_status'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_schema_version'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_runtime_id'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_report_type'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_report_id'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_period_start'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_period_end'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_idempotency_key'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_finalized_at'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_expected_sha256'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_created_via'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_created_by'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_collection_job_id'), table_name='report_upload_sessions')
    op.drop_index(op.f('ix_report_upload_sessions_actual_sha256'), table_name='report_upload_sessions')
    op.drop_table('report_upload_sessions')
    op.drop_table('replication_rules')
    op.drop_index(op.f('ix_registry_sync_events_tag'), table_name='registry_sync_events')
    op.drop_index(op.f('ix_registry_sync_events_sync_state'), table_name='registry_sync_events')
    op.drop_index(op.f('ix_registry_sync_events_skill_name'), table_name='registry_sync_events')
    op.drop_index(op.f('ix_registry_sync_events_operation'), table_name='registry_sync_events')
    op.drop_index(op.f('ix_registry_sync_events_namespace_name'), table_name='registry_sync_events')
    op.drop_index(op.f('ix_registry_sync_events_namespace_id'), table_name='registry_sync_events')
    op.drop_index(op.f('ix_registry_sync_events_is_public'), table_name='registry_sync_events')
    op.drop_index(op.f('ix_registry_sync_events_created_at'), table_name='registry_sync_events')
    op.drop_table('registry_sync_events')
    op.drop_index(op.f('ix_raw_collection_records_stream'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_runtime_id'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_record_hash'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_provider'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_normalized_type'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_normalized_ref_id'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_external_id'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_collection_job_id'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_collected_at'), table_name='raw_collection_records')
    op.drop_index(op.f('ix_raw_collection_records_adapter_name'), table_name='raw_collection_records')
    op.drop_table('raw_collection_records')
    op.drop_table('namespace_quotas')
    op.drop_table('namespace_members')
    op.drop_index(op.f('ix_namespace_governance_policies_namespace_id'), table_name='namespace_governance_policies')
    op.drop_table('namespace_governance_policies')
    op.drop_index(op.f('ix_handover_cases_title'), table_name='handover_cases')
    op.drop_index(op.f('ix_handover_cases_subject_user_id'), table_name='handover_cases')
    op.drop_index(op.f('ix_handover_cases_status'), table_name='handover_cases')
    op.drop_index(op.f('ix_handover_cases_risk_level'), table_name='handover_cases')
    op.drop_index(op.f('ix_handover_cases_receiver_user_id'), table_name='handover_cases')
    op.drop_index(op.f('ix_handover_cases_namespace_id'), table_name='handover_cases')
    op.drop_index(op.f('ix_handover_cases_due_at'), table_name='handover_cases')
    op.drop_index(op.f('ix_handover_cases_created_by'), table_name='handover_cases')
    op.drop_index(op.f('ix_handover_cases_case_type'), table_name='handover_cases')
    op.drop_table('handover_cases')
    op.drop_index(op.f('ix_evidence_items_visibility'), table_name='evidence_items')
    op.drop_index(op.f('ix_evidence_items_source_type'), table_name='evidence_items')
    op.drop_index(op.f('ix_evidence_items_source_provider'), table_name='evidence_items')
    op.drop_index(op.f('ix_evidence_items_sha256'), table_name='evidence_items')
    op.drop_index(op.f('ix_evidence_items_created_by'), table_name='evidence_items')
    op.drop_index(op.f('ix_evidence_items_collection_job_id'), table_name='evidence_items')
    op.drop_table('evidence_items')
    op.drop_index(op.f('ix_clinic_evaluations_trace_id'), table_name='clinic_evaluations')
    op.drop_index(op.f('ix_clinic_evaluations_namespace_id'), table_name='clinic_evaluations')
    op.drop_table('clinic_evaluations')
    op.drop_index(op.f('ix_audit_logs_namespace_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_adapter_run_steps_step_name'), table_name='adapter_run_steps')
    op.drop_index(op.f('ix_adapter_run_steps_status'), table_name='adapter_run_steps')
    op.drop_index(op.f('ix_adapter_run_steps_started_at'), table_name='adapter_run_steps')
    op.drop_index(op.f('ix_adapter_run_steps_finished_at'), table_name='adapter_run_steps')
    op.drop_index(op.f('ix_adapter_run_steps_collection_job_id'), table_name='adapter_run_steps')
    op.drop_table('adapter_run_steps')
    op.drop_index(op.f('ix_adapter_errors_step_name'), table_name='adapter_errors')
    op.drop_index(op.f('ix_adapter_errors_runtime_id'), table_name='adapter_errors')
    op.drop_index(op.f('ix_adapter_errors_provider'), table_name='adapter_errors')
    op.drop_index(op.f('ix_adapter_errors_error_code'), table_name='adapter_errors')
    op.drop_index(op.f('ix_adapter_errors_created_at'), table_name='adapter_errors')
    op.drop_index(op.f('ix_adapter_errors_collection_job_id'), table_name='adapter_errors')
    op.drop_index(op.f('ix_adapter_errors_adapter_name'), table_name='adapter_errors')
    op.drop_table('adapter_errors')
    op.drop_index(op.f('ix_user_handover_profiles_user_id'), table_name='user_handover_profiles')
    op.drop_index(op.f('ix_user_handover_profiles_manager_user_id'), table_name='user_handover_profiles')
    op.drop_index(op.f('ix_user_handover_profiles_handover_receiver_user_id'), table_name='user_handover_profiles')
    op.drop_index(op.f('ix_user_handover_profiles_employment_status'), table_name='user_handover_profiles')
    op.drop_index(op.f('ix_user_handover_profiles_employee_no'), table_name='user_handover_profiles')
    op.drop_table('user_handover_profiles')
    op.drop_index(op.f('ix_user_affiliations_user_id'), table_name='user_affiliations')
    op.drop_index(op.f('ix_user_affiliations_org_unit_id'), table_name='user_affiliations')
    op.drop_index(op.f('ix_user_affiliations_manager_user_id'), table_name='user_affiliations')
    op.drop_index(op.f('ix_user_affiliations_employment_status'), table_name='user_affiliations')
    op.drop_index(op.f('ix_user_affiliations_employee_no'), table_name='user_affiliations')
    op.drop_index(op.f('ix_user_affiliations_cost_center_override'), table_name='user_affiliations')
    op.drop_table('user_affiliations')
    op.drop_index(op.f('ix_runtime_report_tokens_token_prefix'), table_name='runtime_report_tokens')
    op.drop_index(op.f('ix_runtime_report_tokens_runtime_id'), table_name='runtime_report_tokens')
    op.drop_index(op.f('ix_runtime_report_tokens_last_used_at'), table_name='runtime_report_tokens')
    op.drop_index(op.f('ix_runtime_report_tokens_is_active'), table_name='runtime_report_tokens')
    op.drop_index(op.f('ix_runtime_report_tokens_expires_at'), table_name='runtime_report_tokens')
    op.drop_index(op.f('ix_runtime_report_tokens_created_by'), table_name='runtime_report_tokens')
    op.drop_table('runtime_report_tokens')
    op.drop_index(op.f('ix_runtime_capability_snapshots_status'), table_name='runtime_capability_snapshots')
    op.drop_index(op.f('ix_runtime_capability_snapshots_source'), table_name='runtime_capability_snapshots')
    op.drop_index(op.f('ix_runtime_capability_snapshots_runtime_id'), table_name='runtime_capability_snapshots')
    op.drop_index(op.f('ix_runtime_capability_snapshots_provider'), table_name='runtime_capability_snapshots')
    op.drop_index(op.f('ix_runtime_capability_snapshots_collected_at'), table_name='runtime_capability_snapshots')
    op.drop_index(op.f('ix_runtime_capability_snapshots_adapter_name'), table_name='runtime_capability_snapshots')
    op.drop_table('runtime_capability_snapshots')
    op.drop_index(op.f('ix_role_permissions_role_id'), table_name='role_permissions')
    op.drop_index(op.f('ix_role_permissions_permission_id'), table_name='role_permissions')
    op.drop_table('role_permissions')
    op.drop_index(op.f('ix_provider_principals_username'), table_name='provider_principals')
    op.drop_index(op.f('ix_provider_principals_user_id'), table_name='provider_principals')
    op.drop_index(op.f('ix_provider_principals_runtime_id'), table_name='provider_principals')
    op.drop_index(op.f('ix_provider_principals_provider'), table_name='provider_principals')
    op.drop_index(op.f('ix_provider_principals_principal_type'), table_name='provider_principals')
    op.drop_index(op.f('ix_provider_principals_last_seen_at'), table_name='provider_principals')
    op.drop_index(op.f('ix_provider_principals_external_id'), table_name='provider_principals')
    op.drop_index(op.f('ix_provider_principals_email'), table_name='provider_principals')
    op.drop_table('provider_principals')
    op.drop_index(op.f('ix_namespaces_name'), table_name='namespaces')
    op.drop_index(op.f('ix_namespaces_deleted_at'), table_name='namespaces')
    op.drop_table('namespaces')
    op.drop_table('credential_records')
    op.drop_index(op.f('ix_collection_jobs_trigger_type'), table_name='collection_jobs')
    op.drop_index(op.f('ix_collection_jobs_status'), table_name='collection_jobs')
    op.drop_index(op.f('ix_collection_jobs_runtime_id'), table_name='collection_jobs')
    op.drop_table('collection_jobs')
    op.drop_index(op.f('ix_analysis_workers_worker_key'), table_name='analysis_workers')
    op.drop_index(op.f('ix_analysis_workers_token_prefix'), table_name='analysis_workers')
    op.drop_index(op.f('ix_analysis_workers_status'), table_name='analysis_workers')
    op.drop_index(op.f('ix_analysis_workers_name'), table_name='analysis_workers')
    op.drop_index(op.f('ix_analysis_workers_last_seen_at'), table_name='analysis_workers')
    op.drop_index(op.f('ix_analysis_workers_created_by'), table_name='analysis_workers')
    op.drop_table('analysis_workers')
    op.drop_index(op.f('ix_ai_assets_status'), table_name='ai_assets')
    op.drop_index(op.f('ix_ai_assets_source_runtime_id'), table_name='ai_assets')
    op.drop_index(op.f('ix_ai_assets_source_provider'), table_name='ai_assets')
    op.drop_index(op.f('ix_ai_assets_name'), table_name='ai_assets')
    op.drop_index(op.f('ix_ai_assets_last_seen_at'), table_name='ai_assets')
    op.drop_index(op.f('ix_ai_assets_external_id'), table_name='ai_assets')
    op.drop_index(op.f('ix_ai_assets_content_hash'), table_name='ai_assets')
    op.drop_index(op.f('ix_ai_assets_asset_type'), table_name='ai_assets')
    op.drop_table('ai_assets')
    op.drop_index(op.f('ix_adapter_cursors_stream'), table_name='adapter_cursors')
    op.drop_index(op.f('ix_adapter_cursors_runtime_id'), table_name='adapter_cursors')
    op.drop_index(op.f('ix_adapter_cursors_provider'), table_name='adapter_cursors')
    op.drop_index(op.f('ix_adapter_cursors_last_success_at'), table_name='adapter_cursors')
    op.drop_index(op.f('ix_adapter_cursors_high_watermark'), table_name='adapter_cursors')
    op.drop_index(op.f('ix_adapter_cursors_adapter_name'), table_name='adapter_cursors')
    op.drop_table('adapter_cursors')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_external_subject'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_sso_provider_configs_provider_type'), table_name='sso_provider_configs')
    op.drop_index(op.f('ix_sso_provider_configs_name'), table_name='sso_provider_configs')
    op.drop_table('sso_provider_configs')
    op.drop_index(op.f('ix_runtime_instances_status'), table_name='runtime_instances')
    op.drop_index(op.f('ix_runtime_instances_provider'), table_name='runtime_instances')
    op.drop_index(op.f('ix_runtime_instances_name'), table_name='runtime_instances')
    op.drop_index(op.f('ix_runtime_instances_deploy_type'), table_name='runtime_instances')
    op.drop_table('runtime_instances')
    op.drop_index(op.f('ix_roles_scope'), table_name='roles')
    op.drop_index(op.f('ix_roles_key'), table_name='roles')
    op.drop_table('roles')
    op.drop_index(op.f('ix_permissions_scope'), table_name='permissions')
    op.drop_index(op.f('ix_permissions_key'), table_name='permissions')
    op.drop_table('permissions')
    op.drop_index(op.f('ix_org_units_unit_type'), table_name='org_units')
    op.drop_index(op.f('ix_org_units_parent_id'), table_name='org_units')
    op.drop_index(op.f('ix_org_units_cost_center'), table_name='org_units')
    op.drop_index(op.f('ix_org_units_code'), table_name='org_units')
    op.drop_table('org_units')
