#!/usr/bin/env python3
"""
Tailscale Network & Proxy Diagnostics Tool
Determines whether GitHub Actions <-> NAS Exit Node connection is:
- DIRECT (直连 P2P WireGuard UDP)
- or RELAY (中继 DERP Relay)
Measures latency, NAT traversal capability, and HTTP proxy throughput.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Dict, Optional, Tuple
import urllib.request


def run_cmd(cmd: list, timeout: int = 30) -> Tuple[int, str, str]:
    """Execute system command and return (returncode, stdout, stderr)."""
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as e:
        return -1, "", f"Failed to execute {' '.join(cmd)}: {e}"


def check_tailscale_cli() -> bool:
    """Check if tailscale binary exists."""
    return shutil.which("tailscale") is not None


def get_tailscale_status(target_ip: str) -> Optional[dict]:
    """Parse tailscale status --json for target IP."""
    code, stdout, stderr = run_cmd(["tailscale", "status", "--json"])
    if code != 0 or not stdout:
        return None
    try:
        data = json.loads(stdout)
        peers = data.get("Peer") or {}
        for node_key, p in peers.items():
            ips = p.get("TailscaleIPs") or []
            if target_ip in ips:
                return {
                    "node_key": node_key,
                    "hostname": p.get("HostName", "unknown"),
                    "ips": ips,
                    "os": p.get("OS", "unknown"),
                    "cur_addr": p.get("CurAddr", ""),
                    "relay": p.get("Relay", ""),
                    "active": p.get("Active", False),
                    "online": p.get("Online", False),
                    "exit_node": p.get("ExitNode", False),
                    "exit_node_option": p.get("ExitNodeOption", False),
                    "last_handshake": p.get("LastHandshake", ""),
                    "rx_bytes": p.get("RxBytes", 0),
                    "tx_bytes": p.get("TxBytes", 0),
                }
    except Exception as e:
        print(f"Error parsing status json: {e}")
    return None


def run_tailscale_ping(target_ip: str, count: int = 5) -> dict:
    """Run tailscale ping to check direct vs DERP relay."""
    cmd = ["tailscale", "ping", f"--c={count}", target_ip]
    code, stdout, stderr = run_cmd(cmd, timeout=25)

    derp_matches = []
    direct_matches = []
    latencies = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Example 1: pong from nas (100.107.43.2) via DERP(tok) in 185ms
        derp_m = re.search(r"via DERP\(([^)]+)\)\s+in\s+([0-9.]+)ms", line)
        if derp_m:
            derp_matches.append(derp_m.group(1))
            latencies.append(float(derp_m.group(2)))
            continue

        # Example 2: pong from nas (100.107.43.2) via 123.45.67.89:41641 or [2409:...]:41641 in 28ms
        dir_m = re.search(r"via (\[[a-fA-F0-9:]+\]:[0-9]+|[a-fA-F0-9.:]+:[0-9]+)\s+in\s+([0-9.]+)ms", line)
        if dir_m:
            direct_matches.append(dir_m.group(1))
            latencies.append(float(dir_m.group(2)))
            continue

    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    min_lat = min(latencies) if latencies else 0.0
    max_lat = max(latencies) if latencies else 0.0

    is_direct = len(direct_matches) > 0
    is_relay = len(direct_matches) == 0 and len(derp_matches) > 0

    return {
        "raw_output": stdout or stderr,
        "is_direct": is_direct,
        "is_relay": is_relay,
        "derp_regions": list(set(derp_matches)),
        "direct_endpoints": list(set(direct_matches)),
        "packets_sent": count,
        "packets_recv": len(latencies),
        "min_latency_ms": min_lat,
        "avg_latency_ms": avg_lat,
        "max_latency_ms": max_lat,
    }


def run_tailscale_netcheck() -> dict:
    """Run tailscale netcheck to inspect NAT traversal capabilities."""
    code, stdout, stderr = run_cmd(["tailscale", "netcheck"], timeout=30)
    raw = stdout or stderr

    udp = bool(re.search(r"\*\s+UDP:\s+true", raw, re.IGNORECASE))
    sym_nat = bool(re.search(r"\*\s+MappingVariesByDestIP:\s+true", raw, re.IGNORECASE))
    hairpin = bool(re.search(r"\*\s+HairPinning:\s+true", raw, re.IGNORECASE))

    nearest_derp_m = re.search(r"Nearest DERP:\s+([^\r\n]+)", raw)
    nearest_derp = nearest_derp_m.group(1).strip() if nearest_derp_m else "Unknown"

    port_map_m = re.search(r"\*\s+PortMapping:\s+([^\r\n]+)", raw)
    port_mapping = port_map_m.group(1).strip() if port_map_m else "none"

    return {
        "raw": raw,
        "udp": udp,
        "symmetric_nat": sym_nat,
        "hairpinning": hairpin,
        "nearest_derp": nearest_derp,
        "port_mapping": port_mapping,
    }


def benchmark_proxy_speed(proxy_url: str, test_urls: list, max_duration: int = 15) -> dict:
    """Benchmark HTTP proxy download speed."""
    proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(proxy_handler)

    results = []
    for test_url in test_urls:
        try:
            req = urllib.request.Request(
                test_url,
                headers={"User-Agent": "TailscaleDiagnostics/1.0", "Referer": "https://www.bilibili.com/"},
            )
            t0 = time.time()
            total_bytes = 0
            with opener.open(req, timeout=10) as resp:
                status = resp.status
                while time.time() - t0 < max_duration:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes >= 15 * 1024 * 1024:  # max 15MB
                        break

            dt = max(time.time() - t0, 0.001)
            kb = total_bytes / 1024
            speed_kb_s = kb / dt
            speed_mb_s = speed_kb_s / 1024

            results.append({
                "url": test_url,
                "status": status,
                "bytes": total_bytes,
                "duration_s": round(dt, 2),
                "speed_kb_s": round(speed_kb_s, 2),
                "speed_mb_s": round(speed_mb_s, 2),
                "success": True,
            })
            break  # Stop after first successful test
        except Exception as e:
            results.append({
                "url": test_url,
                "error": str(e),
                "success": False,
            })

    return results[0] if results else {"success": False, "error": "No test completed"}


def main():
    parser = argparse.ArgumentParser(description="Tailscale Diagnostics: Direct vs DERP Relay")
    parser.add_argument(
        "--target-ip",
        default=os.environ.get("TAILSCALE_EXIT_NODE", "100.107.43.2"),
        help="Tailscale IP of NAS exit node (default: 100.107.43.2)",
    )
    parser.add_argument(
        "--proxy-url",
        default=os.environ.get("CHINA_PROXY", "http://127.0.0.1:1055"),
        help="Local proxy URL exposed by Tailsocks (default: http://127.0.0.1:1055)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("      🔍 Tailscale 网络诊断与连通性分析 (Direct vs DERP Relay)")
    print("=" * 70)
    runner_ipv6 = None
    for endpoint in ["https://api64.ipify.org", "https://v6.ident.me", "https://ifconfig.co"]:
        try:
            code_ip, stdout_ip, _ = run_cmd(["curl", "-6", "-s", "--max-time", "3", endpoint])
            if code_ip == 0 and ":" in stdout_ip:
                runner_ipv6 = stdout_ip.strip()
                break
        except Exception:
            pass

    print(f"目标 NAS 节点 IP : {args.target_ip}")
    print(f"本地代理地址     : {args.proxy_url}")
    print(f"Runner 公网 IPv6 : {runner_ipv6 or '未启用 (仅 IPv4)'}")
    print("-" * 70)

    has_ts = check_tailscale_cli()
    if not has_ts:
        print("❌ 未检测到 tailscale CLI 工具。请确保在 GitHub Actions 中已安装 Tailscale。")
        sys.exit(1)

    # 1. Query Tailscale Status (Initial)
    print("\n[1/4] 正在检测 Tailscale 节点初始状态 (tailscale status)...")
    status = get_tailscale_status(args.target_ip)
    if status:
        print(f"  ✓ 节点主机名   : {status['hostname']}")
        print(f"  ✓ 操作系统     : {status['os']}")
        print(f"  ✓ 在线状态     : {'在线 (Active)' if status['active'] else '未活跃/离线'}")
        print(f"  ✓ 初始直连地址 : {status['cur_addr'] or '【无直连地址，待探针触发打洞】'}")
        print(f"  ✓ 初始中继节点 : {status['relay'] or '【无中继】'}")
    else:
        print(f"  ⚠️ 未在 tailnet 中找到目标节点 {args.target_ip}，可能节点离线或 IP 不匹配。")

    # 2. Run Tailscale Ping (Triggers Disco & Hole-Punching)
    print("\n[2/4] 正在向目标节点发送 WireGuard 诊断探针 (tailscale ping)...")
    ping_res = run_tailscale_ping(args.target_ip, count=6)
    print("  --- 探针原始回包 ---")
    for line in ping_res["raw_output"].splitlines()[:8]:
        print(f"    {line}")

    # Re-query status after ping to capture dynamic path upgrade
    status_after = get_tailscale_status(args.target_ip) or status
    direct_endpoint = ""
    if status_after and status_after.get("cur_addr"):
        direct_endpoint = status_after["cur_addr"]
    elif ping_res["direct_endpoints"]:
        direct_endpoint = ping_res["direct_endpoints"][-1]

    is_direct = bool(direct_endpoint) or ping_res["is_direct"]
    is_ipv6_direct = False
    if is_direct and direct_endpoint:
        if "[" in direct_endpoint or direct_endpoint.count(":") > 1:
            is_ipv6_direct = True

    # 3. Run Tailscale Netcheck
    print("\n[3/4] 正在分析当前环境 NAT 穿透特征 (tailscale netcheck)...")
    netcheck = run_tailscale_netcheck()
    print(f"  ✓ UDP 支持状态     : {'正常通过' if netcheck['udp'] else '❌ 被防火墙阻断'}")
    print(f"  ✓ 对称型 NAT 检查  : {'❌ 存在 (MappingVariesByDestIP=true，穿透极难)' if netcheck['symmetric_nat'] else '✓ 正常 (容易穿透)'}")
    print(f"  ✓ 端口映射协议     : {netcheck['port_mapping']}")
    print(f"  ✓ 最近 DERP 节点   : {netcheck['nearest_derp']}")

    # 4. Benchmark Proxy Speed
    print("\n[4/4] 正在测试通过 NAS 代理的实际下载吞吐量...")
    test_urls = [
        "https://i0.hdslb.com/bfs/face/member/noface.jpg",
        "https://speed.cloudflare.com/__down?bytes=5000000",
        "https://www.bilibili.com/favicon.ico",
    ]
    speed_res = benchmark_proxy_speed(args.proxy_url, test_urls, max_duration=12)
    if speed_res.get("success"):
        print(f"  ✓ 实测下载文件大小 : {speed_res['bytes'] / 1024:.1f} KB")
        print(f"  ✓ 下载耗时         : {speed_res['duration_s']} 秒")
        print(f"  ✓ 实测平均下载网速 : {speed_res['speed_kb_s']} KB/s ({speed_res['speed_mb_s']} MB/s)")
    else:
        print(f"  ⚠️ 代理下载测试失败: {speed_res.get('error')}")

    # Determine Verdict
    derp_region = (
        ping_res["derp_regions"][0]
        if ping_res["derp_regions"]
        else (status_after.get("relay") if status_after else "")
    )

    if is_direct:
        verdict = "直连 (DIRECT)"
        verdict_icon = "🚀"
        if is_ipv6_direct:
            detail_msg = f"🎉 成功建立公网 IPv6 端到端纯公网 P2P 直连！端点: {direct_endpoint}，完全跳过了 DERP 中继！"
        else:
            detail_msg = f"连接已成功实现 P2P 穿透直连！端点: {direct_endpoint}"
    else:
        verdict = "中继 (DERP RELAY)"
        verdict_icon = "🐢"
        detail_msg = f"当前数据流全部通过 Tailscale 公共中继服务器转发（中继区域代码: {derp_region or '未知'}）。这正是导致速度只有 ~120KB/s 的直接根源！"

    print("\n" + "=" * 70)
    print(f"              🎯 诊断最终结论: {verdict_icon} 【{verdict}】")
    print("=" * 70)
    print(f"• 通信模式     : {verdict}")
    print(f"• 详细判定     : {detail_msg}")
    print(f"• 平均往返延迟 : {ping_res['avg_latency_ms']:.1f} ms")
    if speed_res.get("success"):
        print(f"• 代理下载速率 : {speed_res['speed_kb_s']} KB/s ({speed_res['speed_mb_s']} MB/s)")
    print("-" * 70)

    # Output Root Cause & Recommendations
    print("\n💡 【原因深度解析与提速方案】:")
    if is_direct:
        if is_ipv6_direct:
            print("1. 恭喜！Cloudflare WARP IPv6 成功打通了与家庭 NAS 的纯公网 IPv6 P2P 直连！")
            print("2. 流量直接端到端 WireGuard UDP 通信，完全绕过 DERP 中继，充分跑满家庭宽带上行！")
        else:
            print("1. 恭喜！当前网络已实现 WireGuard 点对点直连。")
            print("2. 当前速度受限于您家庭宽带的实际上行带宽（Uplink Bandwidth）。")
    else:
        print("1. 为什么仍是中继 (DERP Relay)？")
        if runner_ipv6:
            print("   GitHub Actions Runner 已经成功获取公网 IPv6 出口，但直连握手仍未建立。可能原因：")
            print("   - NAS 虚拟机尚未获取独立的公网 IPv6 地址（2409:...）。")
            print("   - 小米路由器虽然开启了 IPv6，但开启了 IPv6 防火墙，丢弃了外网入站的 UDP 数据包。")
            print("   - 虚拟机内部防火墙阻止了 UDP 41641 入站。")
        else:
            print("   Runner 未能成功获得 IPv6 出口，且家庭网络为 100.65 运营商大内网 (CGNAT)，缺乏公网 IPv4。")
        print("\n2. 后续优化方向：")
        print("   - 检查 NAS 是否拥有 2409: 开头的公网 IPv6 地址。")
        print("   - 检查小米路由器后台是否开启了「IPv6 防火墙」，尝试放行或临时关闭测试。")
    print("=" * 70 + "\n")

    # Generate GitHub Step Summary if in Actions environment
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"## {verdict_icon} Tailscale 连通性诊断报告: {verdict}\n\n")
            f.write("| 诊断指标 | 状态与数值 |\n")
            f.write("| :--- | :--- |\n")
            f.write(f"| **当前通信状态** | **{verdict_icon} {verdict}** |\n")
            f.write(f"| **Runner 公网 IPv6** | `{runner_ipv6 or '未启用'}` |\n")
            f.write(f"| **直连端点 / 中继** | `{direct_endpoint if is_direct else ('DERP: ' + str(derp_region))}` |\n")
            f.write(f"| **平均延迟 (RTT)** | `{ping_res['avg_latency_ms']:.1f} ms` |\n")
            f.write(f"| **UDP 穿透支持** | `{'通过' if netcheck['udp'] else '受阻'}` |\n")
            f.write(f"| **对称型 NAT (NAT Mapping)** | `{'是 (MappingVariesByDestIP=true)' if netcheck['symmetric_nat'] else '否 (容易穿透)'}` |\n")
            if speed_res.get("success"):
                f.write(f"| **实测代理下载速率** | **`{speed_res['speed_kb_s']} KB/s` ({speed_res['speed_mb_s']} MB/s)** |\n")
            f.write("\n\n### 💡 结论与说明\n")
            f.write(f"> {detail_msg}\n\n")


if __name__ == "__main__":
    main()
