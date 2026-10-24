"""`scripts/check_run_report.py` 的测试。

脚本是独立运维工具（不在 `src` 下），这里用 importlib 直接按路径加载，
不改动 `pytest.ini` 的 `pythonpath`，避免影响其他测试。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_run_report", SCRIPTS_DIR / "check_run_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_run_report = _load_module()


def _write_report(tmp_path: Path, name: str, payload: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ---------- evaluate：判定逻辑 ----------


def test_clean_report_has_no_problems():
    assert check_run_report.evaluate({"fetched": 1000, "accepted": 1000, "rejected": 0}) == []


def test_failed_pages_means_not_full():
    problems = check_run_report.evaluate({"fetched": 100, "failed_pages": [{"page": 3}]})
    assert len(problems) == 1
    assert "failed_pages=1" in problems[0]
    assert "未拿到全量" in problems[0]


def test_server_error_items_reported():
    problems = check_run_report.evaluate({"fetched": 100, "server_error_items": [{"id": "1"}]})
    assert len(problems) == 1
    assert "server_error_items=1" in problems[0]


def test_detail_failures_reported():
    problems = check_run_report.evaluate({"fetched": 100, "detail_failures": [{"id": "a1"}]})
    assert len(problems) == 1
    assert "detail_failures=1" in problems[0]


def test_rejected_ratio_above_threshold_reported():
    # 100 条里 10 条被拒 = 10% > 默认 5%
    problems = check_run_report.evaluate({"fetched": 100, "rejected": 10})
    assert len(problems) == 1
    assert "rejected 占比 10/100" in problems[0]


def test_rejected_ratio_below_threshold_ignored():
    assert check_run_report.evaluate({"fetched": 100, "rejected": 3}) == []


def test_custom_threshold_respected():
    # 同样是 10%，阈值放宽到 0.2 后不再告警
    assert check_run_report.evaluate({"fetched": 100, "rejected": 10}, 0.2) == []


def test_zero_fetched_does_not_divide_by_zero():
    # fetched=0 时不做占比判定（既不除零，也不误报成字段变化）
    assert check_run_report.evaluate({"fetched": 0, "rejected": 5}) == []


def test_multiple_problems_all_reported():
    problems = check_run_report.evaluate(
        {
            "fetched": 100,
            "failed_pages": [{"page": 1}, {"page": 2}],
            "server_error_items": [{"id": "1"}],
            "detail_failures": [{"id": "a1"}],
            "rejected": 50,
        }
    )
    assert len(problems) == 4


def test_missing_fields_treated_as_empty():
    # RunReport 老版本可能没有 detail_failures 等字段，不能因此报错
    assert check_run_report.evaluate({}) == []


# ---------- find_latest_report ----------


def test_find_latest_report_empty_dir(tmp_path: Path):
    assert check_run_report.find_latest_report(str(tmp_path)) is None


def test_find_latest_report_picks_newest(tmp_path: Path):
    _write_report(tmp_path, "run-20260101-000000.json", {"fetched": 1})
    _write_report(tmp_path, "run-20260102-000000.json", {"fetched": 2})
    latest = check_run_report.find_latest_report(str(tmp_path))
    assert latest is not None
    assert latest.endswith("run-20260102-000000.json")


def test_find_latest_report_ignores_other_files(tmp_path: Path):
    _write_report(tmp_path, "batch-001.json", {"fetched": 1})
    assert check_run_report.find_latest_report(str(tmp_path)) is None


# ---------- main：退出码契约 ----------


def test_main_returns_zero_when_clean(tmp_path: Path):
    path = _write_report(tmp_path, "run-ok.json", {"fetched": 100, "rejected": 0})
    assert check_run_report.main(["--report", path]) == 0


def test_main_returns_one_when_problems(tmp_path: Path):
    path = _write_report(tmp_path, "run-bad.json", {"fetched": 100, "failed_pages": [{"page": 1}]})
    assert check_run_report.main(["--report", path]) == 1


def test_main_returns_two_when_no_report(tmp_path: Path):
    # 空目录：退出 2（与「有问题」区分开，便于 cron 分别处置）
    assert check_run_report.main(["--runs-dir", str(tmp_path)]) == 2


def test_main_returns_two_on_unparsable_report(tmp_path: Path):
    path = tmp_path / "run-broken.json"
    path.write_text("{ not json", encoding="utf-8")
    assert check_run_report.main(["--report", str(path)]) == 2


def test_main_uses_runs_dir_when_no_report_arg(tmp_path: Path):
    _write_report(tmp_path, "run-20260101-000000.json", {"fetched": 10, "rejected": 0})
    assert check_run_report.main(["--runs-dir", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"fetched": 100, "rejected": 0}, 0),
        ({"fetched": 100, "failed_pages": [{"page": 1}]}, 1),
    ],
)
def test_main_exit_codes(tmp_path: Path, payload: dict, expected: int):
    path = _write_report(tmp_path, "run-x.json", payload)
    assert check_run_report.main(["--report", path]) == expected
