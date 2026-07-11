"""OpenAI 兼容 LLM base_url 纵深防御校验(SSRF 硬化 · app/services/llm_http.py）。

覆盖:
  · validate_llm_base_url:接受 http/https 公网 URL;拒绝缺 scheme / 非 http(s) /
    空 / 无 host;默认拒绝私网·环回·链路本地 IP 字面量与 localhost;
  · allow_private 显式放行,以及 DEBUG / LLM_ALLOW_PRIVATE_BASE_URL 两种默认放行路径;
  · 三处 LLM 调用点(handover / clinic / skill_gen)一致地把非法 base_url 转成降级或报错,
    而不是静默把请求打到内网。
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.clinic_service import (
    ClinicService,
    NoopClinicJudgeProvider,
    OpenAICompatibleClinicJudge,
)
from app.services.handover_advisor_service import (
    LLMHandoverAdvisor,
    RuleBasedHandoverAdvisor,
    build_handover_advisor,
)
from app.services.llm_http import LLMConfigError, validate_llm_base_url
from app.services.skill_generation_service import (
    SkillGenerationError,
    skill_generation_service,
)


@pytest.fixture(autouse=True)
def _strict_ssrf_defaults(monkeypatch):
    """把校验默认态钉成生产语义(拒私网),不受本地 .env 的 DEBUG=true 影响。

    需要放行私网的用例(test_debug_default_permits_private /
    test_allow_private_setting_default_permits_private)在各自体内再 monkeypatch 覆盖。
    """
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "LLM_ALLOW_PRIVATE_BASE_URL", False)


# ── validate_llm_base_url ───────────────────────────────────────

@pytest.mark.parametrize(
    "url",
    [
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "http://api.openai.com/v1",
        "https://llm.example.com:8443/v1",
        "https://[2606:4700::1]/v1",  # 公网 IPv6 字面量
    ],
)
def test_accepts_public_http_urls(url):
    assert validate_llm_base_url(url) == url


def test_strips_surrounding_whitespace():
    assert validate_llm_base_url("  https://llm.example.com/v1  ") == "https://llm.example.com/v1"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "llm.example.com/v1",  # 缺 scheme
        "ftp://llm.example.com/v1",
        "file:///etc/passwd",
        "gopher://llm.example.com/v1",
    ],
)
def test_rejects_missing_or_non_http_scheme(url):
    with pytest.raises(LLMConfigError):
        validate_llm_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/v1",  # 环回 127/8
        "http://127.5.5.5:8000/v1",
        "http://10.0.0.5/v1",  # 私网 10/8
        "http://172.16.0.1/v1",  # 私网 172.16/12
        "http://192.168.1.10/v1",  # 私网 192.168/16
        "http://169.254.169.254/latest/meta-data",  # 云元数据 链路本地 169.254/16
        "http://0.0.0.0/v1",  # unspecified
        "http://localhost:1234/v1",
        "http://[::1]/v1",  # IPv6 环回
    ],
)
def test_rejects_private_and_local_hosts_by_default(url):
    # 默认 settings.DEBUG=False 且 LLM_ALLOW_PRIVATE_BASE_URL=False
    with pytest.raises(LLMConfigError):
        validate_llm_base_url(url)


def test_allow_private_flag_permits_local_hosts():
    assert validate_llm_base_url("http://127.0.0.1:8000/v1", allow_private=True)
    assert validate_llm_base_url("http://localhost:1234/v1", allow_private=True)


def test_debug_default_permits_private(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "LLM_ALLOW_PRIVATE_BASE_URL", False)
    assert validate_llm_base_url("http://127.0.0.1:8000/v1")


def test_allow_private_setting_default_permits_private(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "LLM_ALLOW_PRIVATE_BASE_URL", True)
    assert validate_llm_base_url("http://10.0.0.5/v1")


# ── 三处调用点的一致行为 ─────────────────────────────────────────

def test_handover_advisor_init_rejects_private_base_url():
    with pytest.raises(LLMConfigError):
        LLMHandoverAdvisor(
            api_key="k", base_url="http://169.254.169.254/v1", model="qwen", timeout_seconds=5
        )


def test_build_handover_advisor_falls_back_on_invalid_base_url(monkeypatch):
    monkeypatch.setattr(settings, "HANDOVER_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "HANDOVER_LLM_API_KEY", "k")
    monkeypatch.setattr(settings, "HANDOVER_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setattr(settings, "HANDOVER_LLM_MODEL", "qwen")
    # 非法 base_url 不应抛出,而是回落规则版兜底
    assert isinstance(build_handover_advisor(), RuleBasedHandoverAdvisor)


def test_clinic_judge_init_rejects_private_base_url():
    with pytest.raises(LLMConfigError):
        OpenAICompatibleClinicJudge(
            api_key="k",
            base_url="http://10.0.0.5/v1",
            model="qwen",
            timeout_seconds=5,
            prompt_version="v1",
        )


def test_clinic_build_provider_falls_back_to_noop_on_invalid_base_url(monkeypatch):
    monkeypatch.setattr(settings, "CLINIC_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(settings, "CLINIC_LLM_API_KEY", "k")
    monkeypatch.setattr(settings, "CLINIC_LLM_BASE_URL", "http://192.168.1.10/v1")
    monkeypatch.setattr(settings, "CLINIC_LLM_MODEL", "qwen")
    provider = ClinicService()._build_provider()
    assert isinstance(provider, NoopClinicJudgeProvider)


async def test_skill_generation_rejects_private_base_url(monkeypatch):
    monkeypatch.setattr(settings, "SKILL_GEN_API_KEY", "k")
    monkeypatch.setattr(settings, "SKILL_GEN_BASE_URL", "http://127.0.0.1:8000/v1")
    with pytest.raises(SkillGenerationError, match="base_url"):
        await skill_generation_service.generate_skill_draft(
            namespace="ns",
            skill_name="demo",
            description=None,
            tag="v1",
            user_prompt="do something",
        )
