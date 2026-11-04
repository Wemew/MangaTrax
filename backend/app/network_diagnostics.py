"""Container network diagnostics + bounded speed test (task-33).

Why is the container slower than the host?  The usual suspects are visible from
``/proc``/``/sys`` and a short, fixed-endpoint speed test: interface MTU/state, TCP
congestion control/qdisc/buffers, cgroup CPU quota/throttling, Docker DNS, retransmits,
and the parallelism MangaTrax is currently using.  Everything here is read-only, never
raises, and the speed test is restricted to a fixed Cloudflare endpoint (no SSRF).
"""

import json
import logging
import math
import os
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app import security

logger = logging.getLogger("mangatrax.network")

SPEEDTEST_URL = "https://speed.cloudflare.com/__down"
# Cloudflare's speed endpoint rejects requests above this with HTTP 403 + 1 byte;
# larger requested totals are split into sequential rounds per connection.
SPEEDTEST_MAX_REQUEST_BYTES = 90_000_000
SPEEDTEST_DEFAULT_BYTES = 100_000_000
SPEEDTEST_MAX_BYTES = 1_000_000_000
SPEEDTEST_DEFAULT_CONNECTIONS = 4
SPEEDTEST_MAX_CONNECTIONS = 8
SPEEDTEST_TIMEOUT_SECONDS = 60.0

_SYSCTL_PATHS = {
    "tcp_congestion_control": "/proc/sys/net/ipv4/tcp_congestion_control",
    "default_qdisc": "/proc/sys/net/core/default_qdisc",
    "tcp_rmem": "/proc/sys/net/ipv4/tcp_rmem",
    "tcp_wmem": "/proc/sys/net/ipv4/tcp_wmem",
    "rmem_max": "/proc/sys/net/core/rmem_max",
    "wmem_max": "/proc/sys/net/core/wmem_max",
}
_DOCKER_DNS = "127.0.0.11"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except (OSError, IOError):
        return None


def _read_int(path: str) -> Optional[int]:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(str(text).strip().split()[0])
    except (ValueError, IndexError):
        return None


def _exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except OSError:
        return False


def _interfaces() -> List[Dict[str, Any]]:
    interfaces: Dict[str, Dict[str, Any]] = {}
    base = "/sys/class/net"
    try:
        names = sorted(name for name in os.listdir(base) if name != "lo")
    except OSError:
        names = []
    ipv4 = _ipv4_by_interface()
    ipv6 = _ipv6_by_interface()
    for name in names:
        interfaces[name] = {
            "name": name,
            "mtu": _read_int(f"{base}/{name}/mtu"),
            "operstate": (_read_text(f"{base}/{name}/operstate") or "").strip() or None,
            "mac": (_read_text(f"{base}/{name}/address") or "").strip() or None,
            "ipv4": ipv4.get(name, []),
            "ipv6": ipv6.get(name, []),
        }
    return list(interfaces.values())


def _ipv4_by_interface() -> Dict[str, List[str]]:
    """Best-effort IPv4 mapping from /proc/net/fib_trie (no sockets/network I/O)."""
    text = _read_text("/proc/net/fib_trie") or ""
    mapping: Dict[str, List[str]] = {}
    pending: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|--"):
            continue
        value = line[3:].strip()
        parts = value.split(".")
        if len(parts) == 4 and all(part.isdigit() for part in parts):
            pending = value
            continue
        if pending and value and "/" not in value and " " not in value:
            mapping.setdefault(value, []).append(pending)
            pending = None
    return mapping


def _ipv6_by_interface() -> Dict[str, List[str]]:
    text = _read_text("/proc/net/if_inet6") or ""
    mapping: Dict[str, List[str]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        raw_addr, _idx, _plen, _scope, _flags, name = fields[:6]
        if len(raw_addr) == 32:
            groups = [raw_addr[i:i + 4] for i in range(0, 32, 4)]
            mapping.setdefault(name, []).append(":".join(groups))
    return mapping


def _cpu_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "count": os.cpu_count(),
        "quota_cores": None,
        "quota_us": None,
        "period_us": None,
        "throttled_count": None,
        "nr_periods": None,
        "throttled_usec": None,
    }
    cpu_max = (_read_text("/sys/fs/cgroup/cpu.max") or "").split()
    if len(cpu_max) >= 2 and cpu_max[0] != "max":
        try:
            quota, period = int(cpu_max[0]), int(cpu_max[1])
            info.update({"quota_us": quota, "period_us": period})
            if period:
                info["quota_cores"] = round(quota / period, 2)
        except ValueError:
            pass
    else:
        quota = _read_int("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        period = _read_int("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if quota is not None and quota > 0 and period:
            info.update({"quota_us": quota, "period_us": period,
                         "quota_cores": round(quota / period, 2)})
    stat = _read_text("/sys/fs/cgroup/cpu.stat") or ""
    for line in stat.splitlines():
        key, _, value = line.partition(" ")
        if key in ("nr_periods", "nr_throttled"):
            try:
                info["nr_periods" if key == "nr_periods" else "throttled_count"] = int(value.strip())
            except ValueError:
                pass
        elif key == "throttled_usec":
            try:
                info["throttled_usec"] = int(value.strip())
            except ValueError:
                pass
    return info


def _dns_info() -> Dict[str, Any]:
    text = _read_text("/etc/resolv.conf") or ""
    servers = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            servers.append(parts[1])
    return {"resolvers": servers, "docker_embedded_dns": _DOCKER_DNS in servers}


def _tcp_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {"out_segments": None, "retrans_segments": None,
                            "retrans_rate_pct": None, "congestion_drops": []}
    text = _read_text("/proc/net/snmp") or ""
    header: Optional[List[str]] = None
    for line in text.splitlines():
        if line.startswith("Tcp:"):
            values = line.split()[1:]
            if header is None:
                header = values
                continue
            row = dict(zip(header, values))
            try:
                out_segments = int(row.get("OutSegs", 0))
                retrans = int(row.get("RetransSegs", 0))
            except ValueError:
                continue
            info["out_segments"] = out_segments
            info["retrans_segments"] = retrans
            info["retrans_rate_pct"] = round(retrans / out_segments * 100.0, 3) if out_segments else 0.0
            break
    netstat = _read_text("/proc/net/netstat") or ""
    header = None
    for line in netstat.splitlines():
        if line.startswith("TcpExt:"):
            values = line.split()[1:]
            if header is None:
                header = values
                continue
            row = dict(zip(header, values))
            for key in ("ListenDrops", "TCPBacklogDrop", "TCPTimeouts", "SyncookiesSent"):
                if key in row:
                    try:
                        info["congestion_drops"].append({"metric": key, "value": int(row[key])})
                    except ValueError:
                        pass
            break
    return info


def _route_info() -> Dict[str, Any]:
    info = {"default_gateway": None, "default_interface": None, "routes": []}
    text = _read_text("/proc/net/route") or ""
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        iface, destination, gateway, flags = fields[0], fields[1], fields[2], fields[3]
        info["routes"].append({"interface": iface, "destination": destination,
                               "gateway": gateway, "flags": flags})
        if destination == "00000000":
            info["default_interface"] = iface
            try:
                info["default_gateway"] = ".".join(
                    str(int(gateway[i:i + 2], 16)) for i in (6, 4, 2, 0)
                )
            except ValueError:
                info["default_gateway"] = gateway
    return info


def collect_network_diagnostics(
    settings: Optional[Dict[str, Any]] = None,
    interfaces: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Snapshot in-container network facts + warnings.  Never raises."""
    try:
        settings = settings or {}
        ifaces = interfaces if interfaces is not None else _interfaces()
        sysctls = {
            name: (str(_read_text(path)).strip() if _read_text(path) is not None else None)
            for name, path in _SYSCTL_PATHS.items()
        }
        cpu = _cpu_info()
        dns = _dns_info()
        tcp = _tcp_info()
        route = _route_info()
        container = _exists("/.dockerenv") or _exists("/run/.containerenv")
        try:
            hostname = socket.gethostname()
        except OSError:
            hostname = None
        uptime = _read_text("/proc/uptime")
        mtus = {entry.get("mtu") for entry in ifaces if entry.get("mtu")}
        warnings: List[str] = []
        if len(mtus) > 1:
            warnings.append(
                f"Interface MTU mismatch ({sorted(mtus)}) — veth/bridge MTU must match the "
                "host to avoid fragmentation and slow TLS."
            )
        elif mtus and 1500 not in mtus and 9000 not in mtus:
            warnings.append(f"Unusual interface MTU {sorted(mtus)} — align with the host MTU.")
        congestion = (sysctls.get("tcp_congestion_control") or "").lower()
        if congestion and congestion != "bbr":
            warnings.append(
                f"TCP congestion control is '{congestion}' — BBR usually improves throughput on "
                "long-RTT WAN links (set net.ipv4.tcp_congestion_control=bbr)."
            )
        quota = cpu.get("quota_cores")
        if quota is not None and quota < 1.0:
            warnings.append(
                f"CPU quota is {quota} core(s) — TLS/decryption can be CPU-bound and throttle "
                "parallel downloads."
            )
        rate = tcp.get("retrans_rate_pct")
        if rate is not None and rate > 0.5:
            warnings.append(
                f"TCP retransmit rate is {rate}% — packet loss/fragmentation can cap a single "
                "connection far below link speed."
            )
        if dns.get("docker_embedded_dns"):
            warnings.append(
                "Docker embedded DNS (127.0.0.11) is in use — parallel connections can contend "
                "on DNS resolution; consider longer DNS caches."
            )
        if not ifaces:
            warnings.append("No network interfaces could be read from /sys/class/net.")
        return {
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "container": bool(container),
            "hostname": hostname,
            "uptime": str(uptime).strip() if uptime else None,
            "interfaces": ifaces,
            "mtu": sorted(mtus),
            "sysctls": sysctls,
            "cpu": cpu,
            "dns": dns,
            "tcp": tcp,
            "route": route,
            "app": {
                "torbox_download_concurrency": settings.get("torbox_download_concurrency"),
                "torbox_parallel_chunks": settings.get("torbox_parallel_chunks"),
                "downloader_engine": settings.get("downloader_engine"),
            },
            "warnings": warnings,
        }
    except Exception as exc:  # pragma: no cover - diagnostics must never break callers
        logger.debug("Network diagnostics failed: %s", exc)
        return {
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "container": _exists("/.dockerenv"),
            "interfaces": [],
            "sysctls": {},
            "warnings": [f"Network diagnostics failed: {type(exc).__name__}: {exc}"],
        }


def validate_speedtest_url(url: str) -> None:
    """Only the fixed Cloudflare speed endpoint may be used (no arbitrary URL/SSRF)."""
    if not str(url).startswith(SPEEDTEST_URL):
        raise ValueError("speed test URL is not in the fixed allow-list")
    security.validate_public_url(url, resolve_dns=True)


def _download_once(url: str, byte_count: int, timeout: float) -> Tuple[int, float, int]:
    """One ranged GET; returns (status, bytes, seconds).  Never returns body data."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MangaTrax/1.0", "Range": f"bytes=0-{max(0, byte_count - 1)}"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            read = 0
            while read < byte_count:
                chunk = response.read(min(256 * 1024, byte_count - read))
                if not chunk:
                    break
                read += len(chunk)
        return status, read, max(0.001, time.monotonic() - started)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(64) or b""
        except Exception:
            body = b""
        return int(exc.code), len(body), max(0.001, time.monotonic() - started)


def _speedtest_error(message: str, **extra: Any) -> Dict[str, Any]:
    result = {
        "status": "error",
        "error": message,
        "endpoint_limit_bytes": SPEEDTEST_MAX_REQUEST_BYTES,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    result.update(extra)
    return result


def run_speedtest(
    bytes_total: int = SPEEDTEST_DEFAULT_BYTES,
    connections: int = SPEEDTEST_DEFAULT_CONNECTIONS,
    url: str = SPEEDTEST_URL,
    timeout: float = SPEEDTEST_TIMEOUT_SECONDS,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Bounded, fixed-endpoint parallel speed test (never raises).

    Cloudflare rejects >90 MB per request with HTTP 403 + a 1-byte body, so each
    connection repeats sequential ``<=90 MB`` rounds until its share is served; the
    actual transferred bytes are verified against the requested count.
    """
    try:
        bytes_total = int(bytes_total)
        connections = int(connections)
    except (TypeError, ValueError):
        return _speedtest_error("bytes and connections must be integers")
    if bytes_total < 1 or bytes_total > SPEEDTEST_MAX_BYTES:
        return _speedtest_error(
            f"bytes must be between 1 and {SPEEDTEST_MAX_BYTES} (1 GB per run)"
        )
    if connections < 1 or connections > SPEEDTEST_MAX_CONNECTIONS:
        return _speedtest_error(
            f"connections must be between 1 and {SPEEDTEST_MAX_CONNECTIONS}"
        )
    try:
        validate_speedtest_url(url)
    except Exception as exc:
        return _speedtest_error(f"speed test URL rejected: {exc}")
    per_connection_total = int(math.ceil(bytes_total / connections))
    rounds = max(1, int(math.ceil(per_connection_total / SPEEDTEST_MAX_REQUEST_BYTES)))
    errors: List[str] = []
    started = time.monotonic()

    def _connection_worker(index: int) -> Dict[str, Any]:
        remaining = per_connection_total
        conn_started = time.monotonic()
        transferred = 0
        request_sizes: List[int] = []
        while remaining > 0:
            chunk = min(SPEEDTEST_MAX_REQUEST_BYTES, remaining)
            try:
                status, read, seconds = _download_once(url, chunk, timeout)
            except Exception as exc:
                return {"connection": index + 1, "error": f"{type(exc).__name__}: {exc}",
                        "bytes": transferred, "mbps": 0.0}
            request_sizes.append(chunk)
            if status != 200 or read < chunk:
                return {
                    "connection": index + 1,
                    "error": (
                        f"connection {index + 1}: HTTP {status}, received {read}/{chunk} bytes "
                        f"(Cloudflare caps a single request at {SPEEDTEST_MAX_REQUEST_BYTES // 1_000_000} MB; "
                        "the run splits larger totals into sequential rounds)"
                    ),
                    "bytes": transferred + read, "mbps": 0.0, "status": status,
                }
            transferred += read
            remaining -= chunk
        elapsed = max(0.001, time.monotonic() - conn_started)
        return {
            "connection": index + 1,
            "bytes": transferred,
            "seconds": max(0.001, round(elapsed, 2)),
            "mbps": round(transferred / 1_000_000 / elapsed, 2),
            "rounds": len(request_sizes),
        }

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=connections, thread_name_prefix="net-speedtest") as pool:
        futures = [pool.submit(_connection_worker, index) for index in range(connections)]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{type(exc).__name__}: {exc}")
    results.sort(key=lambda item: item.get("connection", 0))
    wall_seconds = max(0.001, time.monotonic() - started)
    transferred_total = sum(item.get("bytes") or 0 for item in results)
    errors.extend(item["error"] for item in results if item.get("error"))
    network = collect_network_diagnostics(settings=settings)
    if errors:
        return _speedtest_error("; ".join(errors[:3]) or "speed test failed",
                                bytes_requested=bytes_total, bytes_transferred=transferred_total,
                                connections=connections, per_connection=results,
                                wall_seconds=round(wall_seconds, 2), url=url,
                                capped=bool(rounds > 1), network=network)
    result = {
        "status": "success",
        "url": url,
        "bytes_requested": bytes_total,
        "bytes_transferred": transferred_total,
        "connections": connections,
        "rounds_per_connection": rounds,
        "capped": bool(rounds > 1 or bytes_total > SPEEDTEST_MAX_REQUEST_BYTES),
        "endpoint_limit_bytes": SPEEDTEST_MAX_REQUEST_BYTES,
        "per_connection": results,
        "wall_seconds": round(wall_seconds, 2),
        "aggregate_mbps": round(transferred_total / 1_000_000 / wall_seconds, 2),
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "network": network,
    }
    return result
