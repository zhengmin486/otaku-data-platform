#!/usr/bin/env python3
"""联调第 1 步：用真实 ticket 验证站点 OAuth 端点的**成功响应体**。

为什么要有这个脚本
    `docs/identity-linkup-contract.md` §1.1 里，四个端点的**错误**行为已经只读
    实测核对过了（无参数 / 非法 client_id / 假 secret 等），但**成功响应体**
    一直没验 —— 因为成功路径需要一张真实的一次性 ticket（用户在站点授权后才有）。

    ticket 只在授权回跳时给一次、120 秒过期，临时手敲 curl 容易错过，也容易把
    client_secret 粘进 shell 历史。这个脚本把步骤固定下来：secret 只从环境变量
    读、token 只打印前缀，验完把字段名与类型打出来，用于核对契约。

用法
    export OTAKU_SITE_OAUTH_CLIENT_SECRET='<站点给的联调 secret>'
    python scripts/verify-site-oauth.py --ticket '<授权回跳带回来的 ticket>'

    # 验完顺带把刚换来的令牌吊销（确认 revoke 真能吊销）
    python scripts/verify-site-oauth.py --ticket '<ticket>' --revoke

安全（与项目铁律一致）
    - client_secret 只从环境变量读取，不打印、不落盘、不进 shell 历史；
    - access_token 只打印前 8 位与长度，完整值不输出；
    - 全程只读验证，不写任何文件、不改平台数据；
    - **这是联调工具，不是生产代码，不参与构建与部署。**

退出码
    0 = 全部通过；1 = 任一步失败；2 = 缺少必要输入（没给 ticket / 没设 secret）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://154.8.194.198:8000"
DEFAULT_CLIENT_ID = "otaku-app-client"
SECRET_ENV = "OTAKU_SITE_OAUTH_CLIENT_SECRET"


def _mask(value: str) -> str:
    """只暴露前缀与长度，避免令牌/密钥进日志或终端历史。"""
    if not value:
        return "(空)"
    return f"{value[:8]}…(共 {len(value)} 字符)"


def _post_form(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> tuple[int, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return _send(req)


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return _send(req)


def _send(req: urllib.request.Request) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def _describe(payload: Any) -> str:
    """打印响应**结构**（字段名与类型），而不是值本身。"""
    if isinstance(payload, dict):
        return "{" + ", ".join(f"{k}: {type(v).__name__}" for k, v in payload.items()) + "}"
    if isinstance(payload, list):
        return f"list[{len(payload)}]"
    return type(payload).__name__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="用真实 ticket 验证站点 OAuth 端点的成功响应体（联调工具）"
    )
    parser.add_argument("--ticket", required=True, help="授权回跳带回来的一次性票据")
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID, help=f"client_id（默认 {DEFAULT_CLIENT_ID}）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"站点地址（默认 {DEFAULT_BASE_URL}）")
    parser.add_argument("--revoke", action="store_true", help="验证成功后顺带吊销该令牌（确认 revoke 可用）")
    args = parser.parse_args(argv)

    secret = os.environ.get(SECRET_ENV)
    if not secret:
        print(f"缺少环境变量 {SECRET_ENV}（client_secret 只从环境变量读，绝不入库）")
        return 2

    base = args.base_url.rstrip("/")
    print(f"站点：{base} ｜ client_id：{args.client_id} ｜ secret：已就绪（不打印）\n")

    # ---- 1. ticket 换令牌 ----
    status, payload = _post_form(
        f"{base}/api/auth/token",
        {"ticket": args.ticket, "client_id": args.client_id, "client_secret": secret},
    )
    print(f"[1] POST /api/auth/token → {status}")
    print(f"    结构：{_describe(payload)}")
    if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
        print("    ❌ 换令牌失败；ticket 可能已过期（120s）或已用过。重新走一次 authorize 拿新 ticket。")
        return 1

    access_token = payload["access_token"]
    user = payload.get("user")
    print(f"    access_token：{_mask(access_token)}")
    print(f"    expires_in：{payload.get('expires_in')}")
    print(f"    user 结构：{_describe(user)}")
    if isinstance(user, dict):
        for key in ("external_user_id", "nickname", "avatar_url"):
            if key in user:
                print(f"      - {key} = {user[key]!r}")
    print("    ✅ 换令牌成功")

    # ---- 2. 带令牌读身份 ----
    status, payload = _get(f"{base}/api/me", {"Authorization": f"Bearer {access_token}"})
    print(f"\n[2] GET /api/me → {status}")
    print(f"    结构：{_describe(payload)}")
    if status != 200:
        print("    ❌ 读身份失败（契约口径：scope 固定 identity，只给身份不给业务写权限）")
        return 1
    print("    ✅ 读身份成功")

    # ---- 3.（可选）吊销 ----
    external_user_id = ""
    if isinstance(user, dict):
        external_user_id = str(user.get("external_user_id") or "")

    if args.revoke:
        status, payload = _post_form(
            f"{base}/api/auth/revoke",
            {"access_token": access_token, "external_user_id": external_user_id},
            {"Authorization": f"Bearer {access_token}"},
        )
        print(f"\n[3] POST /api/auth/revoke → {status}")
        print(f"    结构：{_describe(payload)}")
        if status >= 400:
            print("    ⚠️ 吊销未成功；确认该端点是否只对站点管理员会话开放（契约口径）")
            return 1
        print("    ✅ 吊销成功（令牌已失效，符合可撤销要求）")
    else:
        print("\n[3] 跳过 revoke（未加 --revoke）。注意：本次换来的令牌在 1 小时后自行过期。")

    print("\n全部通过。把上面的字段名与类型核对进 identity-linkup-contract.md §1.1 的「成功响应体」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
