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

        # Example 2: pong from nas (100.107.43.2) via 123.45.67.89:41641 in 28ms
        dir_m = re.search(r"via ([0-9.]+:[0-9]+)\s+in\s+([0-9.]+)ms", line)
        if dir_m:
            direct_matches.append(dir_m.group(1))
            latencies.append(float(dir_m.group(2)))
            continue

    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    min_lat = min(latencies) if latencies else 0.0
    max_lat = max(latencies) if latencies else 0.0

    is_direct = len(direct_matches) > len(derp_matches)
    is_relay = len(derp_matches) > 0 and len(direct_matches) == 0

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
    print(f"目标 NAS 节点 IP : {args.target_ip}")
    print(f"本地代理地址     : {args.proxy_url}")
    print("-" * 70)

    has_ts = check_tailscale_cli()
    if not has_ts:
        print("❌ 未检测到 tailscale CLI 工具。请确保在 GitHub Actions 中已安装 Tailscale。")
        sys.exit(1)

    # 1. Query Tailscale Status
    print("\n[1/4] 正在检测 Tailscale 节点状态 (tailscale status)...")
    status = get_tailscale_status(args.target_ip)
    if status:
        print(f"  ✓ 节点主机名   : {status['hostname']}")
        print(f"  ✓ 操作系统     : {status['os']}")
        print(f"  ✓ 在线状态     : {'在线 (Active)' if status['active'] else '未活跃/离线'}")
        print(f"  ✓ 当前直连地址 : {status['cur_addr'] or '【无直连地址 (未打通 P2P)】'}")
        print(f"  ✓ 当前中继节点 : {status['relay'] or '【无中继 (直连通信中)】'}")
    else:
        print(f"  ⚠️ 未在 tailnet 中找到目标节点 {args.target_ip}，可能节点离线或 IP 不匹配。")

    # 2. Run Tailscale Ping
    print("\n[2/4] 正在向目标节点发送 WireGuard 诊断探针 (tailscale ping)...")
    ping_res = run_tailscale_ping(args.target_ip, count=4)
    print("  --- 探针原始回包 ---")
    for line in ping_res["raw_output"].splitlines()[:6]:
        print(f"    {line}")

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
    is_direct = ping_res["is_direct"] or (status and bool(status.get("cur_addr")))
    derp_region = (
        ping_res["derp_regions"][0]
        if ping_res["derp_regions"]
        else (status.get("relay") if status else "")
    )

    if is_direct:
        verdict = "直连 (DIRECT)"
        verdict_icon = "🚀"
        verdict_color = "绿色"
        detail_msg = f"连接已成功实现 P2P 穿透直连！端点地址: {status.get('cur_addr') if status else ping_res['direct_endpoints']}"
    else:
        verdict = "中继 (DERP RELAY)"
        verdict_icon = "🐢"
        verdict_color = "黄色/红色"
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
    if not is_direct:
        print("1. 为什么是中继 (DERP Relay)？")
        print("   您的 NAS 安装在 Windows 虚拟机内。当 Tailscale 运行在虚拟机中时，流量需要经过：")
        print("   「GitHub Actions (国外) -> 家用路由器 NAT -> Windows 宿主机虚拟交换机 NAT -> Linux/NAS 虚拟机」")
        print("   构成了多层 NAT (Double NAT)，且 Windows 宿主机通常阻断了未经允许的外网入站 UDP 41641 端口探测。")
        print("   Tailscale 在 NAT 穿透握手超时后，被迫回退到了官方免费 DERP 中继服务器（限速且丢包严重）。\n")
        print("2. 如何实现【50倍提速直连 (Direct)】？（三选一）")
        print("   方案 A【最推荐·最简单】：将 NAS 虚拟机的虚拟网卡从「NAT 模式」改为「桥接模式 (Bridged Network)」，使其直接获取局域网真实 IP；并在路由器开启 UPnP。")
        print("   方案 B【端口映射】：在主路由器上将外部 UDP 端口 41641 端口转发（Port Forwarding）直接指向您的 NAS 虚拟机 IP。")
        print("   方案 C【直接运行在 Windows】：直接在宿主机 Windows 系统上安装运行 Tailscale 官方客户端作为 Exit Node，不要走虚拟机内套内。")
    else:
        print("1. 恭喜！当前网络已实现 WireGuard 点对点直连。")
        print("2. 当前速度受限于您家庭宽带的实际上行带宽（Uplink Bandwidth）。")
    print("=" * 70 + "\n")

    # Generate GitHub Step Summary if in Actions environment
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"## {verdict_icon} Tailscale 连通性诊断报告: {verdict}\n\n")
            f.write("| 诊断指标 | 状态与数值 |\n")
            f.write("| :--- | :--- |\n")
            f.write(f"| **当前通信状态** | **{verdict_icon} {verdict}** |\n")
            f.write(f"| **中继服务器** | `{derp_region or '无 (直连模式)'}` |\n")
            f.write(f"| **平均延迟 (RTT)** | `{ping_res['avg_latency_ms']:.1f} ms` |\n")
            f.write(f"| **UDP 穿透支持** | `{'通过' if netcheck['udp'] else '受阻'}` |\n")
            f.write(f"| **对称型 NAT (NAT Mapping)** | `{'是 (MappingVariesByDestIP=true)' if netcheck['symmetric_nat'] else '否 (容易穿透)'}` |\n")
            if speed_res.get("success"):
                f.write(f"| **实测代理下载速率** | **`{speed_res['speed_kb_s']} KB/s` ({speed_res['speed_mb_s']} MB/s)** |\n")
            f.write("\n\n### 💡 结论与优化建议\n")
            f.write(f"> {detail_msg}\n\n")
            if not is_direct:
                f.write("#### 为什么速度慢？\n")
                f.write("- **数据全走免费公共 DERP 中继**：公共 DERP 服务器有单连接带宽限速（通常 100KB/s~300KB/s）。\n")
                f.write("- **虚拟机多层 NAT 阻断**：Windows 宿主机网络过滤导致 WireGuard UDP 无法打洞成功。\n\n")
                f.write("#### 极速提速方案：\n")
                f.write("1. **改用桥接模式 (Bridged)**：虚拟机网卡从 NAT 改为桥接，并在主路由开启 UPnP。\n")
                f.write("2. **端口转发**：路由器将 UDP `41641` 映射到 NAS IP。\n")


if __name__ == "__main__":
    main()
