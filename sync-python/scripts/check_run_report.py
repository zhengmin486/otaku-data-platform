"""检查最近一次 sync 运行报告（RunReport），有问题则非 0 退出。

为什么需要它：`python -m sync` 在正常路径上一律 `return 0`，**即使 `failed_pages`
非空（本次根本没拿到全量），进程退出码也是 0**。所以 cron / systemd 只看退出码会
全部漏报，必须在跑完之后读 `RunReport` 来决定成败。

用法：
    python scripts/check_run_report.py                  # 检查 runs/ 下最新的报告
    python scripts/check_run_report.py --report x.json  # 指定某份报告
    python scripts/check_run_report.py --threshold 0.1  # 放宽 rejected 占比阈值

退出码：0 = 正常；1 = 发现需要处置的问题；2 = 没找到报告 / 报告不可解析。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Any

DEFAULT_RUNS_DIR = "runs"
DEFAULT_REJECTED_RATIO = 0.05


def find_latest_report(runs_dir: str = DEFAULT_RUNS_DIR) -> str | None:
    """返回目录下最新的 `run-*.json` 路径；没有则返回 None。"""
    files = sorted(glob.glob(os.path.join(runs_dir, "run-*.json")))
    return files[-1] if files else None


def evaluate(
    report: dict[str, Any],
    rejected_ratio: float = DEFAULT_REJECTED_RATIO,
) -> list[str]:
    """判定一份 RunReport 是否需要处置，返回问题描述列表（空列表 = 正常）。

    判定口径见 `docs/operations.md` §2.5。注意 `fetched == 0` 时不做占比判定，
    避免出现除零，也不把「什么都没抓到」误报成字段变化。
    """
    problems: list[str] = []
    fetched = report.get("fetched") or 0

    failed_pages = report.get("failed_pages") or []
    if failed_pages:
        problems.append(
            f"failed_pages={len(failed_pages)} → 本次未拿到全量，"
            "重跑同一命令补齐（按 (source, source_id) 幂等，不重复入库）"
        )

    server_error_items = report.get("server_error_items") or []
    if server_error_items:
        problems.append(
            f"server_error_items={len(server_error_items)} → 服务端故障"
            "（如并发导入 DuplicateKey），重跑同一 batch_id"
        )

    detail_failures = report.get("detail_failures") or []
    if detail_failures:
        problems.append(
            f"detail_failures={len(detail_failures)} → 资讯条目缺正文/原文链接"
            "（其余字段已入库），重跑补抓这几条详情"
        )

    rejected = report.get("rejected") or 0
    if fetched > 0 and rejected / fetched > rejected_ratio:
        problems.append(
            f"rejected 占比 {rejected}/{fetched} = {rejected / fetched:.1%} "
            f"> {rejected_ratio:.0%} → 可能来源站字段变化，"
            "人工看 rejected_items（重试无益）"
        )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查 sync 运行报告；有问题则非 0 退出，供 cron / systemd 捕获告警"
    )
    parser.add_argument(
        "--runs-dir",
        default=DEFAULT_RUNS_DIR,
        help=f"运行报告目录（默认 {DEFAULT_RUNS_DIR}）",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="指定报告文件路径（优先于 --runs-dir）",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_REJECTED_RATIO,
        help=f"rejected 占比告警阈值（默认 {DEFAULT_REJECTED_RATIO}）",
    )
    args = parser.parse_args(argv)

    path = args.report or find_latest_report(args.runs_dir)
    if not path:
        print(f"SYNC_ALERT: 未找到运行报告（目录：{args.runs_dir}）")
        return 2

    try:
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"SYNC_ALERT: 报告无法读取或解析 {path}: {exc}")
        return 2

    problems = evaluate(report, args.threshold)
    if problems:
        print(f"SYNC_ALERT {path}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
