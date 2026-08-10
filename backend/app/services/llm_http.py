"""OpenAI 兼容 LLM 客户端 base_url 的共享校验(纵深防御 · SSRF 硬化)。

DuckDock 三处 LLM 调用点 —— 交接顾问(handover_advisor_service)、诊所评审
(clinic_service)、技能生成(skill_generation_service)—— 共用同一形态:从服务端
settings/env 取 ``base_url``,再 POST 到 ``f"{base_url}/chat/completions"``。
``base_url`` 是运维配置而非用户输入,风险偏低;此处做**纵深防御**,确保一个被误填
或被篡改的 env 值不会把服务端 LLM 调用静默地指向 loopback / 云元数据 / 内网地址。

该校验统一应用到所有 LLM 调用点。

校验:
  1. 必须显式 http/https scheme,且解析得到 host;
  2. 默认拒绝私网/环回/链路本地的 **IP 字面量**(127/8、10/8、172.16/12、
     192.168/16、169.254/16、::1 等)与 ``localhost`` —— 除非显式放行。

放行开关 ``allow_private`` 默认取 ``settings.DEBUG or
settings.LLM_ALLOW_PRIVATE_BASE_URL``:开发态 / 运维显式 opt-in 时允许本地 LLM
端点,生产默认拒绝。注意这是**字面量 host 守卫**,不做 DNS 解析(不在构造期发起
网络请求),因此不能替代完整的运行期 SSRF 防护。
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from app.core.config import settings


class LLMConfigError(ValueError):
    """LLM 客户端 base_url 未通过校验。"""


_ALLOWED_SCHEMES = {"http", "https"}
# 不依赖 IP 解析也应判为本地的主机名。
_LOCAL_HOSTNAMES = {"localhost", "ip6-localhost", "ip6-loopback"}


def _is_private_host(host: str) -> bool:
    """host 是否为私网/环回/链路本地/保留的 IP 字面量,或公认的本地主机名。

    公网主机名返回 False —— 此处只做字面量守卫,**不解析 DNS**(避免在构造期发起
    网络请求),因此不是完整的 SSRF 解析器。
    """
    bare = host.strip("[]")  # IPv6 字面量可能带方括号
    if bare.lower() in _LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return False  # 非 IP 字面量 → 视作(公网)主机名
    return (
        ip.is_private  # 含 10/8、172.16/12、192.168/16、127/8
        or ip.is_loopback
        or ip.is_link_local  # 含 169.254/16(云元数据 169.254.169.254)
        or ip.is_reserved
        or ip.is_unspecified  # 0.0.0.0 / ::
    )


def validate_llm_base_url(base_url: str, *, allow_private: bool | None = None) -> str:
    """校验 OpenAI 兼容 LLM 的 ``base_url``,通过则返回去除首尾空白后的值。

    强制:(1) 显式 http/https scheme 且有 host;(2) 除非 ``allow_private``,host 不得
    为私网/环回/链路本地的 IP 字面量或 ``localhost``。任一不满足抛 :class:`LLMConfigError`。

    ``allow_private`` 为 None 时取 ``settings.DEBUG or settings.LLM_ALLOW_PRIVATE_BASE_URL``。
    """
    if allow_private is None:
        allow_private = settings.DEBUG or settings.LLM_ALLOW_PRIVATE_BASE_URL

    url = (base_url or "").strip()
    if not url:
        raise LLMConfigError("LLM base_url is empty")

    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise LLMConfigError(
            f"LLM base_url must use http or https scheme, got {parts.scheme!r}"
        )
    if not parts.hostname:
        raise LLMConfigError(f"LLM base_url has no host: {url!r}")

    if not allow_private and _is_private_host(parts.hostname):
        raise LLMConfigError(
            f"LLM base_url host {parts.hostname!r} is a private/loopback/link-local "
            "address; set LLM_ALLOW_PRIVATE_BASE_URL=true (or DEBUG) to allow it"
        )
    return url
