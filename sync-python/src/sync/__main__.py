"""命令行入口。

    python -m sync --type anime --limit 100 --dry-run
    python -m sync --source bangumi --archive <dump.zip> --stats
    python -m sync --source bangumi --archive <dump.zip> --type anime --limit 100 --dry-run
    python -m sync --source bangumi --archive <dump.zip> --limit 10000 --sample uniform --dry-run
    python -m sync --schedule                 # 每日新番档期（otaku_site）
    python -m sync --schedule --dry-run       # 只落盘载荷不提交
    python -m sync --articles --limit 50      # 最新资讯（otaku_site，含详情抓取）
    python -m sync --articles --no-detail     # 只抓列表页（快，但没有正文与原文链接）
    python -m sync --health                   # 运维自检：来源站 / 后端 / 令牌（不跑同步）

Java 后端就绪后再去掉 --dry-run 并配置 OTAKU_INTERNAL_TOKEN。
"""

from __future__ import annotations

import argparse
import sys

from .bangumi import BangumiArchiveSource, WORK_TYPE_LABELS
from .config import VALID_SOURCES, SyncConfig
from .health import format_results, run_health
from .pipeline import run, run_articles, run_schedule, summarize
from .models import VALID_WORK_TYPES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync", description="把来源作品数据整理后提交给 Java 后端"
    )
    parser.add_argument(
        "--type",
        dest="work_types",
        action="append",
        choices=VALID_WORK_TYPES,
        help="作品类型，可重复传入；默认 anime",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="只处理前 N 条，用于小批量联调"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不联系 Java，只落盘将要发送的载荷与运行报告",
    )
    parser.add_argument("--batch-size", type=int, default=None, help="每批条数，上限 500")
    parser.add_argument("--out", default=None, help="输出目录，默认 runs")
    parser.add_argument(
        "--source",
        choices=VALID_SOURCES,
        default=None,
        help="来源：otaku_site（在线站点）或 bangumi（本地档案），默认读环境变量",
    )
    parser.add_argument(
        "--archive", default=None, help="Bangumi 档案路径（dump.zip），仅 --source bangumi 用"
    )
    parser.add_argument(
        "--sample",
        choices=["head", "uniform"],
        default=None,
        help="抽样方式：head=按档案顺序取前 N 条（默认）；uniform=蓄水池随机抽样，"
        "消除按 id 顺序的偏差，使样本分布接近全量真实比例。不传时读 OTAKU_SAMPLE_METHOD",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="只统计档案里各类型条数，不生成载荷",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="同步每日新番档期（来源站 /api/schedule），与 --type 互斥",
    )
    parser.add_argument(
        "--articles",
        action="store_true",
        help="同步最新资讯（来源站 /articles 与 /articles/{id}），与 --type 互斥",
    )
    parser.add_argument(
        "--no-detail",
        dest="with_detail",
        action="store_false",
        help="资讯只抓列表页：快很多，但入库的条目没有正文与原文链接",
    )
    parser.add_argument(
        "--article-limit",
        type=int,
        default=None,
        help="资讯本次最多处理多少条（列表+详情都算）；默认不限制",
    )
    parser.add_argument(
        "--source-base-url", default=None, help="覆盖来源站点地址"
    )
    parser.add_argument("--java-base-url", default=None, help="覆盖 Java 后端地址")
    parser.add_argument(
        "--health",
        action="store_true",
        help="运维自检：检查来源站与 Java 后端是否可达、内部令牌是否已配置（不跑同步）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (args.batch_size or 0) > 500:
        print("错误：单批上限 500 条", file=sys.stderr)
        return 2

    config = SyncConfig.from_env(
        batch_size=args.batch_size,
        source=args.source,
        archive_path=args.archive,
        sample_method=args.sample,
        source_base_url=args.source_base_url,
        java_base_url=args.java_base_url,
    )

    # 环境变量也能改来源/抽样方式，这里统一兜底校验，避免拼错时静默退化。
    if config.source not in VALID_SOURCES:
        print(
            f"错误：未知来源 {config.source!r}，可选 {', '.join(VALID_SOURCES)}",
            file=sys.stderr,
        )
        return 2
    if config.sample_method not in ("head", "uniform"):
        print(
            f"错误：未知抽样方式 {config.sample_method!r}，可选 head、uniform",
            file=sys.stderr,
        )
        return 2
    if config.source == "bangumi" and not config.archive_path:
        print(
            "错误：--source bangumi 需要 --archive <dump.zip> 或 OTAKU_ARCHIVE_PATH",
            file=sys.stderr,
        )
        return 2

    if args.health:
        results = run_health(
            source_base_url=config.source_base_url,
            java_base_url=config.java_base_url,
            internal_token=config.internal_token,
        )
        print(format_results(results))
        if not all(r.ok for r in results):
            print("\n自检未通过：先修好上面标 ✗ 的项再跑同步。", file=sys.stderr)
            return 1
        return 0

    if args.articles:
        if args.work_types:
            print("错误：--articles 与 --type 互斥（资讯没有类型维度）", file=sys.stderr)
            return 2
        if config.source != "otaku_site":
            print(
                "错误：--articles 只支持来源站 otaku_site（资讯是站点自己的内容）",
                file=sys.stderr,
            )
            return 2
        report = run_articles(
            config,
            dry_run=args.dry_run,
            output_dir=args.out,
            limit=args.article_limit,
            with_detail=args.with_detail,
        )
        print(summarize(report))
        for item in report.rejected_items[:20]:
            print(f"  拒绝 {item.source_id}: {item.reason_code} - {item.message}")
        # 详情缺失不是拒绝：条目已入库，只是读起来单薄。单独提示，
        # 运维才知道要补抓详情而不是重跑整批。
        for item in report.detail_failures[:20]:
            print(f"  详情缺失 {item.source_id}: {item.message}")
        for batch in report.batches:
            if not batch.get("delivered") and not report.dry_run:
                print(f"  批次 {batch['batch_id']} 未送达：{batch.get('error')}")
        return 0

    if args.schedule:
        if args.work_types:
            print("错误：--schedule 与 --type 互斥（档期没有类型维度）", file=sys.stderr)
            return 2
        if config.source != "otaku_site":
            print(
                "错误：--schedule 只支持来源站 otaku_site（档期是站点自己的排播数据）",
                file=sys.stderr,
            )
            return 2
        report = run_schedule(config, dry_run=args.dry_run, output_dir=args.out)
        print(summarize(report))
        for item in report.rejected_items[:20]:
            print(f"  拒绝 {item.source_id}: {item.reason_code} - {item.message}")
        for batch in report.batches:
            if not batch.get("delivered") and not report.dry_run:
                print(f"  批次 {batch['batch_id']} 未送达：{batch.get('error')}")
        return 0

    if args.stats:
        if config.source != "bangumi":
            print("错误：--stats 只支持 --source bangumi", file=sys.stderr)
            return 2
        counts = BangumiArchiveSource(config.archive_path or "").count_by_work_type()
        print("档案类型分布：")
        for name, label in WORK_TYPE_LABELS.items():
            print(f"  {name:6s} {label}  {counts[name]}")
        print(f"  排除（音乐/三次元/其他书籍）  {counts['excluded']}")
        return 0

    # 蓄水池抽样必须知道样本量；缺 --limit 时若静默按 head 顺序取，等于没抽样且样本有偏。
    if config.sample_method == "uniform" and args.limit is None:
        print(
            "错误：--sample uniform 必须配合 --limit N（蓄水池抽样需要明确的样本量）",
            file=sys.stderr,
        )
        return 2

    report = run(
        config,
        work_types=args.work_types,
        limit=args.limit,
        dry_run=args.dry_run,
        output_dir=args.out,
    )
    print(summarize(report))
    for item in report.rejected_items[:20]:
        print(f"  拒绝 {item.source_id}: {item.reason_code} - {item.message}")
    for batch in report.batches:
        if not batch.get("delivered") and not report.dry_run:
            print(f"  批次 {batch['batch_id']} 未送达：{batch.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
