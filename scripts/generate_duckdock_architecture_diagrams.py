from __future__ import annotations

from pathlib import Path


OUT_DIR = Path("docs/architecture-diagrams")


STYLE = """
text { font-family: 'Helvetica Neue', Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.title { fill:#111827; font-size:24px; font-weight:700; }
.subtitle { fill:#6b7280; font-size:13px; }
.lane-label { fill:#4f46e5; font-size:11px; font-weight:700; letter-spacing:.08em; }
.node-title { fill:#111827; font-size:14px; font-weight:700; }
.node-sub { fill:#6b7280; font-size:11px; }
.tiny { fill:#6b7280; font-size:10px; }
.arrow-label { fill:#374151; font-size:11px; font-weight:600; }
.tag { fill:#4b5563; font-size:10px; font-weight:600; }
"""


DEFS = """
<defs>
  <filter id="soft-shadow" x="-20%" y="-20%" width="140%" height="140%">
    <feDropShadow dx="0" dy="4" stdDeviation="4" flood-color="#0f172a" flood-opacity="0.10"/>
  </filter>
  <marker id="arrow-blue" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
    <polygon points="0 0,10 3.5,0 7" fill="#2563eb"/>
  </marker>
  <marker id="arrow-green" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
    <polygon points="0 0,10 3.5,0 7" fill="#16a34a"/>
  </marker>
  <marker id="arrow-orange" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
    <polygon points="0 0,10 3.5,0 7" fill="#ea580c"/>
  </marker>
  <marker id="arrow-purple" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
    <polygon points="0 0,10 3.5,0 7" fill="#9333ea"/>
  </marker>
  <marker id="arrow-gray" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
    <polygon points="0 0,10 3.5,0 7" fill="#6b7280"/>
  </marker>
</defs>
"""


def esc(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def text_lines(lines: list[str], x: int, y: int, cls: str = "node-sub", gap: int = 17) -> str:
    return "\n".join(f'<text x="{x}" y="{y + i * gap}" class="{cls}">{esc(line)}</text>' for i, line in enumerate(lines))


def node(
    x: int,
    y: int,
    w: int,
    h: int,
    title: str,
    subs: list[str],
    *,
    fill: str = "#ffffff",
    stroke: str = "#d1d5db",
    icon: str = "",
    icon_fill: str = "#eff6ff",
    icon_stroke: str = "#bfdbfe",
    icon_text: str = "#2563eb",
    shadow: bool = True,
) -> str:
    group = ' filter="url(#soft-shadow)"' if shadow else ""
    icon_svg = ""
    text_x = x + 62 if icon else x + 14
    if icon:
        icon_svg = f"""
    <rect x="{x + 14}" y="{y + 18}" width="38" height="38" rx="8" fill="{icon_fill}" stroke="{icon_stroke}"/>
    <text x="{x + 33}" y="{y + 42}" text-anchor="middle" fill="{icon_text}" font-size="13" font-weight="700">{esc(icon)}</text>"""
    return f"""
  <g{group}>
    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>
{icon_svg}
    <text x="{text_x}" y="{y + 28}" class="node-title">{esc(title)}</text>
{text_lines(subs, text_x, y + 48)}
  </g>"""


def cylinder(cx: int, top: int, w: int, h: int, title: str, sub: str, fill: str, stroke: str) -> str:
    left = cx - w // 2
    return f"""
  <g filter="url(#soft-shadow)">
    <ellipse cx="{cx}" cy="{top}" rx="{w // 2}" ry="{w // 7}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>
    <rect x="{left}" y="{top}" width="{w}" height="{h}" fill="{fill}" stroke="none"/>
    <line x1="{left}" y1="{top}" x2="{left}" y2="{top + h}" stroke="{stroke}" stroke-width="1.5"/>
    <line x1="{left + w}" y1="{top}" x2="{left + w}" y2="{top + h}" stroke="{stroke}" stroke-width="1.5"/>
    <ellipse cx="{cx}" cy="{top + h}" rx="{w // 2}" ry="{w // 7}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>
    <text x="{cx}" y="{top + h // 2}" text-anchor="middle" class="node-title">{esc(title)}</text>
    <text x="{cx}" y="{top + h // 2 + 18}" text-anchor="middle" class="node-sub">{esc(sub)}</text>
  </g>"""


def lane(x: int, y: int, w: int, h: int, label: str, fill: str, stroke: str) -> str:
    return f"""
  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" fill-opacity="0.42" stroke="{stroke}" stroke-dasharray="7,5"/>
  <text x="{x + 16}" y="{y + 24}" class="lane-label">{esc(label)}</text>"""


def arrow(x1: int, y1: int, x2: int, y2: int, label: str, color: str = "#2563eb", marker: str = "blue", dash: str = "") -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    mx = (x1 + x2) // 2
    my = (y1 + y2) // 2 - 8
    return f"""
  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.8"{dash_attr} marker-end="url(#arrow-{marker})"/>
  <rect x="{mx - 42}" y="{my - 13}" width="84" height="18" rx="4" fill="#ffffff" opacity="0.92"/>
  <text x="{mx}" y="{my}" text-anchor="middle" class="arrow-label">{esc(label)}</text>"""


def path_arrow(d: str, label_x: int, label_y: int, label: str, color: str = "#2563eb", marker: str = "blue", dash: str = "") -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return f"""
  <path d="{d}" stroke="{color}" stroke-width="1.8" fill="none"{dash_attr} marker-end="url(#arrow-{marker})"/>
  <rect x="{label_x - 48}" y="{label_y - 14}" width="96" height="18" rx="4" fill="#ffffff" opacity="0.92"/>
  <text x="{label_x}" y="{label_y}" text-anchor="middle" class="arrow-label">{esc(label)}</text>"""


def legend(x: int, y: int) -> str:
    rows = [
        ("#2563eb", "blue", "", "主数据流"),
        ("#16a34a", "green", "5,3", "写入/入库"),
        ("#ea580c", "orange", "", "控制/触发"),
        ("#9333ea", "purple", "", "LLM/转换"),
        ("#6b7280", "gray", "4,2", "异步/可选"),
    ]
    body = []
    for i, (color, marker, dash, label) in enumerate(rows):
        yy = y + i * 20
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        body.append(f'<line x1="{x}" y1="{yy}" x2="{x + 32}" y2="{yy}" stroke="{color}" stroke-width="1.6"{dash_attr} marker-end="url(#arrow-{marker})"/>')
        body.append(f'<text x="{x + 42}" y="{yy + 4}" class="tiny">{esc(label)}</text>')
    return "\n".join(body)


def svg(width: int, height: int, title: str, subtitle: str, body: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <style>{STYLE}</style>
  {DEFS}
  <rect width="{width}" height="{height}" fill="#ffffff"/>
  <text x="36" y="42" class="title">{esc(title)}</text>
  <text x="36" y="66" class="subtitle">{esc(subtitle)}</text>
{body}
</svg>
"""


def overall() -> str:
    body = ""
    body += lane(28, 94, 244, 630, "USERS / RUNTIMES", "#eff6ff", "#bfdbfe")
    body += lane(304, 94, 312, 630, "FASTAPI CONTROL PLANE", "#f8fafc", "#d1d5db")
    body += lane(648, 94, 260, 630, "DATA / ARTIFACTS", "#f0fdf4", "#bbf7d0")
    body += lane(940, 94, 310, 630, "ASYNC / OPTIONAL", "#faf5ff", "#e9d5ff")
    body += node(56, 144, 188, 86, "企业运行时", ["OpenClaw / WorkBuddy", "ArkClaw / JVS / 定制"], icon="RT")
    body += node(56, 268, 188, 86, "管理员控制台", ["运行时、Token、组件", "资产、交接、审计"], icon="ADM", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += node(56, 392, 188, 86, "个人工作台", ["我的 AI 资产", "确认/补充/排除"], icon="ME", icon_fill="#f0fdfa", icon_stroke="#99f6e4", icon_text="#0f766e")
    body += node(56, 516, 188, 86, "私有 Skill 消费方", ["SkillHub Registry", "ClawHub / 同步客户端"], icon="SK", icon_fill="#fff7ed", icon_stroke="#fed7aa", icon_text="#ea580c")
    body += node(336, 128, 244, 78, "Auth / IAM / RBAC", ["用户、岗位状态、权限、审计"], icon="IAM")
    body += node(336, 236, 244, 78, "Agent 控制平面", ["Runtime、Reporter Credential", "Report Session"], icon="ACP")
    body += node(336, 344, 244, 78, "AI 资产与交接", ["Assets、Ownership", "WorkTrace、Handover"], icon="AST")
    body += node(336, 452, 244, 78, "Skill 仓库治理", ["Namespace、Version、Scan", "Public Release"], icon="REG")
    body += node(336, 560, 244, 78, "组件管理", ["核心服务默认启动", "可选 Langfuse"], icon="CMP")
    body += cylinder(778, 134, 122, 78, "MySQL", "索引 + lease", "#e0f2fe", "#4479A1")
    body += cylinder(778, 280, 122, 78, "MinIO", "包与结果制品", "#dcfce7", "#16a34a")
    body += cylinder(778, 426, 122, 78, "Git Bare", "Skill 版本", "#fef3c7", "#d97706")
    body += cylinder(778, 572, 122, 78, "Redis", "Celery 队列", "#fee2e2", "#dc2626")
    body += node(974, 142, 238, 82, "Reporter Skill", ["对话安装，周期上报", "不持有 MinIO 密钥"], icon="REP", icon_fill="#eff6ff")
    body += node(974, 270, 238, 82, "Analysis Worker", ["MySQL lease 领任务", "baseline 或 qwen3.7-max"], icon="ANL", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += node(974, 398, 238, 82, "Clinic / Sandbox", ["扫描、运行验证、质量评估"], icon="QA", icon_fill="#fff7ed", icon_stroke="#fed7aa", icon_text="#ea580c")
    body += node(974, 526, 238, 82, "Langfuse 可选组件", ["Trace / Prompt 审计", "Compose profile 解耦"], icon="LF", icon_fill="#faf5ff", icon_stroke="#e9d5ff", icon_text="#9333ea")
    body += arrow(244, 187, 336, 275, "上报", "#2563eb", "blue")
    body += arrow(244, 311, 336, 167, "管理", "#ea580c", "orange")
    body += arrow(244, 435, 336, 383, "确认", "#2563eb", "blue")
    body += arrow(244, 559, 336, 491, "拉取", "#2563eb", "blue")
    body += arrow(580, 167, 717, 173, "用户/权限", "#16a34a", "green", "5,3")
    body += arrow(580, 275, 717, 319, "上传元数据", "#16a34a", "green", "5,3")
    body += arrow(580, 383, 717, 173, "资产索引", "#16a34a", "green", "5,3")
    body += arrow(580, 491, 717, 465, "Git tag", "#16a34a", "green", "5,3")
    body += path_arrow("M580,491 C626,532 666,594 717,611", 640, 560, "scan/clinic任务", "#16a34a", "green", "5,3")
    body += path_arrow("M580,275 C690,72 910,106 974,183", 884, 122, "安装说明", "#ea580c", "orange")
    body += arrow(717, 173, 974, 311, "lease任务", "#ea580c", "orange")
    body += arrow(839, 319, 974, 311, "下载包", "#2563eb", "blue")
    body += arrow(974, 334, 839, 319, "结果回写", "#16a34a", "green", "5,3")
    body += path_arrow("M839,611 C884,548 924,486 974,439", 902, 536, "Celery执行", "#6b7280", "gray", "4,2")
    body += path_arrow("M974,439 C850,456 720,478 580,491", 796, 452, "质量结果", "#9333ea", "purple")
    body += arrow(580, 599, 974, 567, "启停组件", "#6b7280", "gray", "4,2")
    body += legend(56, 662)
    return svg(1280, 760, "DuckDock 总体业务架构图", "企业 AI Agent 资产与交接控制平面：运行时接入、私有 Skill 仓库、异步 LLM 分析、精简索引入库和可选观测组件。", body)


def reporter_flow() -> str:
    body = ""
    body += lane(28, 96, 260, 590, "ENROLLMENT SETUP", "#eff6ff", "#bfdbfe")
    body += lane(312, 96, 260, 590, "RUNTIME SIDE", "#fff7ed", "#fed7aa")
    body += lane(596, 96, 260, 590, "DUCKDOCK API", "#f8fafc", "#d1d5db")
    body += lane(880, 96, 260, 590, "OBJECT STORE / QUEUE", "#f0fdf4", "#bbf7d0")
    body += node(54, 140, 208, 78, "登录 DuckDock", ["员工/Agent 认证", "自助登记 endpoint"], icon="1")
    body += node(54, 260, 208, 78, "自助 Reporter 凭证", ["dkr_report_* credential", "长期可撤销 / 可轮换"], icon="2")
    body += node(54, 380, 208, 78, "指定私有 Registry", ["skill.md 地址", "安装 duckdock-reporter"], icon="3")
    body += node(338, 140, 208, 78, "对话式安装", ["WorkBuddy/OpenClaw", "按 SOP 创建任务"], icon="A")
    body += node(338, 260, 208, 78, "周期采集", ["Skills/Agents/Prompts", "会话摘要/记忆索引/证据"], icon="B")
    body += node(338, 380, 208, 78, "生成 pack", ["duckdock-pack-v1.zip", "hash、manifest、summary"], icon="C")
    body += node(622, 140, 208, 78, "Create Upload Session", ["返回一次性 PUT URL", "记录 report_id/object_key"], icon="API")
    body += node(622, 260, 208, 78, "Finalize Report", ["校验 size/sha256", "创建 analysis job"], icon="FIN")
    body += node(622, 380, 208, 78, "Reporter 状态", ["Portal 显示健康度", "管理员可审计"], icon="STA")
    body += cylinder(978, 160, 120, 78, "MinIO", "原始 pack 暂存", "#dcfce7", "#16a34a")
    body += node(906, 300, 208, 78, "Analysis Job", ["pending 队列", "等待 worker lease"], icon="JOB", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += node(906, 420, 208, 78, "MySQL 元数据", ["report_id/runtime_id", "sha256/object_key/status"], icon="SQL", icon_fill="#e0f2fe", icon_stroke="#93c5fd")
    body += arrow(158, 218, 158, 260, "enroll", "#ea580c", "orange")
    body += arrow(158, 338, 158, 380, "保存", "#ea580c", "orange")
    body += arrow(262, 420, 338, 180, "配置", "#ea580c", "orange")
    body += arrow(442, 218, 442, 260, "定时", "#ea580c", "orange")
    body += arrow(442, 338, 442, 380, "打包", "#2563eb", "blue")
    body += arrow(546, 420, 622, 180, "申请上传", "#2563eb", "blue")
    body += arrow(830, 180, 978, 160, "PUT 包", "#16a34a", "green", "5,3")
    body += arrow(978, 238, 726, 260, "object_key", "#2563eb", "blue")
    body += arrow(726, 338, 726, 380, "状态回写", "#16a34a", "green", "5,3")
    body += arrow(830, 300, 906, 339, "创建任务", "#ea580c", "orange")
    body += arrow(830, 300, 978, 459, "写元数据", "#16a34a", "green", "5,3")
    body += legend(54, 622)
    return svg(1160, 720, "Reporter 接入与上报业务流程", "员工或数字员工通过自助 enroll 拿 Reporter Credential；运行时只拿 dkr_report_* 和一次性上传 URL，不接触 MinIO 密钥。", body)


def iam_portal_flow() -> str:
    body = ""
    body += lane(28, 96, 246, 594, "LOGIN ENTRY", "#eff6ff", "#bfdbfe")
    body += lane(298, 96, 272, 594, "AUTH / IAM", "#f8fafc", "#d1d5db")
    body += lane(594, 96, 246, 594, "PORTAL ROUTING", "#f0fdfa", "#99f6e4")
    body += lane(864, 96, 246, 594, "BUSINESS SCOPE", "#fff7ed", "#fed7aa")
    body += node(54, 148, 194, 78, "统一登录页", ["用户名/密码或 SSO", "同一入口自动分流"], icon="IN")
    body += node(54, 290, 194, 78, "普通员工", ["只进入个人工作台", "看自己的资产与上报状态"], icon="EMP", icon_fill="#f0fdfa", icon_stroke="#99f6e4", icon_text="#0f766e")
    body += node(54, 432, 194, 78, "管理员", ["进入管理控制台", "运行时、资产、组件治理"], icon="ADM", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += node(326, 142, 216, 78, "认证校验", ["access token", "会话过期与刷新"], icon="AUTH")
    body += node(326, 278, 216, 78, "权限判断", ["system_role + permissions", "最小权限菜单"], icon="RBAC")
    body += node(326, 414, 216, 78, "用户维度状态", ["岗位/离职状态", "交接功能开关"], icon="USR")
    body += node(622, 152, 190, 78, "Admin Console", ["管理页按权限开放", "组件页仅 admin"], icon="AC")
    body += node(622, 300, 190, 78, "Employee Portal", ["我的 AI 资产", "接入 SOP、反馈确认"], icon="EP")
    body += node(622, 448, 190, 78, "隐藏交接入口", ["active 用户默认不显示", "离职状态才暴露"], icon="HD")
    body += node(890, 136, 194, 82, "管理域", ["Runtime / 资产 / 审计", "Components 仅 admin"], icon="MNG")
    body += node(890, 286, 194, 82, "个人域", ["归属资产、工作历程", "Reporter 健康度"], icon="OWN", icon_fill="#f0fdfa", icon_stroke="#99f6e4", icon_text="#0f766e")
    body += node(890, 436, 194, 82, "交接闭环域", ["审批/执行/回执/验收", "不扩展成 HR/OA"], icon="HO", icon_fill="#fff7ed", icon_stroke="#fed7aa", icon_text="#ea580c")
    body += arrow(248, 187, 326, 181, "登录", "#2563eb", "blue")
    body += arrow(434, 220, 434, 278, "解析", "#2563eb", "blue")
    body += arrow(434, 356, 434, 414, "读取状态", "#16a34a", "green", "5,3")
    body += arrow(542, 317, 622, 191, "admin", "#ea580c", "orange")
    body += arrow(542, 317, 622, 339, "user", "#2563eb", "blue")
    body += arrow(542, 453, 622, 487, "leave flag", "#ea580c", "orange")
    body += arrow(812, 191, 890, 177, "可操作", "#2563eb", "blue")
    body += arrow(812, 339, 890, 327, "可确认", "#2563eb", "blue")
    body += arrow(812, 487, 890, 477, "可交接", "#ea580c", "orange")
    body += path_arrow("M248,329 C280,329 296,317 326,317", 288, 306, "员工", "#2563eb", "blue")
    body += path_arrow("M248,471 C280,471 296,317 326,317", 288, 402, "管理员", "#ea580c", "orange")
    body += legend(54, 632)
    return svg(1140, 720, "身份入口与权限分流流程", "同一登录入口按角色和用户状态自动分流；普通用户不进入管理后台，交接能力只在后台标记离职后对本人显示。", body)


def analysis_flow() -> str:
    body = ""
    body += lane(28, 96, 230, 600, "QUEUE", "#f8fafc", "#d1d5db")
    body += lane(282, 96, 356, 600, "ANALYSIS WORKER", "#faf5ff", "#e9d5ff")
    body += lane(662, 96, 230, 600, "ARTIFACTS", "#f0fdf4", "#bbf7d0")
    body += lane(916, 96, 220, 600, "CONTROL PLANE", "#eff6ff", "#bfdbfe")
    body += node(54, 150, 178, 78, "Pending Job", ["report pack 已上传", "等待 lease"], icon="JOB")
    body += node(54, 300, 178, 78, "Lease / Heartbeat", ["worker token", "租约、重试、幂等"], icon="LSE")
    body += cylinder(766, 150, 120, 76, "MinIO", "duckdock-pack", "#dcfce7", "#16a34a")
    body += node(314, 142, 292, 76, "下载与解包", ["manifest/runtime/inventory/summaries"], icon="ZIP")
    body += node(314, 254, 292, 76, "Baseline 解析", ["确定性抽取资产、摘要、信号"], icon="BAS", icon_fill="#eff6ff")
    body += node(314, 366, 292, 86, "可选 Qwen LLM", ["auto/llm 模式调用 qwen3.7-max", "默认 baseline 也可跑通"], icon="LLM", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += node(314, 496, 292, 82, "规范化与合并", ["schema normalize", "LLM/baseline 去重"], icon="MRG")
    body += node(682, 294, 188, 96, "5 个结果文件", ["analysis-result", "asset-cards", "worktrace-summary", "memory-candidates", "handover-signals"], icon="JSON", icon_fill="#f0fdf4", icon_stroke="#86efac", icon_text="#16a34a")
    body += cylinder(766, 480, 120, 76, "MinIO", "analysis result", "#dcfce7", "#16a34a")
    body += node(944, 176, 164, 78, "Finalize Job", ["校验 sha256/size", "状态 succeeded"], icon="FIN")
    body += node(944, 326, 164, 78, "Materializer", ["读取结果制品", "精简字段入库"], icon="MAT")
    body += cylinder(1026, 508, 116, 76, "MySQL", "资产/记忆/历程", "#e0f2fe", "#4479A1")
    body += arrow(232, 190, 314, 180, "lease", "#ea580c", "orange")
    body += arrow(232, 339, 314, 180, "download URL", "#2563eb", "blue")
    body += arrow(766, 226, 606, 180, "pack", "#2563eb", "blue")
    body += arrow(460, 218, 460, 254, "解析", "#2563eb", "blue")
    body += arrow(460, 330, 460, 366, "prompt", "#9333ea", "purple")
    body += arrow(460, 452, 460, 496, "JSON", "#9333ea", "purple")
    body += arrow(606, 537, 682, 342, "写结果", "#16a34a", "green", "5,3")
    body += arrow(776, 390, 776, 480, "PUT", "#16a34a", "green", "5,3")
    body += arrow(870, 342, 944, 215, "artifact refs", "#2563eb", "blue")
    body += arrow(1026, 254, 1026, 326, "触发", "#ea580c", "orange")
    body += arrow(1026, 404, 1026, 508, "入库", "#16a34a", "green", "5,3")
    body += legend(54, 638)
    return svg(1160, 730, "Analysis Worker 与入库流程", "Worker 不扫描员工电脑，不拿 MinIO 密钥；默认可用 baseline，配置 auto/llm 后可调用 qwen3.7-max 生成更高质量结果。", body)


def workspace_handover_flow() -> str:
    body = ""
    body += lane(28, 96, 254, 600, "EMPLOYEE PORTAL", "#f0fdfa", "#99f6e4")
    body += lane(306, 96, 276, 600, "CONTROL PLANE", "#f8fafc", "#d1d5db")
    body += lane(606, 96, 254, 600, "MANAGER VIEW", "#eff6ff", "#bfdbfe")
    body += lane(884, 96, 230, 600, "LIGHT HANDOVER", "#fff7ed", "#fed7aa")
    body += node(54, 140, 202, 78, "我的 AI 资产", ["Skills / Agents / Prompts", "待确认、已确认、排除"], icon="ME")
    body += node(54, 266, 202, 78, "最近工作历程", ["会话摘要、项目上下文", "不展示原始私聊"], icon="TRC", icon_fill="#f0fdfa", icon_stroke="#99f6e4", icon_text="#0f766e")
    body += node(54, 392, 202, 78, "Reporter 状态", ["健康度、最近上报", "dry-run 引导"], icon="REP")
    body += node(332, 158, 224, 78, "资产归属", ["AssetOwnership", "owner_hint -> 用户"], icon="OWN")
    body += node(332, 284, 224, 78, "长期记忆候选", ["project_context", "handover/risk signal"], icon="MEM", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += node(332, 410, 224, 78, "员工反馈", ["确认、补充、排除", "记录到 metadata"], icon="FB")
    body += node(632, 146, 202, 78, "管理员资产视图", ["风险资产、缺口", "运行时覆盖"], icon="ADM")
    body += node(632, 272, 202, 78, "岗位状态", ["active 默认隐藏交接", "leave/offboarded 才显示"], icon="JOB")
    body += node(632, 398, 202, 78, "交接队列", ["按用户/岗位交接闭环", "不纳管复杂组织流程"], icon="QUE")
    body += node(910, 176, 178, 78, "创建交接单", ["subject_user", "receiver/due date"], icon="HC")
    body += node(910, 316, 178, 78, "分析交接项", ["资产、风险、证据", "建议人工确认"], icon="ANL")
    body += node(910, 456, 178, 78, "执行/关闭", ["审批、转移、禁用", "保留审计"], icon="END")
    body += arrow(256, 180, 332, 197, "确认归属", "#2563eb", "blue")
    body += arrow(256, 305, 332, 323, "沉淀上下文", "#2563eb", "blue")
    body += arrow(256, 431, 332, 449, "健康信号", "#2563eb", "blue")
    body += arrow(444, 236, 444, 284, "候选", "#16a34a", "green", "5,3")
    body += arrow(444, 362, 444, 410, "反馈", "#16a34a", "green", "5,3")
    body += arrow(556, 197, 632, 185, "治理视图", "#2563eb", "blue")
    body += arrow(734, 224, 734, 272, "标记状态", "#ea580c", "orange")
    body += arrow(734, 350, 734, 398, "生成队列", "#ea580c", "orange")
    body += arrow(834, 437, 910, 215, "创建", "#ea580c", "orange")
    body += arrow(999, 254, 999, 316, "分析", "#9333ea", "purple")
    body += arrow(999, 394, 999, 456, "审批执行", "#16a34a", "green", "5,3")
    body += legend(54, 638)
    return svg(1140, 730, "个人工作台与交接闭环流程", "平台专注 AI 资产和用户维度交接；组织管理只保留岗位状态和交接单，不把 DuckDock 变成大型 HR/OA 系统。", body)


def skill_registry_flow() -> str:
    body = ""
    body += lane(28, 96, 220, 600, "DEVELOPER", "#eff6ff", "#bfdbfe")
    body += lane(272, 96, 292, 600, "REGISTRY GOVERNANCE", "#f8fafc", "#d1d5db")
    body += lane(588, 96, 270, 600, "QUALITY GATES", "#fff7ed", "#fed7aa")
    body += lane(882, 96, 226, 600, "DISTRIBUTION", "#f0fdf4", "#bbf7d0")
    body += node(54, 150, 168, 78, "命名空间", ["团队/项目隔离", "RBAC 成员"], icon="NS")
    body += node(54, 282, 168, 78, "提交 Skill", ["SKILL.md / files", "版本说明"], icon="DEV")
    body += node(54, 414, 168, 78, "发布请求", ["tag / semver", "artifact build"], icon="PUB")
    body += node(300, 136, 236, 78, "版本事务", ["advisory lock", "Git commit + tag"], icon="GIT")
    body += node(300, 260, 236, 78, "扫描与策略", ["静态扫描、敏感信息", "治理规则"], icon="SCN", icon_fill="#fee2e2", icon_stroke="#fecaca", icon_text="#dc2626")
    body += node(300, 384, 236, 78, "Artifact 打包", ["tar.gz/manifest", "bytes safe path"], icon="PKG")
    body += node(616, 150, 214, 78, "Sandbox 验证", ["OpenClaw/容器/SSH", "运行 smoke test"], icon="SBX", icon_fill="#fff7ed", icon_stroke="#fed7aa", icon_text="#ea580c")
    body += node(616, 282, 214, 78, "Clinic 质量诊所", ["LLM judge 维度评测", "可接 Langfuse trace"], icon="QA", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += node(616, 414, 214, 78, "发布门禁", ["通过/阻断/人工复核", "审计日志"], icon="GATE")
    body += cylinder(982, 150, 112, 72, "Git", "版本源", "#fef3c7", "#d97706")
    body += cylinder(982, 286, 112, 72, "MinIO", "包分发", "#dcfce7", "#16a34a")
    body += node(912, 420, 168, 82, "私有 Registry", ["skill.md", "signed URL"], icon="REG")
    body += node(912, 552, 168, 78, "ClawHub / Sync", ["公开或私有分发", "OpenClaw 拉取"], icon="HUB")
    body += arrow(138, 228, 138, 282, "开发", "#2563eb", "blue")
    body += arrow(138, 360, 138, 414, "提交", "#2563eb", "blue")
    body += arrow(222, 453, 300, 175, "发布", "#ea580c", "orange")
    body += arrow(418, 214, 418, 260, "扫描", "#ea580c", "orange")
    body += arrow(418, 338, 418, 384, "打包", "#2563eb", "blue")
    body += arrow(536, 299, 616, 189, "验证", "#9333ea", "purple")
    body += arrow(724, 228, 724, 282, "评测", "#9333ea", "purple")
    body += arrow(724, 360, 724, 414, "门禁", "#ea580c", "orange")
    body += arrow(830, 453, 982, 186, "tag", "#16a34a", "green", "5,3")
    body += arrow(830, 453, 982, 322, "artifact", "#16a34a", "green", "5,3")
    body += arrow(982, 358, 996, 420, "索引", "#2563eb", "blue")
    body += arrow(996, 502, 996, 552, "同步", "#2563eb", "blue")
    body += legend(54, 638)
    return svg(1140, 730, "私有 Skill 仓库与发布治理流程", "Skill 仓库仍是 DuckDock 的底座能力：版本、扫描、沙箱验证、质量诊所和私有/公开分发。", body)


def component_flow() -> str:
    body = ""
    body += lane(28, 96, 280, 584, "CORE ALWAYS ON", "#eff6ff", "#bfdbfe")
    body += lane(332, 96, 300, 584, "COMPONENT MANAGER", "#f8fafc", "#d1d5db")
    body += lane(656, 96, 456, 584, "OPTIONAL OBSERVABILITY PROFILE", "#faf5ff", "#e9d5ff")
    body += node(58, 142, 220, 78, "DuckDock Core", ["frontend / backend", "worker / MySQL / Redis / MinIO"], icon="DD")
    body += node(58, 278, 220, 78, "主业务链路", ["Reporter 上传", "资产入库与交接"], icon="BUS")
    body += node(58, 414, 220, 78, "核心不依赖 Langfuse", ["停用观测不影响主链路", "单机 Docker Compose"], icon="OK", icon_fill="#f0fdf4", icon_stroke="#bbf7d0", icon_text="#16a34a")
    body += node(362, 158, 240, 78, "组件管理页", ["/components", "查看 profile 状态"], icon="UI")
    body += node(362, 296, 240, 78, "Docker Compose 控制", ["start/stop observability", "超时与日志"], icon="DC", icon_fill="#eff6ff")
    body += node(362, 434, 240, 78, "组件说明", ["Postgres 不等于主库", "ClickHouse/Langfuse 解耦"], icon="DOC")
    body += node(690, 132, 178, 78, "Langfuse Web", ["LLM trace UI", "Prompt/评估回放"], icon="LF", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += node(902, 132, 178, 78, "Langfuse Worker", ["事件/队列处理", "后台写入"], icon="LFW", icon_fill="#ede9fe", icon_stroke="#c4b5fd", icon_text="#7c3aed")
    body += cylinder(778, 300, 112, 72, "Postgres", "Langfuse 专用", "#e0f2fe", "#336791")
    body += cylinder(994, 300, 112, 72, "ClickHouse", "Trace 分析", "#fef3c7", "#d97706")
    body += cylinder(778, 500, 112, 72, "MinIO", "Langfuse bucket", "#dcfce7", "#16a34a")
    body += cylinder(994, 500, 112, 72, "Redis DB1", "Langfuse queue", "#fee2e2", "#dc2626")
    body += arrow(278, 181, 362, 197, "管理入口", "#2563eb", "blue")
    body += arrow(482, 236, 482, 296, "提交命令", "#ea580c", "orange")
    body += arrow(602, 335, 690, 171, "启动", "#6b7280", "gray", "4,2")
    body += arrow(602, 335, 902, 171, "启动", "#6b7280", "gray", "4,2")
    body += arrow(779, 210, 779, 300, "DB", "#16a34a", "green", "5,3")
    body += arrow(991, 210, 991, 300, "events", "#16a34a", "green", "5,3")
    body += path_arrow("M902,210 C880,280 820,430 778,500", 832, 378, "media/S3", "#16a34a", "green", "5,3")
    body += arrow(994, 372, 994, 500, "queue", "#6b7280", "gray", "4,2")
    body += arrow(278, 453, 362, 473, "说明", "#2563eb", "blue")
    body += legend(58, 622)
    return svg(1140, 710, "组件管理与观测解耦流程", "DuckDock 核心服务默认启动；Langfuse 作为可选观测组件由管理员在控制台按需启停，不阻塞 Reporter、资产入库和交接主链路。", body)


def write_all() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    diagrams = {
        "duckdock-overall-architecture.svg": overall(),
        "duckdock-iam-portal-routing-flow.svg": iam_portal_flow(),
        "duckdock-reporter-upload-flow.svg": reporter_flow(),
        "duckdock-analysis-llm-worker-flow.svg": analysis_flow(),
        "duckdock-asset-handover-workspace-flow.svg": workspace_handover_flow(),
        "duckdock-skill-registry-governance-flow.svg": skill_registry_flow(),
        "duckdock-component-observability-flow.svg": component_flow(),
    }
    for name, content in diagrams.items():
        (OUT_DIR / name).write_text(content, encoding="utf-8")

    readme = """# DuckDock 业务架构图集

使用 `fireworks-tech-graph` Flat Icon 风格绘制，覆盖当前项目的总体架构和主要功能流程。每张图都有 SVG 源文件，并可导出同名 PNG。

| 图 | 说明 |
|---|---|
| `duckdock-business-function-architecture.svg` | 业务功能架构：使用入口、运行时接入、Reporter 上报、分析归档、资产确认、交接闭环、私有 Skill 仓库、质量治理和组件管理 |
| `duckdock-overall-architecture.svg` | 总体业务架构：运行时、控制平面、存储、异步分析和可选组件 |
| `duckdock-iam-portal-routing-flow.svg` | 身份入口与权限分流：统一登录、管理员后台、员工 Portal、离职交接开关 |
| `duckdock-reporter-upload-flow.svg` | Reporter 接入与上报：私有 Registry、Reporter Credential、MinIO 直传、analysis job |
| `duckdock-analysis-llm-worker-flow.svg` | Analysis Worker：MySQL lease、下载包、baseline/LLM 分析、结果回写、materialize |
| `duckdock-asset-handover-workspace-flow.svg` | 个人工作台与交接闭环：员工确认、岗位状态、审批/执行/回执/验收 |
| `duckdock-skill-registry-governance-flow.svg` | 私有 Skill 仓库：版本、扫描、Sandbox、Clinic、分发 |
| `duckdock-component-observability-flow.svg` | 组件管理：核心服务与 Langfuse 可选观测解耦 |

## 当前验证边界

- SVG 是权威源；PNG 阅读版已按当前 SVG 重新导出。
- P3-11 seeded 闭环由 CI 阻塞 job 启动完整 dev 栈运行。
- 生产环境 backup→restore 和 WorkBuddy 周期 Reporter 仍需部署方在目标环境验收。
"""
    (OUT_DIR / "README.zh-CN.md").write_text(readme, encoding="utf-8")


if __name__ == "__main__":
    write_all()
