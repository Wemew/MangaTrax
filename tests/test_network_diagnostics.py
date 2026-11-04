#!/usr/bin/env python3
"""task-33 suite: container network diagnostics + bounded Cloudflare speed test.

Run through the honest runner (assertions compiled live):
    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e "tests\\run_tests.py"
"""

import json
import os
import sys
import time
import traceback
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (BACKEND_DIR, TESTS_DIR, BASE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from e2e.harness import E2ETestContext  # installs FastAPI/Jinja shims in Calibre
import app.main as main_module  # noqa: E402
from app import network_diagnostics  # noqa: E402


FAKE_FILES = {
    "/sys/class/net/eth0/mtu": "1500",
    "/sys/class/net/eth0/operstate": "up\n",
    "/sys/class/net/eth0/address": "aa:bb:cc:dd:ee:ff\n",
    "/sys/class/net/eth1/mtu": "9000",
    "/sys/class/net/eth1/operstate": "up",
    "/proc/sys/net/ipv4/tcp_congestion_control": "cubic\n",
    "/proc/sys/net/core/default_qdisc": "fq_codel\n",
    "/proc/sys/net/ipv4/tcp_rmem": "4096\t131072\t6291456\n",
    "/proc/sys/net/ipv4/tcp_wmem": "4096\t16384\t4194304\n",
    "/proc/sys/net/core/rmem_max": "212992\n",
    "/proc/sys/net/core/wmem_max": "212992\n",
    "/sys/fs/cgroup/cpu.max": "50000 100000\n",
    "/sys/fs/cgroup/cpu.stat": "nr_periods 100\nnr_throttled 12\nthrottled_usec 500000\n",
    "/etc/resolv.conf": "nameserver 127.0.0.11\n",
    "/proc/net/snmp": (
        "Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens AttemptFails "
        "EstabResets CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts InCsumErrors\n"
        "Tcp: 1 200 120000 -1 100 100 1 0 5 100000 1000000 10000 0 0 0\n"
    ),
    "/proc/net/netstat": "TcpExt: ListenDrops TCPBacklogDrop\nTcpExt: 3 7\n",
    "/proc/net/route": (
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t0101A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    ),
    "/proc/uptime": "1234.5 999.0\n",
}

FAKE_INTERFACES = [
    {"name": "eth0", "mtu": 1500, "operstate": "up", "mac": "aa", "ipv4": ["192.168.1.2"], "ipv6": []},
    {"name": "eth1", "mtu": 9000, "operstate": "up", "mac": "bb", "ipv4": [], "ipv6": []},
]


def _fake_read(path):
    return FAKE_FILES.get(path)


# ===========================================================================
# 1. Collector: parses facts and emits the classic-gap warnings
# ===========================================================================
def test_collector_parses_and_warns():
    print("--> Testing network diagnostics collector + warnings...")
    with patch("app.network_diagnostics._read_text", side_effect=_fake_read):
        with patch("app.network_diagnostics._exists", side_effect=lambda path: path == "/.dockerenv"):
            snapshot = network_diagnostics.collect_network_diagnostics(
                settings={"torbox_download_concurrency": "4", "torbox_parallel_chunks": "4",
                          "downloader_engine": "torbox"},
                interfaces=FAKE_INTERFACES,
            )
    assert snapshot["checked_at"], snapshot
    assert snapshot["container"] is True, snapshot
    assert snapshot["mtu"] == [1500, 9000], snapshot
    assert snapshot["interfaces"][0]["name"] == "eth0", snapshot
    assert snapshot["sysctls"]["tcp_congestion_control"] == "cubic", snapshot["sysctls"]
    assert snapshot["sysctls"]["default_qdisc"] == "fq_codel", snapshot["sysctls"]
    assert snapshot["cpu"]["quota_cores"] == 0.5, snapshot["cpu"]
    assert snapshot["cpu"]["throttled_count"] == 12, snapshot["cpu"]
    assert snapshot["dns"]["docker_embedded_dns"] is True, snapshot["dns"]
    assert snapshot["tcp"]["retrans_rate_pct"] == 1.0, snapshot["tcp"]
    assert snapshot["route"]["default_gateway"] == "192.168.1.1", snapshot["route"]
    assert snapshot["app"]["torbox_download_concurrency"] == "4", snapshot["app"]
    warnings = " | ".join(snapshot["warnings"])
    assert "MTU mismatch" in warnings, warnings
    assert "cubic" in warnings and "BBR" in warnings, warnings
    assert "CPU quota" in warnings, warnings
    assert "retransmit rate" in warnings, warnings
    assert "Docker embedded DNS" in warnings, warnings
    print("    [PASSED] MTU/sysctls/cpu/dns/retrans/routes parsed with actionable warnings.")


# ===========================================================================
# 2. Missing files: graceful None, never raises
# ===========================================================================
def test_collector_missing_files_resilient():
    print("--> Testing collector resilience to missing files...")
    with patch("app.network_diagnostics._read_text", return_value=None):
        with patch("app.network_diagnostics._exists", return_value=False):
            snapshot = network_diagnostics.collect_network_diagnostics(
                settings={}, interfaces=[],
            )
    assert snapshot["checked_at"], snapshot
    assert snapshot["container"] is False, snapshot
    assert snapshot["interfaces"] == [], snapshot
    assert snapshot["sysctls"]["tcp_congestion_control"] is None, snapshot
    assert snapshot["tcp"]["retrans_rate_pct"] is None, snapshot
    assert any("No network interfaces" in warning for warning in snapshot["warnings"]), snapshot["warnings"]
    print("    [PASSED] missing /proc//sys files yield None/empty and never raise.")


# ===========================================================================
# 3. Speed-test math, caps, 90 MB rounds, short-body 403 handling, allow-list
# ===========================================================================
def _fake_download_factory(short=False):
    def _fake(url, byte_count, timeout):
        if short:
            return 403, 1, 0.05
        return 200, byte_count, 0.25
    return _fake


def test_speedtest_math_and_caps():
    print("--> Testing speed-test math, rounds and caps...")
    with patch("app.network_diagnostics._download_once", side_effect=_fake_download_factory()):
        with patch("app.network_diagnostics.collect_network_diagnostics", return_value={"warnings": []}):
            result = network_diagnostics.run_speedtest(
                bytes_total=360_000_000, connections=4, url=network_diagnostics.SPEEDTEST_URL,
            )
    assert result["status"] == "success", result
    assert result["bytes_requested"] == 360_000_000, result
    assert result["bytes_transferred"] == 360_000_000, result
    assert result["connections"] == 4 and result["rounds_per_connection"] == 1, result
    assert len(result["per_connection"]) == 4, result
    for connection in result["per_connection"]:
        assert connection["bytes"] == 90_000_000, connection
        assert connection["rounds"] == 1, connection
        assert connection["mbps"] == round(90_000_000 / 1_000_000 / connection["seconds"], 2), connection
    assert result["capped"] is True, result

    with patch("app.network_diagnostics._download_once", side_effect=_fake_download_factory()):
        with patch("app.network_diagnostics.collect_network_diagnostics", return_value={"warnings": []}):
            big = network_diagnostics.run_speedtest(
                bytes_total=1_000_000_000, connections=4, url=network_diagnostics.SPEEDTEST_URL,
            )
    assert big["rounds_per_connection"] == 3, big
    assert all(connection["rounds"] == 3 for connection in big["per_connection"]), big
    assert big["bytes_transferred"] == 1_000_000_000, big

    for bad_bytes in (0, network_diagnostics.SPEEDTEST_MAX_BYTES + 1):
        result = network_diagnostics.run_speedtest(bytes_total=bad_bytes, connections=4)
        assert result["status"] == "error" and "bytes" in result["error"], result
    result = network_diagnostics.run_speedtest(bytes_total=1000, connections=9)
    assert result["status"] == "error" and "connections" in result["error"], result
    result = network_diagnostics.run_speedtest(bytes_total=1000, connections=1, url="https://evil.example/x")
    assert result["status"] == "error" and "URL rejected" in result["error"], result
    print("    [PASSED] per-connection/aggregate math, 90 MB rounds, caps and allow-list enforced.")


def test_speedtest_short_body_403():
    print("--> Testing 403/short-body is a structured error, not a fake fast result...")
    with patch("app.network_diagnostics._download_once", side_effect=_fake_download_factory(short=True)):
        result = network_diagnostics.run_speedtest(
            bytes_total=100_000_000, connections=4, url=network_diagnostics.SPEEDTEST_URL,
        )
    assert result["status"] == "error", result
    assert "HTTP 403" in result["error"], result
    assert "90 MB" in result["error"], result
    assert result.get("aggregate_mbps") is None, result
    assert result["bytes_transferred"] < 100_000_000, result
    print("    [PASSED] Cloudflare's 403/short body is reported with the endpoint limit, not as speed.")


# ===========================================================================
# 4. /health network block + diagnostics endpoints + rate limit
# ===========================================================================
def test_health_and_diagnostics_endpoints():
    print("--> Testing /health network block + diagnostics endpoints...")
    ctx = E2ETestContext(prefix="mangatrax_network_api_")
    try:
        marker = {"container": True, "mtu": [1500], "warnings": ["test warning"]}
        with patch("app.main.network_diagnostics.collect_network_diagnostics", return_value=marker):
            health = ctx.client.get("/health").json()
            network = ctx.client.get("/api/diagnostics/network").json()
        assert health["network"]["mtu"] == [1500], health["network"]
        assert network["container"] is True and network["warnings"], network
        fake_result = {
            "status": "success", "aggregate_mbps": 42.0, "bytes_transferred": 90_000_000,
            "bytes_requested": 90_000_000, "connections": 1, "per_connection": [],
            "wall_seconds": 2.1, "capped": False,
        }
        main_module._NETWORK_SPEEDTEST_LAST.clear()
        with patch("app.main.network_diagnostics.run_speedtest", return_value=fake_result):
            first = ctx.client.post("/api/diagnostics/speedtest", json_data={"bytes": 1000, "connections": 1})
            second = ctx.client.post("/api/diagnostics/speedtest", json_data={"bytes": 1000, "connections": 1})
        assert first.status_code == 200 and first.json()["aggregate_mbps"] == 42.0, first.text
        assert second.status_code == 429, second.status_code
        assert "rate" in second.json()["detail"].lower(), second.json()
        print("    [PASSED] /health exposes network; network endpoint and rate-limited speedtest work.")
    finally:
        main_module._NETWORK_SPEEDTEST_LAST.clear()
        ctx.close()


# ===========================================================================
# 5. Settings card + README wiring
# ===========================================================================
def test_template_and_docs_wiring():
    print("--> Testing Settings network card + README guidance wiring...")
    settings = open(os.path.join(BACKEND_DIR, "app", "templates", "settings.html"), encoding="utf-8").read()
    for needle in (
        "network-diagnostics-card", "Network diagnostics (container vs host)",
        "Run speed test", "runNetworkSpeedTest", "copyNetworkDiagnostics",
        "btn-network-speedtest", "network-speedtest-result",
        "Cloudflare caps a single speed-test request at 90 MB",
    ):
        assert needle in settings, f"missing Settings network wiring: {needle}"
    readme = open(os.path.join(BASE_DIR, "README.md"), encoding="utf-8").read()
    for needle in (
        "Network diagnostics: why is the container slower than the host?",
        "network_mode: host", "tcp_congestion_control=bbr", "conntrack",
        "HTTP 403", "90,000,000", "speed.cloudflare.com", "mergerfs",
    ):
        assert needle in readme, f"missing README guidance: {needle}"
    print("    [PASSED] Settings card (snapshot, Run speed test, copy JSON) and README guidance are wired.")


def main():
    print("=" * 78)
    print("Running network diagnostics suite (task-33)")
    print("=" * 78)
    tests = [
        test_collector_parses_and_warns,
        test_collector_missing_files_resilient,
        test_speedtest_math_and_caps,
        test_speedtest_short_body_403,
        test_health_and_diagnostics_endpoints,
        test_template_and_docs_wiring,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception:
            failures += 1
            print(f"  [FAILED] {test.__name__}")
            traceback.print_exc()
    print("=" * 78)
    if failures:
        print(f"NETWORK DIAGNOSTICS SUITE FAILED ({failures} test(s))")
        return 1
    print("ALL NETWORK DIAGNOSTICS TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
