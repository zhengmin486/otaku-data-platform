"""`sync/health.py` 运维自检的测试。

自带一个可控桩服务（能指定返回状态码），不依赖 conftest 的 stub ——
后者不一定处理 `/api/works` 这类后端公开端点，用它测状态码分支不可靠。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sync import health
from sync.__main__ import main


class _Handler(BaseHTTPRequestHandler):
    """返回类变量 status 指定的状态码；改这个类属性即可切换分支。"""

    status = 200

    def do_GET(self) -> None:
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:  # 静音，避免污染测试输出
        return


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _Handler.status = 200
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _url(httpd: ThreadingHTTPServer) -> str:
    host, port = httpd.server_address[0], httpd.server_address[1]
    return f"http://{host}:{port}"


# ---------- check_endpoint ----------


def test_endpoint_ok_on_200(server):
    r = health.check_endpoint("测试", f"{_url(server)}/x", timeout=5)
    assert r.ok
    assert "200" in r.detail


def test_endpoint_fail_on_404_but_shows_status(server):
    # 404 = 服务在跑但端点不对：判失败，但要把状态码打出来便于分辨
    _Handler.status = 404
    r = health.check_endpoint("测试", f"{_url(server)}/x", timeout=5)
    assert not r.ok
    assert "404" in r.detail


def test_endpoint_hints_proxy_on_502(server):
    # 502 常常不是后端挂了，而是本机全局代理把 localhost 请求也拦了
    _Handler.status = 502
    r = health.check_endpoint("测试", f"{_url(server)}/x", timeout=5)
    assert not r.ok
    assert "NO_PROXY" in r.detail


def test_endpoint_fail_when_unreachable():
    # 端口 1 基本不会有服务在听：连不上与「连上但 404」是两种失败，提示不同
    r = health.check_endpoint("测试", "http://127.0.0.1:1/x", timeout=2)
    assert not r.ok
    assert "不可达" in r.detail


# ---------- check_token ----------


def test_token_ok_when_set():
    r = health.check_token("a" * 43)
    assert r.ok
    assert "已配置" in r.detail


def test_token_fail_when_missing():
    r = health.check_token(None)
    assert not r.ok
    assert "OTAKU_INTERNAL_TOKEN" in r.detail


def test_empty_token_counts_as_missing():
    # 空串和 None 一样等于没配
    assert not health.check_token("").ok


# ---------- run_health ----------


def test_run_health_returns_three_checks(server):
    url = _url(server)
    results = health.run_health(url, url, internal_token="t")
    assert len(results) == 3
    assert [r.name for r in results] == ["来源站", "Java 后端", "内部令牌"]


def test_run_health_all_ok(server):
    url = _url(server)
    results = health.run_health(url, url, internal_token="t")
    assert all(r.ok for r in results)


def test_run_health_reports_backend_down(server):
    url = _url(server)
    # 后端指向一个不可能通的地址
    results = health.run_health(url, "http://127.0.0.1:1", internal_token="t")
    by_name = {r.name: r for r in results}
    assert by_name["来源站"].ok
    assert not by_name["Java 后端"].ok


# ---------- format_results ----------


def test_format_results_marks_pass_and_fail():
    out = health.format_results(
        [
            health.CheckResult("甲", True, "200"),
            health.CheckResult("乙", False, "不可达"),
        ]
    )
    assert "✓" in out and "✗" in out
    assert "甲" in out and "乙" in out


# ---------- CLI: --health ----------


def test_cli_health_exit_zero_when_all_ok(server, monkeypatch):
    monkeypatch.setenv("OTAKU_INTERNAL_TOKEN", "x" * 43)
    url = _url(server)
    assert main(["--health", "--source-base-url", url, "--java-base-url", url]) == 0


def test_cli_health_exit_one_when_token_missing(server, monkeypatch):
    monkeypatch.delenv("OTAKU_INTERNAL_TOKEN", raising=False)
    url = _url(server)
    assert main(["--health", "--source-base-url", url, "--java-base-url", url]) == 1


def test_cli_health_exit_one_when_endpoint_bad(server, monkeypatch):
    monkeypatch.setenv("OTAKU_INTERNAL_TOKEN", "x" * 43)
    _Handler.status = 500
    url = _url(server)
    assert main(["--health", "--source-base-url", url, "--java-base-url", url]) == 1
