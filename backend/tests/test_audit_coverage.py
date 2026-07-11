"""写端点审计覆盖回归锁(specs/001 T070 · 宪法原则 IV「审计与证据不可绕过」)。

原则 IV:每一次写操作和投递事件都进审计日志。本测试自动发现 `/api/v1` 下**全部**
写端点(POST/PATCH/PUT/DELETE),断言每个 handler 要么:
  (a) 在自身函数体内调用 `audit(...)`;要么
  (b) 在下方 `_NON_AUDITING` 显式豁免表里、且附带理由。

新增写端点若两者都不满足 → 测试失败,强制作者**自觉决定**:补审计,还是登记豁免理由。
这把「当前已审计」的事实固化为「永远不回退」的护栏,防止悄悄引入未审计的写路径。

豁免分三类(2026-06-16 全量对账):
  · delegated —— handler 委托给会审计的 service(grep 源码看不到,但确实审计);
  · non-mutating —— 只读自检/无状态校验,无写操作;
  · worker-protocol —— Worker token 机器接口(非用户),生命周期由作业状态机 + 产物追溯。
"""
from __future__ import annotations

import importlib
import inspect
import re

import pytest

# api_router 挂载的全部端点模块(见 app/api/v1/router.py)
_ENDPOINT_MODULES = [
    "auth", "iam", "namespaces", "skills", "clawhub", "registry", "scans",
    "clinic", "components", "control_plane", "analysis", "people", "audit",
    "webhooks", "robots", "lifecycle", "replication",
]
_WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
_AUDIT_CALL = re.compile(r"\baudit\s*\(")

# handler 名 -> 不在自身函数体审计的理由。增删此表都必须写清原因。
_NON_AUDITING: dict[str, str] = {
    # delegated:委托给会审计的 service / 内部 helper
    "clawhub_publish_skill": "delegates to _publish_compat_skill which calls audit()",
    "run_rule": "delegates to execute_replication_rule which calls audit()",
    # non-mutating:只读自检 / 无状态校验,无写操作
    "test_runtime": "Push-only 上报链路自检(T080),只读、不改状态",
    "validate_package": "无状态包校验,不落库",
    "refresh": "token 轮换,是已审计登录的延续",
    "exchange_sso_token": "一次性 SSO code 兑换,是已审计 SSO 登录的延续",
    # worker-protocol:Worker token 机器接口,生命周期由 report_analysis_jobs 状态 + 产物追溯
    "lease_analysis_job": "worker-token lease 协议;生命周期记于作业状态机,非用户审计",
    "heartbeat_worker_analysis_job": "worker-token 心跳,高频,不应进审计日志",
    "finalize_worker_analysis_job": "worker-token finalize;结果落 analysis_result_artifacts",
    "fail_worker_analysis_job": "worker-token fail;失败态落作业状态机",
    "reporter_heartbeat_endpoint": "reporter-token 心跳,高频,状态落 runtime metadata / token last_used_at",
}


def _discover_write_endpoints() -> list[tuple[str, str, str, object]]:
    """返回 (module, method, path, endpoint_fn) — 全部 v1 写端点。"""
    found: list[tuple[str, str, str, object]] = []
    for mod_name in _ENDPOINT_MODULES:
        module = importlib.import_module(f"app.api.v1.endpoints.{mod_name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in router.routes:
            methods = getattr(route, "methods", set()) or set()
            write = methods & _WRITE_METHODS
            if not write:
                continue
            found.append((mod_name, sorted(write)[0], getattr(route, "path", ""), route.endpoint))
    return found


def _handler_audits(fn) -> bool:
    try:
        return bool(_AUDIT_CALL.search(inspect.getsource(fn)))
    except OSError:
        return False


def test_write_endpoints_are_discovered():
    """护栏自身的保险:发现机制必须真的找到一批写端点(防止 import 静默失败把表清空)。"""
    endpoints = _discover_write_endpoints()
    assert len(endpoints) >= 80, f"只发现 {len(endpoints)} 个写端点,发现机制可能坏了"


def test_every_write_endpoint_audits_or_is_justified():
    """原则 IV 回归锁:每个写端点要么自身审计,要么在豁免表里带理由。"""
    violations: list[str] = []
    for mod_name, method, path, fn in _discover_write_endpoints():
        name = fn.__name__
        if _handler_audits(fn):
            continue
        if name in _NON_AUDITING:
            continue
        violations.append(f"{method} {path} ({mod_name}.{name}) 既不审计也未登记豁免理由")
    assert not violations, (
        "发现未审计且未豁免的写端点(违反宪法原则 IV)。"
        "请在 handler 内调用 audit(...),或在 _NON_AUDITING 登记理由:\n  - "
        + "\n  - ".join(violations)
    )


def test_no_stale_allowlist_entries():
    """豁免表不得腐烂:每个豁免项必须对应一个真实存在、且确实未在体内审计的写端点。"""
    by_name = {fn.__name__: fn for *_rest, fn in _discover_write_endpoints()}
    stale: list[str] = []
    for name in _NON_AUDITING:
        fn = by_name.get(name)
        if fn is None:
            stale.append(f"{name}: 已不是写端点(应从豁免表删除)")
        elif _handler_audits(fn):
            stale.append(f"{name}: 现在已自身审计(应从豁免表删除)")
    assert not stale, "豁免表有陈旧项:\n  - " + "\n  - ".join(stale)


@pytest.mark.parametrize("reason", _NON_AUDITING.values())
def test_allowlist_entries_carry_reason(reason: str):
    assert reason.strip(), "每个审计豁免都必须写明理由"
