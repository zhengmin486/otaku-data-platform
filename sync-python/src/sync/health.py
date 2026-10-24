"""运维自检：跑 sync 之前先确认链路通不通、令牌在不在。

为什么要有它
    sync 跑一轮要几十分钟（全量约 1.8 万条，串行限流 ≈1.67 req/s）。如果后端没起、
    或 `OTAKU_INTERNAL_TOKEN` 没设，往往要等抓取跑完、提交阶段才发现整轮白跑。
    定时化之后更明显：cron 半夜跑，失败要第二天才看到。
    `--health` 花几秒先确认，避免这种浪费。

判定口径
    两种失败原因不同，都要判失败，但提示分开写，便于一眼分辨：
    - 连不上（DNS / 端口没起 / 超时）= 不可达，多半是地址写错或服务没起；
    - 连上了但状态码不是 2xx/3xx = 端点可能变了（或后端没就绪），
      这种情况服务其实在跑，所以把状态码原样打出来。

    令牌只检查**是否配置**，不发请求验证有效性：验证要真发一次导入，
    那是有副作用的写操作，不该出现在只读自检里。

只读：本模块不发起任何写请求。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass
class CheckResult:
    """一项自检的结果。"""

    name: str
    ok: bool
    detail: str


def check_endpoint(name: str, url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> CheckResult:
    """检查一个 HTTP 端点能否连通。能收到响应即算可达（非 2xx 仅提示）。"""
    started = time.monotonic()
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        # 连不上 / 超时 / DNS 失败 —— 这才是真问题
        return CheckResult(name, False, f"不可达（{type(exc).__name__}）")
    elapsed_ms = (time.monotonic() - started) * 1000
    status = resp.status_code
    ok = 200 <= status < 400
    note = "" if ok else " —— 服务在跑，但这个状态码不正常，检查端点是不是变了"
    if status in (502, 503):
        # 本机设了全局代理时，连 localhost 也会被代理拦掉并返回 502/503，
        # 看起来像后端挂了，其实是代理。这个坑踩过，直接把排查方向写出来。
        note += "（若本机设了 HTTP_PROXY，先试 NO_PROXY=localhost,127.0.0.1）"
    return CheckResult(name, ok, f"{status} · {elapsed_ms:.0f}ms{note}")


def check_token(token: str | None) -> CheckResult:
    """只检查令牌是否配置，不验证有效性（避免有副作用的请求）。"""
    if token:
        return CheckResult("内部令牌", True, f"已配置（{len(token)} 字符，不校验有效性）")
    return CheckResult(
        "内部令牌",
        False,
        "未设置 OTAKU_INTERNAL_TOKEN —— 只能 --dry-run，正式提交会因 401 全批失败",
    )


def run_health(
    source_base_url: str,
    java_base_url: str,
    internal_token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[CheckResult]:
    """执行一次完整自检。顺序：来源站 → 后端 → 令牌。"""
    source = source_base_url.rstrip("/")
    java = java_base_url.rstrip("/")
    return [
        check_endpoint("来源站", f"{source}/api/contents?page=1&page_size=1", timeout),
        check_endpoint("Java 后端", f"{java}/api/works?page=1&page_size=1", timeout),
        check_token(internal_token),
    ]


def format_results(results: list[CheckResult]) -> str:
    """把自检结果排成可读文本（供 CLI 打印）。"""
    lines = []
    for r in results:
        mark = "✓" if r.ok else "✗"
        lines.append(f"[{mark}] {r.name}: {r.detail}")
    return "\n".join(lines)
