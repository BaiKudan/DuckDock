import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from functools import lru_cache

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPOS_ROOT = PROJECT_ROOT / "repos"
DEFAULT_SANDBOX_CACHE_ROOT = PROJECT_ROOT / ".sandbox-cache"

# Known insecure placeholder shipped in .env.example; production must never boot with it.
INSECURE_SECRET_KEY_DEFAULT = "change-me-in-production-use-openssl-rand-hex-32"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        case_sensitive=True,
        extra="ignore",
    )

    # App
    APP_NAME: str = "DuckDock"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    API_V2_PREFIX: str = "/api/v2"

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, value):
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on", "debug", "development"}

    # Database
    DATABASE_URL: str = "mysql+aiomysql://duckdock:duckdock@localhost:3306/duckdock?charset=utf8mb4"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Transactional Outbox dispatcher. Domain writes never call Redis/Celery;
    # the dispatcher leases committed rows and publishes them afterward.
    OUTBOX_BATCH_SIZE: int = 50
    OUTBOX_LEASE_SECONDS: int = 60
    OUTBOX_MAX_ATTEMPTS: int = 5
    OUTBOX_BASE_RETRY_SECONDS: int = 5
    OUTBOX_MAX_RETRY_SECONDS: int = 3600
    OUTBOX_DISPATCH_SCHEDULE_SECONDS: int = 5
    EVALUATION_LEASE_SECONDS: int = 300
    EVALUATION_HEARTBEAT_SECONDS: int = 60
    EVALUATION_MAX_ATTEMPTS: int = 3
    EVALUATION_BASE_RETRY_SECONDS: int = 15
    EVALUATION_MAX_RETRY_SECONDS: int = 900
    EVALUATION_MAX_CONCURRENCY: int = 5
    EVALUATION_DISPATCH_SCHEDULE_SECONDS: int = 10
    EVALUATION_DISPATCH_BATCH_SIZE: int = 50
    ANNOTATION_QUEUE_LEASE_SECONDS: int = 300
    ANNOTATION_QUEUE_MAX_ATTEMPTS: int = 5
    ANNOTATION_QUEUE_BASE_RETRY_SECONDS: int = 15
    ANNOTATION_QUEUE_MAX_RETRY_SECONDS: int = 900
    ANNOTATION_QUEUE_DISPATCH_SCHEDULE_SECONDS: int = 10
    ANNOTATION_QUEUE_DISPATCH_BATCH_SIZE: int = 50
    SEMANTIC_MONITOR_LEASE_SECONDS: int = 300
    SEMANTIC_MONITOR_MAX_ATTEMPTS: int = 3
    SEMANTIC_MONITOR_BASE_RETRY_SECONDS: int = 15
    SEMANTIC_MONITOR_MAX_RETRY_SECONDS: int = 900
    SEMANTIC_MONITOR_DISPATCH_SCHEDULE_SECONDS: int = 10
    SEMANTIC_MONITOR_DISPATCH_BATCH_SIZE: int = 20

    # Foundation Reporter Session/Run starts are rollout controlled. Disabling
    # this blocks new work while completion endpoints remain available so
    # already accepted executions can reach a durable terminal state.
    AGENT_EXECUTION_INGESTION_ENABLED: bool = False
    AGENT_EXECUTION_RUNTIME_ALLOWLIST: Annotated[list[int], NoDecode] = []

    @field_validator("AGENT_EXECUTION_RUNTIME_ALLOWLIST", mode="before")
    @classmethod
    def parse_execution_runtime_allowlist(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            return json.loads(stripped)
        return [
            int(entry.strip())
            for entry in stripped.split(",")
            if entry.strip()
        ]

    # SEC-03: Redis token-bucket rate limiter for auth/token/register endpoints.
    # CRITICAL SAFETY: default OFF so dev/tests + `from app.main import app` are unaffected.
    # When enabled, an ip+username key gets RATE_LIMIT_MAX_ATTEMPTS per RATE_LIMIT_WINDOW_SECONDS;
    # exceeding it returns 429 and locks the key out for RATE_LIMIT_LOCKOUT_SECONDS.
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_MAX_ATTEMPTS: int = 5
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_LOCKOUT_SECONDS: int = 300
    # L0-SEC-XFF: trusted reverse-proxy IPs/CIDRs whose X-Forwarded-For we honour. Empty
    # (the default) ⇒ XFF is never trusted and the limiter always keys on request.client.host,
    # so a spoofed X-Forwarded-For from a direct/untrusted peer cannot move the lockout bucket.
    RATE_LIMIT_TRUSTED_PROXIES: Annotated[list[str], NoDecode] = []

    @field_validator("RATE_LIMIT_TRUSTED_PROXIES", mode="before")
    @classmethod
    def parse_trusted_proxies(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            return json.loads(stripped)
        return [entry.strip() for entry in stripped.split(",") if entry.strip()]

    # OPS-05: structured JSON logging level (applied via app.core.logging.configure_logging).
    LOG_LEVEL: str = "INFO"

    # JWT
    SECRET_KEY: str = INSECURE_SECRET_KEY_DEFAULT
    # 运行时凭证加密密钥：Fernet key，独立于 SECRET_KEY。
    # 生成:python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    DUCKDOCK_CREDENTIAL_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    SSO_STATE_EXPIRE_MINUTES: int = 10
    SSO_EXCHANGE_EXPIRE_MINUTES: int = 5
    BACKEND_BASE_URL: str = "http://127.0.0.1:8801"
    FRONTEND_BASE_URL: str = "http://127.0.0.1:5174"

    # Git repos storage root
    REPOS_ROOT: str = str(DEFAULT_REPOS_ROOT)

    # CORS
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:5174", "http://127.0.0.1:5174"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            return json.loads(stripped)
        return [origin.strip() for origin in stripped.split(",") if origin.strip()]

    @field_validator("CORS_ORIGINS")
    @classmethod
    def reject_wildcard_cors_origins(cls, value: list[str]) -> list[str]:
        if any(origin.strip() == "*" for origin in value):
            raise ValueError("CORS_ORIGINS must not contain '*' while credentialed CORS is enabled")
        return value

    # MinIO / S3-compatible artifact storage
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_PUBLIC_ENDPOINT: str = ""
    MINIO_ACCESS_KEY: str = "duckdock"
    MINIO_SECRET_KEY: str = "duckdock_secret"
    MINIO_BUCKET: str = "duckdock"
    MINIO_SECURE: bool = False
    REGISTRY_SIGNED_URL_EXPIRE_SECONDS: int = 900
    REGISTRY_SIGNED_URL_MAX_SECONDS: int = 86400
    REPORT_UPLOAD_URL_EXPIRE_SECONDS: int = 900
    REPORT_UPLOAD_MAX_SIZE_MB: int = 512
    REPORT_UPLOAD_DIRECT_INGEST_ENABLED: bool = False
    PACK_IMPORT_MAX_COMPRESSED_BYTES: int = 64 * 1024 * 1024
    PACK_IMPORT_MAX_UNCOMPRESSED_BYTES: int = 256 * 1024 * 1024
    PACK_IMPORT_MAX_ENTRY_BYTES: int = 64 * 1024 * 1024
    PACK_IMPORT_MAX_PAYLOADS: int = 64
    PACK_IMPORT_MAX_COMPRESSION_RATIO: int = 100
    PACK_IMPORT_MAX_MANIFEST_BYTES: int = 128 * 1024
    PACK_IMPORT_PENDING_TTL_SECONDS: int = 24 * 60 * 60
    PACK_BATCH_REPLAY_WINDOW_SECONDS: int = 7 * 24 * 60 * 60
    ADAPTER_HANDSHAKE_TTL_SECONDS: int = 24 * 60 * 60
    ADAPTER_HANDSHAKE_REPLAY_WINDOW_SECONDS: int = 7 * 24 * 60 * 60
    ADAPTER_MAX_CLOCK_SKEW_SECONDS: int = 300
    FLEET_HEARTBEAT_STALE_SECONDS: int = 5 * 60
    ANALYSIS_JOB_LEASE_SECONDS: int = 1800
    ANALYSIS_RESULT_CONTENT_TYPE: str = "application/json"

    # L0-OPS-RETENTION-BEAT: how often the retention GC sweep runs (celery beat), in seconds.
    # Default daily; the sweep enqueues per-namespace run_gc for every retention policy.
    RETENTION_GC_SCHEDULE_SECONDS: int = 86400
    # L4-03: global audit log retention. Set <=0 to disable audit-log pruning.
    AUDIT_LOG_RETENTION_DAYS: int = 90

    # Claude API — 历史遗留,不再用于扫描(已迁移到统一 DUCKDOCK_LLM_*);保留以兼容旧 .env。
    ANTHROPIC_API_KEY: str = ""

    # OpenAI 兼容 LLM base_url 纵深防御(SSRF 硬化,见 app/services/llm_http.py)。
    # 默认拒绝私网/环回/链路本地 IP 字面量与 localhost;开发态(DEBUG)或显式置 true 时放行
    # 本地 LLM 端点。所有 LLM 调用点(scan/handover/clinic/skill_gen)统一遵循。
    LLM_ALLOW_PRIVATE_BASE_URL: bool = False

    # L2-PROVIDER-UNIFY: 单一规范服务端 LLM 供应商(DashScope/Qwen OpenAI 兼容)。
    # 默认开启意图、key 闸控:解析出非空 api_key 即激活 LLM 路径,否则各消费点优雅降级。
    # 所有消费点(scan / clinic / handover / skill_gen)的 (key, base_url, model) 解析链
    # 都以此为终点回落(见 resolve_llm)。专属前缀(CLINIC_LLM_* 等)仅作可选覆盖。
    DUCKDOCK_LLM_API_KEY: str = ""
    DUCKDOCK_LLM_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DUCKDOCK_LLM_MODEL: str = "qwen3.6-plus"
    DUCKDOCK_LLM_ENABLE_THINKING: bool = True
    DUCKDOCK_LLM_TIMEOUT_SECONDS: int = 90
    DUCKDOCK_LLM_MAX_INPUT_CHARS: int = 24000
    SCAN_LLM_TIMEOUT_SECONDS: int = 60

    # Qwen / DashScope OpenAI-compatible API（技能生成）。
    # 默认值指向与规范 DUCKDOCK_LLM_* 相同的 base_url/model,故 skill-gen 行为不变;
    # key 留空时回落规范 key（见 SkillGenerationService）。
    SKILL_GEN_API_KEY: str = ""
    SKILL_GEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    SKILL_GEN_MODEL: str = "qwen3.6-plus"

    # Skill package sandbox validation (OpenClaw official sandbox backends)
    SKILL_SANDBOX_ENABLED: bool = True
    SKILL_SANDBOX_BACKEND: str = "docker"
    SKILL_SANDBOX_TIMEOUT_SECONDS: int = 240
    SKILL_SANDBOX_MODE: str = "all"
    SKILL_SANDBOX_SCOPE: str = "session"
    SKILL_SANDBOX_WORKSPACE_ACCESS: str = "none"
    SKILL_SANDBOX_CACHE_ROOT: str = str(DEFAULT_SANDBOX_CACHE_ROOT)
    SKILL_SANDBOX_DOCKER_IMAGE: str = "duckdock-openclaw-sandbox:local"
    SKILL_SANDBOX_DOCKER_NETWORK: str = "bridge"
    SKILL_SANDBOX_CLAWHUB_COMMAND: str = "clawhub"
    SKILL_SANDBOX_OPENCLAW_COMMAND: str = "openclaw"
    SKILL_SANDBOX_OPENCLAW_PACKAGE: str = "openclaw@latest"
    SKILL_SANDBOX_CLAWHUB_PACKAGE: str = "clawhub@latest"
    SKILL_SANDBOX_REGISTRY_URL: str = ""
    SKILL_SANDBOX_ENABLE_AGENT_SMOKE: bool = False
    SKILL_SANDBOX_AGENT_MODEL: str = ""
    SKILL_SANDBOX_SSH_TARGET: str = ""
    SKILL_SANDBOX_SSH_PORT: int = 22
    SKILL_SANDBOX_SSH_USER: str = ""
    SKILL_SANDBOX_SSH_IDENTITY_FILE: str = ""
    SKILL_SANDBOX_SSH_KNOWN_HOSTS_FILE: str = ""
    SKILL_SANDBOX_SSH_WORKSPACE_ROOT: str = "/tmp/openclaw-sandboxes"
    SKILL_SANDBOX_SSH_STRICT_HOST_KEY_CHECKING: bool = True
    SKILL_SANDBOX_SSH_UPDATE_HOST_KEYS: bool = True

    # Clinic evaluation provider
    CLINIC_LLM_PROVIDER: str = "openai_compatible"
    CLINIC_LLM_API_KEY: str = ""
    CLINIC_LLM_BASE_URL: str = ""
    CLINIC_LLM_MODEL: str = ""
    CLINIC_LLM_TIMEOUT_SECONDS: int = 20
    CLINIC_EVAL_MAX_SKILLS: int = 12
    CLINIC_EVAL_MODE: str = "hybrid"
    CLINIC_RUBRIC_VERSION: str = "v1"
    CLINIC_JUDGE_PROMPT_VERSION: str = "v1"

    # Handover LLM advisor：默认开启意图但仍由 key 闸控，开启后使用 OpenAI 兼容 LLM
    # 产出每个资产的 {推荐动作, 置信度, 理由};解析不出 key 时仍降级 RuleBased(无 key 行为不变)。
    # key/base_url/model 留空时回落规范 DUCKDOCK_LLM_*（见 resolve_llm）。
    HANDOVER_LLM_ENABLED: bool = True
    HANDOVER_LLM_API_KEY: str = ""
    HANDOVER_LLM_BASE_URL: str = ""
    HANDOVER_LLM_MODEL: str = ""
    HANDOVER_LLM_TIMEOUT_SECONDS: int = 30

    # Langfuse observability
    CLINIC_LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"
    LANGFUSE_PUBLIC_BASE_URL: str = ""
    LANGFUSE_ENVIRONMENT: str = "development"
    LANGFUSE_TIMEOUT_SECONDS: int = 5
    LANGFUSE_COMPATIBILITY_PROFILE: str = "langfuse-v4"

    # Optional semantic-failure clustering. The endpoint is deployment config,
    # never accepted from an API request or persisted in governance rows.
    SEMANTIC_EMBEDDING_ENABLED: bool = False
    SEMANTIC_EMBEDDING_PROFILE: str = "openai-compatible-local"
    SEMANTIC_EMBEDDING_BASE_URL: str = ""
    SEMANTIC_EMBEDDING_API_KEY: str = ""
    SEMANTIC_EMBEDDING_TIMEOUT_SECONDS: int = 15

    @field_validator("LANGFUSE_COMPATIBILITY_PROFILE")
    @classmethod
    def validate_langfuse_compatibility_profile(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != "langfuse-v4":
            raise ValueError(
                "Unsupported LANGFUSE_COMPATIBILITY_PROFILE; "
                "register a new adapter before changing major versions"
            )
        return normalized

    # Optional component manager. This is intentionally limited to the
    # same compose file so optional services stay outside the core runtime.
    COMPONENT_MANAGER_ENABLED: bool = True
    COMPONENT_DOCKER_COMMAND: str = "docker"
    COMPONENT_DOCKER_COMPOSE_FILE: str = str(PROJECT_ROOT / "docker-compose.yml")
    COMPONENT_DOCKER_PROJECT_DIRECTORY: str = str(PROJECT_ROOT)
    COMPONENT_DOCKER_TIMEOUT_SECONDS: int = 180
    COMPONENT_LANGFUSE_URL: str = "http://127.0.0.1:3200"

    # Readiness probes the separately deployable Prometheus component.
    # Compose overrides this with the internal service address; host-native dev
    # can use the loopback default.
    PROMETHEUS_BASE_URL: str = "http://127.0.0.1:9090"
    PROMETHEUS_READINESS_TIMEOUT_SECONDS: int = 3


def assert_production_security(settings: Settings) -> None:
    """Fail fast when a production runtime (DEBUG=False) is configured insecurely.

    Refuses to boot if SECRET_KEY is empty / still the shipped insecure default, or if
    the Fernet credential key (DUCKDOCK_CREDENTIAL_KEY) is missing. No-op under DEBUG so
    the local dev / test path (which loads the placeholder secret from .env) is unaffected.
    """
    if settings.DEBUG:
        return

    problems: list[str] = []
    if not settings.SECRET_KEY or settings.SECRET_KEY == INSECURE_SECRET_KEY_DEFAULT:
        problems.append("SECRET_KEY is empty or still set to the insecure default")
    if not settings.DUCKDOCK_CREDENTIAL_KEY:
        problems.append("DUCKDOCK_CREDENTIAL_KEY is empty")

    if problems:
        raise RuntimeError(
            "Refusing to start in production (DEBUG=False) with insecure configuration: "
            + "; ".join(problems)
            + ". Set strong values via environment / .env before deploying."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def resolve_llm(prefix: str) -> tuple[str, str, str]:
    """解析某消费点的 (api_key, base_url, model)，专属值优先、缺失回落规范 DUCKDOCK_LLM_*。

    ``prefix`` 为该消费点的设置前缀，如 ``"CLINIC_LLM"`` / ``"HANDOVER_LLM"`` /
    ``"DUCKDOCK_LLM"``（scan 直接复用规范前缀）。每个字段独立回落：例如只覆盖了
    ``<prefix>_MODEL`` 时，key/base_url 仍取规范值。这样所有服务端 LLM 调用统一收敛到
    单一 DashScope/Qwen 供应商，专属前缀只作可选覆盖（L2-PROVIDER-UNIFY）。

    返回去除首尾空白后的三元组；空字符串表示该字段未配置（由调用方判定降级）。
    """

    def _pick(suffix: str) -> str:
        own = getattr(settings, f"{prefix}_{suffix}", "") or ""
        own = own.strip()
        if own:
            return own
        canon = getattr(settings, f"DUCKDOCK_LLM_{suffix}", "") or ""
        return canon.strip()

    return _pick("API_KEY"), _pick("BASE_URL"), _pick("MODEL")
