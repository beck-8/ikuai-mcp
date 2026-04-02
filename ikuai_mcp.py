"""
iKuai Router MCP Server
========================
MCP server for interacting with iKuai (爱快) routers via their HTTP API.
Provides 39 read-only tools covering: system status, WAN/LAN interfaces,
online devices, DHCP, DNS, ACL, traffic stats, VLAN, DDNS, Docker, VMs,
QoS, flow control, load balancing, UPnP, SD-WAN, behavior audit (URL/IM/
terminal records), system logs, warnings, and a raw API passthrough.

Usage:
    Set environment variables:
        IKUAI_URL=http://192.168.9.1:81
        IKUAI_USERNAME=admin
        IKUAI_PASSWORD=yourpassword

    Run:
        python ikuai_mcp.py            # stdio transport (default)
        python ikuai_mcp.py --http      # streamable HTTP transport on port 8000
"""

import os
import sys
import json
import hashlib
import time
import logging
import functools
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from enum import Enum

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import httpx
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP, Context

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IKUAI_BASE_URL = os.environ.get("IKUAI_URL", "http://192.168.9.1:81").rstrip("/")
IKUAI_USERNAME = os.environ.get("IKUAI_USERNAME", "admin")
IKUAI_PASSWORD = os.environ.get("IKUAI_PASSWORD", "admin")
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

logger = logging.getLogger("ikuai_mcp")
logging.basicConfig(level=logging.INFO, stream=sys.stderr)


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class IKuaiClient:
    """Async HTTP client for iKuai router API with session management."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._last_login: float = 0

    async def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=IKUAI_BASE_URL,
                timeout=15.0,
                verify=False,
            )

    async def login(self) -> bool:
        """Authenticate with the iKuai router and obtain a session cookie."""
        await self._ensure_client()

        # iKuai expects MD5-hashed password
        passwd_md5 = hashlib.md5(IKUAI_PASSWORD.encode()).hexdigest()

        payload = {
            "username": IKUAI_USERNAME,
            "passwd": passwd_md5,
            "pass": passwd_md5,
        }

        try:
            resp = await self._client.post("/Action/login", json=payload)
            data = resp.json()

            if data.get("Result") == 10000 or data.get("code") == 0:
                self._last_login = time.time()
                logger.info("iKuai login successful")
                return True

            logger.error(f"iKuai login failed: {data}")
            return False
        except Exception as e:
            logger.error(f"iKuai login error: {e}")
            return False

    async def _ensure_session(self):
        """Re-login if session is stale (older than 10 minutes)."""
        await self._ensure_client()
        if time.time() - self._last_login > 600:
            await self.login()

    async def api_call(
        self,
        func_name: str,
        action: str = "show",
        param: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make a call to /Action/call.

        Returns the 'results' dict from the response, or raises on error.
        """
        await self._ensure_session()

        payload: Dict[str, Any] = {
            "func_name": func_name,
            "action": action,
        }
        if param:
            payload["param"] = param

        try:
            resp = await self._client.post("/Action/call", json=payload)
            data = resp.json()

            # Session expired — re-login and retry once
            if data.get("code") == 1003:
                logger.info("Session expired (1003), re-authenticating...")
                await self.login()
                resp = await self._client.post("/Action/call", json=payload)
                data = resp.json()

            if data.get("code") != 0:
                return {
                    "error": True,
                    "message": data.get("message", "Unknown API error"),
                    "code": data.get("code"),
                    "raw": data,
                }

            return data.get("results", {})

        except httpx.TimeoutException:
            return {"error": True, "message": "Request to router timed out. Check connectivity."}
        except httpx.ConnectError:
            return {"error": True, "message": f"Cannot connect to router at {IKUAI_BASE_URL}. Check host/port."}
        except Exception as e:
            return {"error": True, "message": f"Unexpected error: {type(e).__name__}: {e}"}

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# Shared singleton client
# ---------------------------------------------------------------------------

_client = IKuaiClient()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(b: int) -> str:
    """Format bytes into human-readable string."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.2f} KB"
    elif b < 1024 ** 3:
        return f"{b / (1024 ** 2):.2f} MB"
    else:
        return f"{b / (1024 ** 3):.2f} GB"


def _fmt_uptime(seconds: int) -> str:
    """Format seconds into days/hours/minutes."""
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    parts = []
    if d:
        parts.append(f"{d}天")
    if h:
        parts.append(f"{h}时")
    parts.append(f"{m}分")
    return "".join(parts)


def _json_result(data: Any) -> str:
    """Serialize result to JSON string."""
    return json.dumps(data, ensure_ascii=False, indent=2)


def _check_error(result: Dict) -> Optional[str]:
    """If the result contains an error, return an error message string."""
    if isinstance(result, dict) and result.get("error"):
        return f"Error: {result.get('message', 'Unknown error')}"
    return None


def _tool_logged(fn):
    """Decorator that adds MCP protocol-level logging and timing to tool functions."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        ctx = kwargs.get("ctx") or next((a for a in args if isinstance(a, Context)), None)
        tool_name = fn.__name__
        start = time.monotonic()
        logger.info(f"Tool call: {tool_name}")
        if ctx:
            await ctx.info(f"Executing {tool_name}")
        try:
            result = await fn(*args, **kwargs)
            elapsed = (time.monotonic() - start) * 1000
            logger.info(f"Tool {tool_name} completed in {elapsed:.0f}ms")
            if ctx:
                await ctx.info(f"{tool_name} completed in {elapsed:.0f}ms")
            return result
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.error(f"Tool {tool_name} failed after {elapsed:.0f}ms: {exc}")
            if ctx:
                await ctx.error(f"{tool_name} failed: {exc}")
            raise
    return wrapper


# ---------------------------------------------------------------------------
# Response format enum
# ---------------------------------------------------------------------------

class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


# ---------------------------------------------------------------------------
# Lifespan: login on start, close on shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def app_lifespan(server):
    logger.info(f"Connecting to iKuai router at {IKUAI_BASE_URL} ...")
    await _client.login()
    yield {"client": _client}
    await _client.close()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("ikuai_mcp", lifespan=app_lifespan)


# ========================== SYSTEM STATUS ==================================

class SystemStatusInput(BaseModel):
    """Input for getting system status."""
    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for raw data",
    )


@mcp.tool(
    name="ikuai_get_system_status",
    annotations={
        "title": "Get System Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_system_status(params: SystemStatusInput, ctx: Context) -> str:
    """Get iKuai router system status including CPU, memory, temperature,
    online users, connection count, uptime, and firmware version.

    Returns:
        str: System status in markdown or JSON format.
    """
    result = await _client.api_call("homepage", "show", {
        "TYPE": "sysstat"
    })
    err = _check_error(result)
    if err:
        return err

    stat = result.get("sysstat", {})

    if params.response_format == ResponseFormat.JSON:
        return _json_result(stat)

    cpu = stat.get("cpu", [])
    mem = stat.get("memory", {})
    users = stat.get("online_user", {})
    stream = stat.get("stream", {})
    ver = stat.get("verinfo", {})
    temps = stat.get("cputemp", [])

    cpu_str = " / ".join(cpu) if cpu else "N/A"
    temp_str = ", ".join(f"{t}°C" for t in temps) if temps else "N/A"

    return f"""## iKuai 系统状态

**主机名**: {stat.get('hostname', 'N/A')}
**管理 IP**: {stat.get('ip_addr', 'N/A')}
**固件版本**: {ver.get('verstring', 'N/A')}
**运行时间**: {_fmt_uptime(stat.get('uptime', 0))}
**架构**: {ver.get('arch', '')} {ver.get('sysbit', '')}

### CPU
- **使用率**: {cpu_str}
- **温度**: {temp_str}

### 内存
- **总量**: {_fmt_bytes(mem.get('total', 0) * 1024)}
- **已用**: {mem.get('used', 'N/A')}
- **可用**: {_fmt_bytes(mem.get('available', 0) * 1024)}

### 在线终端
- **总数**: {users.get('count', 0)}
- **有线**: {users.get('count_wired', 0)}
- **无线**: {users.get('count_wireless', 0)} (2.4G: {users.get('count_2g', 0)}, 5G: {users.get('count_5g', 0)})

### 连接数
- **总连接**: {stream.get('connect_num', 0)}
- **TCP**: {stream.get('tcp_connect_num', 0)} / **UDP**: {stream.get('udp_connect_num', 0)} / **ICMP**: {stream.get('icmp_connect_num', 0)}
- **当前上行**: {_fmt_bytes(stream.get('upload', 0))}/s
- **当前下行**: {_fmt_bytes(stream.get('download', 0))}/s
- **累计上行**: {_fmt_bytes(stream.get('total_up', 0))}
- **累计下行**: {_fmt_bytes(stream.get('total_down', 0))}
"""


# ========================== WAN INFO =======================================

class WanInfoInput(BaseModel):
    """Input for getting WAN interface information."""
    model_config = ConfigDict(extra="forbid")

    interface: str = Field(
        default="adsl1",
        description="WAN interface name (e.g., 'adsl1', 'adsl2', 'adsl3'). Default is 'adsl1'.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


@mcp.tool(
    name="ikuai_get_wan_info",
    annotations={
        "title": "Get WAN Interface Info",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_wan_info(params: WanInfoInput, ctx: Context) -> str:
    """Get WAN interface status including IP, gateway, ISP, uptime,
    speed, and latency for a specific WAN link.

    Args:
        params: WAN interface name and response format.

    Returns:
        str: WAN info in markdown or JSON format.
    """
    result = await _client.api_call("homepage", "show", {
        "TYPE": "wan_stat",
        "ifname": params.interface,
        "interface": params.interface,
    })
    err = _check_error(result)
    if err:
        return err

    wan = result.get("wan_stat", {})

    if params.response_format == ResponseFormat.JSON:
        return _json_result(wan)

    isp_map = {"CTCC": "中国电信", "CUCC": "中国联通", "CMCC": "中国移动"}
    isp = isp_map.get(wan.get("isp", ""), wan.get("isp", "未知"))

    return f"""## WAN 线路信息 — {params.interface}

- **WAN IP**: {wan.get('ip_addr', 'N/A')}
- **网关**: {wan.get('gateway', 'N/A')}
- **接入方式**: {wan.get('internet', 'N/A')}
- **运营商**: {isp}
- **延迟 (RTT)**: {wan.get('rtt', 'N/A')} ms
- **状态**: {wan.get('errmsg', 'N/A')}
- **上行速率**: {wan.get('upload', 0)} KB/s
- **下行速率**: {wan.get('download', 0)} KB/s
- **累计流量**: {_fmt_bytes(wan.get('total', 0))}
"""


# ========================== ONLINE DEVICES =================================

class OnlineDevicesInput(BaseModel):
    """Input for listing online devices (terminals)."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=DEFAULT_LIMIT,
        description="Maximum number of devices to return",
        ge=1,
        le=MAX_LIMIT,
    )
    offset: int = Field(
        default=0,
        description="Number of devices to skip for pagination",
        ge=0,
    )
    order_by: str = Field(
        default="ip_addr_int",
        description="Sort field: 'ip_addr_int' (IP), 'connect_num' (connections), 'upload', 'download', 'total_up', 'total_down'",
    )
    order: str = Field(
        default="asc",
        description="Sort order: 'asc' or 'desc'",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


@mcp.tool(
    name="ikuai_get_online_devices",
    annotations={
        "title": "Get Online Devices",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_online_devices(params: OnlineDevicesInput, ctx: Context) -> str:
    """List all currently online devices (terminals) connected to the router.
    Includes IP, MAC, hostname, device type, upload/download speeds, and connection count.

    Args:
        params: Pagination, sorting, and format options.

    Returns:
        str: Online device list in markdown or JSON format.
    """
    result = await _client.api_call("monitor_lanip", "show", {
        "TYPE": "data,total",
        "limit": f"{params.offset},{params.limit}",
        "ORDER_BY": params.order_by,
        "ORDER": params.order,
    })
    err = _check_error(result)
    if err:
        return err

    total = result.get("total", 0)
    devices = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        return _json_result({
            "total": total,
            "count": len(devices),
            "offset": params.offset,
            "has_more": total > params.offset + len(devices),
            "devices": devices,
        })

    lines = [f"## 在线终端列表 (共 {total} 台, 显示 {len(devices)} 台)\n"]
    lines.append("| # | IP 地址 | MAC | 设备名 | 类型 | 连接数 | 上行 | 下行 | 今日流量 |")
    lines.append(
        "|---|---------|-----|--------|------|--------|------|------|----------|")

    for i, d in enumerate(devices, start=params.offset + 1):
        name = d.get("comment") or d.get("termname") or d.get("hostname", "—")
        dtype = d.get("client_type", "Unknown")
        ip = d.get("ip_addr", "")
        mac = d.get("mac", "")
        conn = d.get("connect_num", 0)
        up = _fmt_bytes(d.get("upload", 0))
        down = _fmt_bytes(d.get("download", 0))
        today = _fmt_bytes(d.get("today_total", 0))
        lines.append(
            f"| {i} | {ip} | {mac} | {name} | {dtype} | {conn} | {up}/s | {down}/s | {today} |")

    if total > params.offset + len(devices):
        lines.append(
            f"\n> 还有更多设备，使用 offset={params.offset + len(devices)} 查看下一页")

    return "\n".join(lines)


# ========================== DHCP CONFIG ====================================

class DhcpConfigInput(BaseModel):
    """Input for getting DHCP server configuration."""
    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


@mcp.tool(
    name="ikuai_get_dhcp_config",
    annotations={
        "title": "Get DHCP Server Config",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_dhcp_config(params: DhcpConfigInput, ctx: Context) -> str:
    """Get DHCP server pool configuration including address ranges,
    DNS servers, gateway, lease time, and available addresses.

    Returns:
        str: DHCP config in markdown or JSON format.
    """
    result = await _client.api_call("dhcp_server", "show", {
        "TYPE": "total,data",
        "limit": "0,500",
    })
    err = _check_error(result)
    if err:
        return err

    data = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        return _json_result({"total": result.get("total", 0), "pools": data})

    lines = [f"## DHCP 服务器配置 (共 {result.get('total', 0)} 个地址池)\n"]

    for pool in data:
        enabled = "✅ 启用" if pool.get("enabled") == "yes" else "❌ 禁用"
        lines.append(f"""### {pool.get('tagname', 'N/A')} ({enabled})
- **接口**: {pool.get('interface', 'N/A')}
- **地址池**: {pool.get('addr_pool', 'N/A')}
- **子网掩码**: {pool.get('netmask', 'N/A')}
- **网关**: {pool.get('gateway', 'N/A')}
- **DNS**: {pool.get('dns1', '')} / {pool.get('dns2', '')}
- **租约时间**: {pool.get('lease', 0)} 分钟
- **可用地址**: {pool.get('available', 'N/A')} 个
""")

    return "\n".join(lines)


# ========================== DHCP STATIC BINDINGS ===========================

class DhcpBindingsInput(BaseModel):
    """Input for getting DHCP static bindings."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_dhcp_bindings",
    annotations={
        "title": "Get DHCP Static Bindings",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_dhcp_bindings(params: DhcpBindingsInput, ctx: Context) -> str:
    """Get DHCP static IP-MAC bindings list.

    Returns:
        str: DHCP bindings in markdown or JSON format.
    """
    result = await _client.api_call("dhcp_addr_bind", "show", {
        "TYPE": "total,data",
        "limit": f"{params.offset},{params.limit}",
    })
    err = _check_error(result)
    if err:
        return err

    total = result.get("total", 0)
    data = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        return _json_result({"total": total, "count": len(data), "bindings": data})

    lines = [f"## DHCP 静态绑定 (共 {total} 条)\n"]
    lines.append("| # | IP 地址 | MAC 地址 | 备注 | 状态 |")
    lines.append("|---|---------|----------|------|------|")

    for i, b in enumerate(data, start=params.offset + 1):
        enabled = "✅" if b.get("enabled") == "yes" else "❌"
        lines.append(
            f"| {i} | {b.get('ip_addr', '')} | {b.get('mac', '')} "
            f"| {b.get('comment', '')} | {enabled} |"
        )

    return "\n".join(lines)


# ========================== DNS CONFIG =====================================

class DnsConfigInput(BaseModel):
    """Input for getting DNS service configuration."""
    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_dns_config",
    annotations={
        "title": "Get DNS Service Config",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_dns_config(params: DnsConfigInput, ctx: Context) -> str:
    """Get DNS service configuration, DNS proxy rules, and DNS cache stats.

    Returns:
        str: DNS config in markdown or JSON format.
    """
    config_result = await _client.api_call("dns", "show", {"TYPE": "dns_config"})
    proxy_result = await _client.api_call("dns", "show", {
        "TYPE": "dns_proxy_total,dns_proxy",
        "FINDS": "domain,dns_addr,src_addr,comment",
        "limit": "0,500",
    })

    err = _check_error(config_result) or _check_error(proxy_result)
    if err:
        return err

    if params.response_format == ResponseFormat.JSON:
        return _json_result({"config": config_result, "proxy": proxy_result})

    config = config_result.get("dns_config", {})
    proxies = proxy_result.get("dns_proxy", [])
    proxy_total = proxy_result.get("dns_proxy_total", 0)

    lines = [f"## DNS 服务配置\n"]

    if config:
        lines.append(f"- **DNS 配置**: {_json_result(config)}")

    lines.append(f"\n### DNS 代理规则 (共 {proxy_total} 条)\n")

    if proxies:
        lines.append("| # | 域名 | DNS 服务器 | 来源 | 备注 | 状态 |")
        lines.append("|---|------|-----------|------|------|------|")
        for i, p in enumerate(proxies, 1):
            enabled = "✅" if p.get("enabled") == "yes" else "❌"
            lines.append(
                f"| {i} | {p.get('domain', '')} | {p.get('dns_addr', '')} "
                f"| {p.get('src_addr', '')} | {p.get('comment', '')} | {enabled} |"
            )
    else:
        lines.append("暂无 DNS 代理规则。")

    return "\n".join(lines)


# ========================== ACL RULES ======================================

class AclRulesInput(BaseModel):
    """Input for getting ACL firewall rules."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_acl_rules",
    annotations={
        "title": "Get ACL Firewall Rules",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_acl_rules(params: AclRulesInput, ctx: Context) -> str:
    """Get Access Control List (ACL) firewall rules configured on the router.

    Returns:
        str: ACL rules in markdown or JSON format.
    """
    result = await _client.api_call("acl", "show", {
        "TYPE": "total,data",
        "limit": f"{params.offset},{params.limit}",
    })
    err = _check_error(result)
    if err:
        return err

    total = result.get("total", 0)
    data = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        return _json_result({"total": total, "count": len(data), "rules": data})

    lines = [f"## ACL 防火墙规则 (共 {total} 条)\n"]

    if not data:
        lines.append("暂无 ACL 规则。")
        return "\n".join(lines)

    lines.append("| # | 备注 | 协议 | 源地址 | 目标地址 | 动作 | 状态 |")
    lines.append("|---|------|------|--------|----------|------|------|")

    for i, r in enumerate(data, start=params.offset + 1):
        enabled = "✅" if r.get("enabled") == "yes" else "❌"
        lines.append(
            f"| {i} | {r.get('comment', '')} | {r.get('proto', 'any')} "
            f"| {r.get('src_addr', 'any')} | {r.get('dst_addr', 'any')} "
            f"| {r.get('action', '')} | {enabled} |"
        )

    return "\n".join(lines)


# ========================== TRAFFIC STATS ==================================

class TrafficStatsInput(BaseModel):
    """Input for getting traffic ranking statistics."""
    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_traffic_stats",
    annotations={
        "title": "Get Traffic Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_traffic_stats(params: TrafficStatsInput, ctx: Context) -> str:
    """Get traffic statistics: top devices by traffic and top applications/protocols.
    Uses the homepage traffic ranking data.

    Returns:
        str: Traffic ranking in markdown or JSON format.
    """
    result = await _client.api_call("homepage", "show", {
        "TYPE": "wan_stat,sysstat",
        "ifname": "adsl1",
        "interface": "adsl1",
    })
    err = _check_error(result)
    if err:
        return err

    # Also get the device list sorted by today_total desc for traffic ranking
    dev_result = await _client.api_call("monitor_lanip", "show", {
        "TYPE": "data,total",
        "limit": "0,20",
        "ORDER_BY": "today_total",
        "ORDER": "desc",
    })

    if params.response_format == ResponseFormat.JSON:
        stat = result.get("sysstat", {})
        stream = stat.get("stream", {})
        devices = dev_result.get("data", []) if not _check_error(dev_result) else []
        return _json_result({
            "stream": stream,
            "device_ranking": devices,
        })

    lines = ["## 流量统计\n"]

    stat = result.get("sysstat", {})
    stream = stat.get("stream", {})
    lines.append(f"**累计上行**: {_fmt_bytes(stream.get('total_up', 0))}")
    lines.append(f"**累计下行**: {_fmt_bytes(stream.get('total_down', 0))}")
    lines.append(f"**当前上行**: {_fmt_bytes(stream.get('upload', 0))}/s")
    lines.append(f"**当前下行**: {_fmt_bytes(stream.get('download', 0))}/s\n")

    # Device traffic ranking
    if not _check_error(dev_result):
        devices = dev_result.get("data", [])
        if devices:
            lines.append("### 终端流量排行 (TOP 20)\n")
            lines.append("| # | IP 地址 | 设备名 | 今日流量 | 连接数 |")
            lines.append("|---|---------|--------|----------|--------|")
            for i, d in enumerate(devices[:20], 1):
                name = d.get("comment") or d.get(
                    "termname") or d.get("hostname", "—")
                lines.append(
                    f"| {i} | {d.get('ip_addr', '')} | {name} "
                    f"| {_fmt_bytes(d.get('today_total', 0))} | {d.get('connect_num', 0)} |"
                )

    return "\n".join(lines)


# ========================== LAN INTERFACES =================================

class LanInfoInput(BaseModel):
    """Input for getting LAN/WAN interface configuration."""
    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_lan_info",
    annotations={
        "title": "Get LAN/WAN Interface Info",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_lan_info(params: LanInfoInput, ctx: Context) -> str:
    """Get LAN and WAN interface configuration including physical port mapping,
    network segments, and interface status.

    Returns:
        str: Interface info in markdown or JSON format.
    """
    ether_result = await _client.api_call("lan", "show", {
        "TYPE": "ether_info,snapshoot,wan_vlan_fail"
    })
    net_result = await _client.api_call("lan", "show", {
        "TYPE": "netinfo,snapshoot",
        "limit": "0,500",
    })

    err = _check_error(ether_result) or _check_error(net_result)
    if err:
        return err

    if params.response_format == ResponseFormat.JSON:
        return _json_result({"ether": ether_result, "network": net_result})

    lines = ["## 网络接口信息\n"]

    # Ether info
    ether = ether_result.get("ether_info", [])
    if ether:
        lines.append("### 物理端口\n")
        for e in ether:
            status = "🟢 连接" if e.get("link") == "up" else "🔴 断开"
            lines.append(
                f"- **{e.get('name', '')}** ({e.get('ifname', '')}): {status}, 速率 {e.get('speed', 'N/A')}")
        lines.append("")

    # Network info
    netinfo = net_result.get("netinfo", [])
    if netinfo:
        lines.append("### 网络段配置\n")
        for n in netinfo:
            lines.append(
                f"- **{n.get('interface', '')}**: {n.get('ip_addr', '')}/{n.get('netmask', '')} — {n.get('comment', '')}")

    return "\n".join(lines)


# ========================== DDNS CONFIG ====================================

class DdnsConfigInput(BaseModel):
    """Input for getting DDNS configuration."""
    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_ddns_config",
    annotations={
        "title": "Get DDNS Config",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_ddns_config(params: DdnsConfigInput, ctx: Context) -> str:
    """Get Dynamic DNS (DDNS) configuration including domain bindings,
    providers, and current resolution status.

    Returns:
        str: DDNS config in markdown or JSON format.
    """
    result = await _client.api_call("ddns", "show", {
        "TYPE": "total,data",
        "limit": "0,500",
    })
    err = _check_error(result)
    if err:
        return err

    total = result.get("total", 0)
    data = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        # Strip sensitive passwd field
        safe_data = []
        for d in data:
            entry = {k: v for k, v in d.items() if k != "passwd"}
            safe_data.append(entry)
        return _json_result({"total": total, "entries": safe_data})

    lines = [f"## DDNS 动态域名配置 (共 {total} 条)\n"]

    if not data:
        lines.append("暂无 DDNS 配置。")
        return "\n".join(lines)

    lines.append("| # | 域名 | 类型 | 服务商 | 接口 | 当前 IP | 状态 | 启用 |")
    lines.append("|---|------|------|--------|------|---------|------|------|")

    for i, d in enumerate(data, 1):
        enabled = "✅" if d.get("enabled") == "yes" else "❌"
        lines.append(
            f"| {i} | {d.get('domain', '')} | {d.get('type', '')} "
            f"| {d.get('server', '')} | {d.get('interface', '')} "
            f"| {d.get('ipaddress', '')} | {d.get('result', '')} | {enabled} |"
        )

    return "\n".join(lines)


# ========================== DOCKER STATUS ==================================

class DockerStatusInput(BaseModel):
    """Input for getting Docker status."""
    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_docker_status",
    annotations={
        "title": "Get Docker Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_docker_status(params: DockerStatusInput, ctx: Context) -> str:
    """Get Docker service status, overview, and disk usage on the router.

    Returns:
        str: Docker status in markdown or JSON format.
    """
    status_result = await _client.api_call("docker", "show", {"TYPE": "docker_status"})
    overview_result = await _client.api_call("docker_server", "show", {"TYPE": "overview"})
    disk_result = await _client.api_call("docker_server", "show", {"TYPE": "disks"})

    if params.response_format == ResponseFormat.JSON:
        return _json_result({
            "status": status_result,
            "overview": overview_result,
            "disks": disk_result,
        })

    lines = ["## Docker 状态\n"]

    # Status
    err = _check_error(status_result)
    if err:
        lines.append(f"状态查询失败: {err}")
    else:
        lines.append(
            f"**Docker 状态**: {_json_result(status_result.get('docker_status', status_result))}\n")

    # Overview
    err = _check_error(overview_result)
    if not err:
        lines.append(
            f"**概览**: {_json_result(overview_result.get('overview', overview_result))}\n")

    # Disks
    err = _check_error(disk_result)
    if not err:
        lines.append(
            f"**磁盘**: {_json_result(disk_result.get('disks', disk_result))}")

    return "\n".join(lines)


# ========================== VLAN CONFIG ====================================

class VlanConfigInput(BaseModel):
    """Input for getting VLAN configuration."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_vlan_config",
    annotations={
        "title": "Get VLAN Config",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_vlan_config(params: VlanConfigInput, ctx: Context) -> str:
    """Get VLAN configuration list.

    Returns:
        str: VLAN config in markdown or JSON format.
    """
    result = await _client.api_call("vlan", "show", {
        "TYPE": "total,data",
        "limit": f"{params.offset},{params.limit}",
    })
    err = _check_error(result)
    if err:
        return err

    if params.response_format == ResponseFormat.JSON:
        return _json_result(result)

    total = result.get("total", 0)
    data = result.get("data", [])

    lines = [f"## VLAN 配置 (共 {total} 条)\n"]

    if not data:
        lines.append("暂无 VLAN 配置。")
    else:
        for i, v in enumerate(data, 1):
            enabled = "✅" if v.get("enabled") == "yes" else "❌"
            lines.append(f"### VLAN {i} ({enabled})")
            for key, val in v.items():
                if key not in ("id",):
                    lines.append(f"- **{key}**: {val}")
            lines.append("")

    return "\n".join(lines)


# ========================== SYSTEM MONITOR HISTORY =========================

class SystemMonitorInput(BaseModel):
    """Input for getting system monitoring history data."""
    model_config = ConfigDict(extra="forbid")

    data_type: str = Field(
        default="cpu,memory,on_terminal,conn_num",
        description="Data types to query, comma-separated. Options: cpu, memory, disk_space_used, on_terminal, conn_num, cputemp1, cputemp2, rate_stat",
    )
    datetype: str = Field(
        default="hour",
        description="Time granularity: 'hour', 'day', or 'week'",
    )
    math: str = Field(
        default="avg",
        description="Aggregation: 'avg', 'max', or 'min'",
    )


@mcp.tool(
    name="ikuai_get_system_monitor",
    annotations={
        "title": "Get System Monitor History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_system_monitor(params: SystemMonitorInput, ctx: Context) -> str:
    """Get historical system monitoring data (CPU, memory, connection count,
    terminal count, temperature, speed). Useful for trend analysis.

    Args:
        params: Data type, time granularity, and aggregation method.

    Returns:
        str: Historical monitoring data in JSON format.
    """
    result = await _client.api_call("monitor_system", "show", {
        "TYPE": params.data_type,
        "datetype": params.datetype,
        "time_range": "",
        "start_time": "",
        "end_time": "",
        "math": params.math,
    })
    err = _check_error(result)
    if err:
        return err

    return _json_result(result)


# ========================== CONNECTION LIMITS ===============================

class ConnLimitInput(BaseModel):
    """Input for getting connection limit rules."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_conn_limits",
    annotations={
        "title": "Get Connection Limit Rules",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_conn_limits(params: ConnLimitInput, ctx: Context) -> str:
    """Get connection limit rules configured on the router.

    Returns:
        str: Connection limit rules in markdown or JSON format.
    """
    result = await _client.api_call("conn_limit", "show", {
        "TYPE": "total,data",
        "limit": f"{params.offset},{params.limit}",
    })
    err = _check_error(result)
    if err:
        return err

    if params.response_format == ResponseFormat.JSON:
        return _json_result(result)

    total = result.get("total", 0)
    data = result.get("data", [])

    lines = [f"## 连接数限制规则 (共 {total} 条)\n"]
    if not data:
        lines.append("暂无连接数限制规则。")
    else:
        for i, r in enumerate(data, 1):
            enabled = "✅" if r.get("enabled") == "yes" else "❌"
            lines.append(f"**{i}.** {r.get('comment', 'N/A')} ({enabled})")
            for key, val in r.items():
                if key not in ("id", "comment", "enabled"):
                    lines.append(f"  - {key}: {val}")
            lines.append("")

    return "\n".join(lines)


# ========================== FIREWALL STATUS ================================

class FirewallStatusInput(BaseModel):
    """Input for getting firewall status."""
    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="ikuai_get_firewall_status",
    annotations={
        "title": "Get Cloud Firewall Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_firewall_status(params: FirewallStatusInput, ctx: Context) -> str:
    """Get cloud firewall and domain blacklist status.

    Returns:
        str: Firewall status and blacklist rules.
    """
    fw_result = await _client.api_call("firewall", "show", {"TYPE": "status"})
    bl_result = await _client.api_call("domain_blacklist", "show", {
        "TYPE": "total,data", "limit": "0,100",
    })

    return _json_result({
        "firewall_status": fw_result,
        "domain_blacklist": bl_result,
    })


# ========================== VIRTUAL MACHINES ===============================

class QemuInput(BaseModel):
    """Input for getting virtual machine list."""
    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="ikuai_get_virtual_machines",
    annotations={
        "title": "Get Virtual Machines",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_virtual_machines(params: QemuInput, ctx: Context) -> str:
    """Get QEMU virtual machine list and status.

    Returns:
        str: Virtual machine data in JSON format.
    """
    result = await _client.api_call("qemu", "show", {
        "TYPE": "total,data", "limit": "0,500",
    })
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== BEHAVIOR RECORDS (AUDIT) =======================

class AuditUrlLogInput(BaseModel):
    """Input for querying URL browsing records."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT,
                       description="Max records to return")
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    order: str = Field(
        default="desc", description="Sort order: 'asc' or 'desc'")
    keywords: Optional[str] = Field(default=None,
                                    description="Search keyword to filter results (matches host, IP, MAC, comment, appname)")
    start_time: Optional[str] = Field(default=None,
                                      description="Start date filter, format 'YYYY-MM-DD'")
    end_time: Optional[str] = Field(default=None,
                                    description="End date filter, format 'YYYY-MM-DD'")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_url_records",
    annotations={
        "title": "Get URL Browsing Records",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_url_records(params: AuditUrlLogInput, ctx: Context) -> str:
    """Get URL browsing history records from the router's behavior audit log.
    Each record includes timestamp, device IP/MAC, visited URL/host, app name,
    and device type. Supports keyword search, date range filter, pagination.
    Useful for network usage analysis and security auditing.

    Args:
        params: Pagination, search keywords, date range, and format options.

    Returns:
        str: URL browsing records in markdown or JSON format.
    """
    param: Dict[str, Any] = {
        "TYPE": "data",
        "limit": f"{params.offset},{params.limit}",
        "ORDER_BY": "timestamp",
        "ORDER": params.order,
    }
    if params.keywords:
        param["FINDS"] = "host,ip_addr,mac,comment,appname"
        param["KEYWORDS"] = params.keywords
    if params.start_time:
        param["start_time"] = params.start_time
    if params.end_time:
        param["end_time"] = params.end_time

    result = await _client.api_call("audit_url_log", "show", param)
    err = _check_error(result)
    if err:
        return err

    data = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        return _json_result({"count": len(data), "offset": params.offset, "records": data})

    lines = [f"## 网址浏览记录 (返回 {len(data)} 条)\n"]
    lines.append("| # | 时间 | IP | 设备 | 应用 | 域名 |")
    lines.append("|---|------|----|----- |------|------|")

    for i, r in enumerate(data, start=params.offset + 1):
        from datetime import datetime
        ts = r.get("timestamp", 0)
        t = datetime.fromtimestamp(ts).strftime(
            "%m-%d %H:%M:%S") if ts else "N/A"
        name = r.get("comment") or r.get("client_type", "")
        host = r.get("host", "")
        if len(host) > 50:
            host = host[:50] + "..."
        lines.append(
            f"| {i} | {t} | {r.get('ip_addr', '')} | {name} "
            f"| {r.get('appname', '')} | {host} |"
        )

    if len(data) == params.limit:
        lines.append(
            f"\n> 可能有更多数据，使用 offset={params.offset + params.limit} 查看下一页")

    return "\n".join(lines)


class AuditImLogInput(BaseModel):
    """Input for querying IM (instant messaging) records."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    order: str = Field(default="desc")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_im_records",
    annotations={
        "title": "Get IM Chat Records",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_im_records(params: AuditImLogInput, ctx: Context) -> str:
    """Get instant messaging (IM) login/logout records. Tracks QQ, WeChat,
    and other IM app usage including account numbers and event types.
    Useful for network usage policy enforcement and auditing.

    Returns:
        str: IM records in markdown or JSON format.
    """
    result = await _client.api_call("audit_im_log", "show", {
        "TYPE": "data",
        "limit": f"{params.offset},{params.limit}",
        "ORDER_BY": "timestamp",
        "ORDER": params.order,
    })
    err = _check_error(result)
    if err:
        return err

    data = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        return _json_result({"count": len(data), "offset": params.offset, "records": data})

    lines = [f"## IM 即时通讯记录 (返回 {len(data)} 条)\n"]
    lines.append("| # | 时间 | IP | 设备 | IM类型 | 账号 | 事件 |")
    lines.append("|---|------|----|------|--------|------|------|")

    for i, r in enumerate(data, start=params.offset + 1):
        t = r.get("date_time", "N/A")
        name = r.get("comment") or r.get("client_type", "")
        lines.append(
            f"| {i} | {t} | {r.get('ip', '')} | {name} "
            f"| {r.get('im_type', '')} | {r.get('account', '')} | {r.get('event', '')} |"
        )

    return "\n".join(lines)


class AuditTerminalLogInput(BaseModel):
    """Input for querying terminal online/offline records."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    order: str = Field(default="desc")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_terminal_records",
    annotations={
        "title": "Get Terminal Online/Offline Records",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_terminal_records(params: AuditTerminalLogInput, ctx: Context) -> str:
    """Get terminal (device) online/offline history records. Each record includes
    connect time, disconnect time, online duration, upload/download traffic,
    device type and model. Essential for device usage pattern analysis.

    Returns:
        str: Terminal records in markdown or JSON format.
    """
    result = await _client.api_call("audit_terminal_log", "show", {
        "TYPE": "data",
        "limit": f"{params.offset},{params.limit}",
        "ORDER_BY": "timestamp",
        "ORDER": params.order,
    })
    err = _check_error(result)
    if err:
        return err

    data = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        return _json_result({"count": len(data), "offset": params.offset, "records": data})

    lines = [f"## 终端上下线记录 (返回 {len(data)} 条)\n"]
    lines.append("| # | 上线时间 | IP | 设备 | 在线时长 | 上行 | 下行 | 系统 | 型号 |")
    lines.append(
        "|---|---------|----|----- |---------|------|------|------|------|")

    for i, r in enumerate(data, start=params.offset + 1):
        t = r.get("date_time", "N/A")
        name = r.get("comment") or r.get("termname") or "—"
        online_sec = r.get("online_time", 0)
        if online_sec >= 3600:
            duration = f"{online_sec // 3600}时{(online_sec % 3600) // 60}分"
        elif online_sec >= 60:
            duration = f"{online_sec // 60}分{online_sec % 60}秒"
        else:
            duration = f"{online_sec}秒"
        lines.append(
            f"| {i} | {t} | {r.get('ip_addr', '')} | {name} "
            f"| {duration} | {_fmt_bytes(r.get('total_up', 0))} "
            f"| {_fmt_bytes(r.get('total_down', 0))} "
            f"| {r.get('systype', '')} | {r.get('devtype', '')} |"
        )

    if len(data) == params.limit:
        lines.append(
            f"\n> 可能有更多数据，使用 offset={params.offset + params.limit} 查看下一页")

    return "\n".join(lines)


class AuditConfigInput(BaseModel):
    """Input for getting audit configuration."""
    model_config = ConfigDict(extra="forbid")


@mcp.tool(
    name="ikuai_get_audit_config",
    annotations={
        "title": "Get Audit Config",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_audit_config(params: AuditConfigInput, ctx: Context) -> str:
    """Get behavior audit configuration: which record types are enabled,
    storage usage and capacity. Useful for checking audit system status.

    Returns:
        str: Audit config in JSON format.
    """
    result = await _client.api_call("audit", "show", {})
    err = _check_error(result)
    if err:
        return err

    data = result.get("data", [{}])[0] if result.get("data") else {}

    return _json_result({
        "url_record_enabled": bool(data.get("open_url_record")),
        "im_record_enabled": bool(data.get("open_im_record")),
        "terminal_record_enabled": bool(data.get("open_terminal_record")),
        "appid_record_enabled": bool(data.get("open_appid_record")),
        "storage_total_kb": data.get("total_size"),
        "storage_used_kb": data.get("use_size"),
        "storage_usage_pct": round(data.get("use_size", 0) / max(data.get("total_size", 1), 1) * 100, 1),
    })


# ========================== SYSTEM LOGS (syslog-xxx) =======================

class SyslogInput(BaseModel):
    """Input for querying system logs."""
    model_config = ConfigDict(extra="forbid")

    log_type: str = Field(
        default="sysevent",
        description=(
            "Log type. Available types: "
            "'sysevent' (系统事件), 'dhcpd' (DHCP日志), 'ddns' (动态域名日志), "
            "'wanpppoe' (外网拨号日志), 'notice' (推送通知), "
            "'pppauth' (PPP认证/用户日志), 'arp' (ARP日志), 'apaction' (无线终端日志)"
        ),
    )
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    filter_field: Optional[str] = Field(default=None,
                                        description="Field name to filter on (e.g., 'interface')")
    filter_value: Optional[str] = Field(default=None,
                                        description="Filter value (e.g., 'adsl1'). Used with filter_field as: FILTER1='field,==,value'")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="ikuai_get_syslog",
    annotations={
        "title": "Get System Logs",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_syslog(params: SyslogInput, ctx: Context) -> str:
    """Get system logs from the router's log center. Covers system events,
    DHCP leases, DDNS updates, WAN PPPoE dial events, ARP events,
    push notifications, PPP auth, and wireless AP events.

    Log types and their fields:
    - sysevent: timestamp, content, id (system events like link detection)
    - dhcpd: timestamp, id, mac, event, msgtype, ip_addr, interface
    - ddns: timestamp, id, ip_addr, interface, event, domain, result
    - wanpppoe: timestamp, id, content, interface (supports filter by interface)
    - pppauth: timestamp, content, id
    - arp: timestamp, content, id (ARP spoofing detection)
    - notice: timestamp, content, id
    - apaction: timestamp, content, id

    Args:
        params: Log type, pagination, optional field filter.

    Returns:
        str: Log entries in markdown or JSON format.
    """
    func_name = f"syslog-{params.log_type}"
    param: Dict[str, Any] = {
        "TYPE": "total,data",
        "ORDER": "desc",
        "ORDER_BY": "timestamp",
        "limit": f"{params.offset},{params.limit}",
    }
    if params.filter_field and params.filter_value:
        param["FILTER1"] = f"{params.filter_field},==,{params.filter_value}"

    result = await _client.api_call(func_name, "show", param)
    err = _check_error(result)
    if err:
        return err

    total = result.get("total", 0)
    data = result.get("data", [])

    if params.response_format == ResponseFormat.JSON:
        return _json_result({
            "log_type": params.log_type,
            "func_name": func_name,
            "total": total,
            "count": len(data),
            "offset": params.offset,
            "records": data,
        })

    lines = [f"## 系统日志 — {func_name} (共 {total} 条, 返回 {len(data)} 条)\n"]

    if not data:
        lines.append("暂无日志记录。")
        return "\n".join(lines)

    # Adaptive column display based on available fields
    sample = data[0]
    from datetime import datetime

    if "msgtype" in sample:
        # DHCP log format
        lines.append("| # | 时间 | 类型 | MAC | IP | 接口 |")
        lines.append("|---|------|------|-----|----|------|")
        for i, r in enumerate(data, start=params.offset + 1):
            ts = r.get("timestamp", 0)
            t = datetime.fromtimestamp(ts).strftime(
                "%m-%d %H:%M:%S") if ts else "N/A"
            lines.append(
                f"| {i} | {t} | {r.get('msgtype', '')} | {r.get('mac', '')} "
                f"| {r.get('ip_addr', '')} | {r.get('interface', '')} |"
            )
    elif "domain" in sample:
        # DDNS log format
        lines.append("| # | 时间 | 域名 | IP | 事件 | 结果 | 接口 |")
        lines.append("|---|------|------|----|----- |------|------|")
        for i, r in enumerate(data, start=params.offset + 1):
            ts = r.get("timestamp", 0)
            t = datetime.fromtimestamp(ts).strftime(
                "%m-%d %H:%M:%S") if ts else "N/A"
            lines.append(
                f"| {i} | {t} | {r.get('domain', '')} | {r.get('ip_addr', '')} "
                f"| {r.get('event', '')} | {r.get('result', '')} | {r.get('interface', '')} |"
            )
    else:
        # Generic content-based log format
        lines.append("| # | 时间 | 内容 |")
        lines.append("|---|------|------|")
        for i, r in enumerate(data, start=params.offset + 1):
            ts = r.get("timestamp", 0)
            t = datetime.fromtimestamp(ts).strftime(
                "%m-%d %H:%M:%S") if ts else "N/A"
            content = r.get("content", "")
            iface = r.get("interface", "")
            if iface:
                content = f"[{iface}] {content}"
            lines.append(f"| {i} | {t} | {content} |")

    if total > params.offset + len(data):
        lines.append(f"\n> 还有更多日志，使用 offset={params.offset + len(data)} 查看下一页")

    return "\n".join(lines)


class WarningInput(BaseModel):
    """Input for querying system warnings."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)


@mcp.tool(
    name="ikuai_get_warnings",
    annotations={
        "title": "Get System Warnings",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_get_warnings(params: WarningInput, ctx: Context) -> str:
    """Get system warning/alert messages and warning level statistics.

    Returns:
        str: Warnings and level stats in JSON format.
    """
    level_result = await _client.api_call("warning", "show", {"TYPE": "level_total"})
    data_result = await _client.api_call("warning", "show", {
        "TYPE": "total,data",
        "ORDER": "desc",
        "ORDER_BY": "timestamp",
        "limit": f"{params.offset},{params.limit}",
    })

    return _json_result({
        "level_stats": level_result,
        "warnings": data_result,
    })


# ========================== QOS / TERMINAL SPEED LIMIT ====================

class SimpleQosInput(BaseModel):
    """Input for getting terminal speed limit rules."""
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

@mcp.tool(name="ikuai_get_simple_qos", annotations={"title": "Get Terminal Speed Limit Rules", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_simple_qos(params: SimpleQosInput, ctx: Context) -> str:
    """Get terminal speed limit (QoS) rules."""
    result = await _client.api_call("simple_qos", "show", {"TYPE": "total,data", "limit": f"{params.offset},{params.limit}"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== FLOW CONTROL ==================================

class FlowControlInput(BaseModel):
    """Input for getting smart flow control rules."""
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

@mcp.tool(name="ikuai_get_flow_control", annotations={"title": "Get Smart Flow Control Rules", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_flow_control(params: FlowControlInput, ctx: Context) -> str:
    """Get smart flow control (bandwidth management) rules."""
    result = await _client.api_call("flow_control", "show", {"TYPE": "total,data", "limit": f"{params.offset},{params.limit}"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== LOAD BALANCING ================================

class LoadBalanceInput(BaseModel):
    """Input for getting load balancing / traffic diversion rules."""
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

@mcp.tool(name="ikuai_get_load_balance", annotations={"title": "Get Load Balance / Diversion Rules", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_load_balance(params: LoadBalanceInput, ctx: Context) -> str:
    """Get load balancing and traffic diversion policy rules."""
    result = await _client.api_call("lb_pcc", "show", {"TYPE": "total,data", "limit": f"{params.offset},{params.limit}"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== UPNP / NAT ====================================

class UpnpInput(BaseModel):
    """Input for getting UPnP/NAT port mapping status."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_upnp", annotations={"title": "Get UPnP/NAT Port Mappings", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_upnp(params: UpnpInput, ctx: Context) -> str:
    """Get UPnP/NAT port mapping configuration and status."""
    result = await _client.api_call("upnpd", "show", {"TYPE": "ifconf_data,ifconf_total"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== MAC ACCESS CONTROL ============================

class AclMacInput(BaseModel):
    """Input for getting MAC access control rules."""
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

@mcp.tool(name="ikuai_get_acl_mac", annotations={"title": "Get MAC Access Control Rules", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_acl_mac(params: AclMacInput, ctx: Context) -> str:
    """Get MAC address access control rules."""
    result = await _client.api_call("acl_mac", "show", {"TYPE": "total,data", "limit": f"{params.offset},{params.limit}"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== URL REDIRECT ==================================

class UrlRedirectInput(BaseModel):
    """Input for getting URL redirect/control rules."""
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

@mcp.tool(name="ikuai_get_url_redirect", annotations={"title": "Get URL Control Rules", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_url_redirect(params: UrlRedirectInput, ctx: Context) -> str:
    """Get URL redirect and control rules."""
    result = await _client.api_call("url_redirect", "show", {"TYPE": "total,data", "limit": f"{params.offset},{params.limit}"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== APP PROTOCOL CONTROL ==========================

class MacAppInput(BaseModel):
    """Input for getting application protocol control rules."""
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

@mcp.tool(name="ikuai_get_mac_app", annotations={"title": "Get App Protocol Control Rules", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_mac_app(params: MacAppInput, ctx: Context) -> str:
    """Get application protocol control and parental control rules."""
    result = await _client.api_call("mac_app", "show", {"TYPE": "total,data", "limit": f"{params.offset},{params.limit}"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== TERMINAL NAME MANAGEMENT ======================

class MacCommentInput(BaseModel):
    """Input for getting terminal name (comment) management list."""
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

@mcp.tool(name="ikuai_get_mac_comment", annotations={"title": "Get Terminal Name List", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_mac_comment(params: MacCommentInput, ctx: Context) -> str:
    """Get terminal name (MAC comment) management list."""
    result = await _client.api_call("mac_comment", "show", {"TYPE": "total,data", "limit": f"{params.offset},{params.limit}"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== ROUTE OBJECTS =================================

class RouteObjectInput(BaseModel):
    """Input for getting route objects (time groups and IP groups)."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_route_objects", annotations={"title": "Get Route Objects", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_route_objects(params: RouteObjectInput, ctx: Context) -> str:
    """Get route objects including time groups and IP groups."""
    time_result = await _client.api_call("route_object", "show", {"TYPE": "timegroup"})
    ip_result = await _client.api_call("route_object", "show", {"TYPE": "ipgroup"})
    return _json_result({
        "timegroup": time_result if not _check_error(time_result) else {"error": _check_error(time_result)},
        "ipgroup": ip_result if not _check_error(ip_result) else {"error": _check_error(ip_result)},
    })


# ========================== SD-WAN ========================================

class SdwanInput(BaseModel):
    """Input for getting SD-WAN status."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_sdwan", annotations={"title": "Get SD-WAN Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_sdwan(params: SdwanInput, ctx: Context) -> str:
    """Get SD-WAN configuration and binding status."""
    result = await _client.api_call("ik_web_sdwan", "show", {"TYPE": "data,bind_status"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== SYSTEM BACKUP =================================

class BackupInput(BaseModel):
    """Input for getting system backup configuration."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_backup", annotations={"title": "Get System Backup Config", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_backup(params: BackupInput, ctx: Context) -> str:
    """Get system backup configuration and schedules."""
    result = await _client.api_call("backup", "show", {"TYPE": "total,data"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== FTP SERVER ====================================

class FtpServerInput(BaseModel):
    """Input for getting FTP server status."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_ftp_server", annotations={"title": "Get FTP Server Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_ftp_server(params: FtpServerInput, ctx: Context) -> str:
    """Get FTP server status and shared directory list."""
    status_result = await _client.api_call("ftp_server", "show", {"TYPE": "ftp_status"})
    data_result = await _client.api_call("ftp_server", "show", {"TYPE": "total,data"})
    return _json_result({
        "status": status_result if not _check_error(status_result) else {"error": _check_error(status_result)},
        "shares": data_result if not _check_error(data_result) else {"error": _check_error(data_result)},
    })


# ========================== CROSS-LAYER SERVICE ===========================

class AclL2routeInput(BaseModel):
    """Input for getting cross-layer (L2 route) service config."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_acl_l2route", annotations={"title": "Get Cross-Layer Service Config", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_acl_l2route(params: AclL2routeInput, ctx: Context) -> str:
    """Get cross-layer (L2 route) service configuration."""
    result = await _client.api_call("acl_l2route", "show", {"TYPE": "data"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== MULTICAST / IGMP ==============================

class IgmpProxyInput(BaseModel):
    """Input for getting IGMP proxy (multicast) config."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_igmp_proxy", annotations={"title": "Get Multicast/IGMP Config", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_igmp_proxy(params: IgmpProxyInput, ctx: Context) -> str:
    """Get IGMP proxy (multicast management) configuration."""
    result = await _client.api_call("igmp_proxy", "show", {"TYPE": "data,lan_interface,wan_interface"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== PPPOE SERVER ==================================

class PppoeServerInput(BaseModel):
    """Input for getting PPPoE server config."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_pppoe_server", annotations={"title": "Get PPPoE Server Config", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_pppoe_server(params: PppoeServerInput, ctx: Context) -> str:
    """Get PPPoE server configuration."""
    result = await _client.api_call("pppoe_server", "show", {"TYPE": "data"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== WEB AUTH ======================================

class WebAuthInput(BaseModel):
    """Input for getting web authentication service config."""
    model_config = ConfigDict(extra="forbid")

@mcp.tool(name="ikuai_get_webauth", annotations={"title": "Get Web Auth Service Config", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@_tool_logged
async def ikuai_get_webauth(params: WebAuthInput, ctx: Context) -> str:
    """Get web authentication service configuration."""
    result = await _client.api_call("webauth", "show", {"TYPE": "data"})
    err = _check_error(result)
    if err:
        return err
    return _json_result(result)


# ========================== RAW API CALL ===================================

class RawApiInput(BaseModel):
    """Input for making a raw API call to the iKuai router."""
    model_config = ConfigDict(extra="forbid")

    func_name: str = Field(
        ...,
        description="API function name (e.g., 'homepage', 'monitor_lanip', 'dhcp_server', 'dns', 'acl', 'lan')",
        min_length=1,
    )
    action: str = Field(
        default="show",
        description="API action: 'show' for read, 'add', 'del', 'up', 'down' for modifications",
    )
    param: Optional[Dict[str, Any]] = Field(
        default=None,
        description="API parameters as a JSON object. Example: {\"TYPE\": \"sysstat\"} or {\"TYPE\": \"data,total\", \"limit\": \"0,100\"}",
    )


@mcp.tool(
    name="ikuai_raw_api_call",
    annotations={
        "title": "Raw API Call",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
@_tool_logged
async def ikuai_raw_api_call(params: RawApiInput, ctx: Context) -> str:
    """Make a raw API call to the iKuai router. Use this for any API function
    not covered by other specific tools. Supports all iKuai API endpoints.

    All confirmed func_names (iKuai 4.0.120-beta, 45+ endpoints):

    System & Monitoring:
    - homepage: sysstat, wan_stat, wan_speed, ac_status
    - monitor_lanip: data,total (with pagination/sorting), remote_addr
    - monitor_system: cpu,memory,on_terminal,conn_num,cputemp1,cputemp2 (with datetype/math), rate_stat
    - faststart: register
    - register: data

    Network Config:
    - lan: ether_info,snapshoot,wan_vlan_fail / netinfo,snapshoot / stream
    - vlan: total,data
    - simple_qos: total,data
    - flow_control: total,data
    - lb_pcc: total,data (load balancing / diversion)
    - route_object: timegroup / ipgroup
    - acl_l2route: data (cross-layer service)
    - igmp_proxy: data,lan_interface,wan_interface
    - dhcp_server: total,data
    - dhcp_addr_bind: total,data
    - dns: dns_config / dns_cache,dns_cache_total / dns_proxy_total,dns_proxy
    - ik_web_sdwan: data,bind_status (with method:local_info)
    - upnpd: ifconf_data,ifconf_total
    - ddns: total,data

    Security:
    - acl: total,data
    - acl_mac: total,data / acl_mac
    - conn_limit: total,data
    - firewall: status
    - domain_blacklist: total,data
    - url_redirect: total,data
    - mac_app: parental_mode / total,data
    - mac_comment: total,data

    Behavior Audit:
    - audit: {} (config: open_url_record, open_im_record, open_terminal_record, storage stats)
    - audit_url_log: data (URL browsing records, supports FINDS/KEYWORDS search, start_time/end_time)
    - audit_im_log: data (IM login/logout records)
    - audit_terminal_log: data (device online/offline records with traffic stats)

    System Logs (syslog-xxx series, all use TYPE:'total,data' with ORDER/ORDER_BY):
    - syslog-sysevent: system events (link detection, etc.)
    - syslog-dhcpd: DHCP request logs (mac, msgtype, ip_addr, interface)
    - syslog-ddns: DDNS update logs (domain, ip_addr, event, result)
    - syslog-wanpppoe: WAN PPPoE dial logs (supports FILTER1:'interface,==,adsl1')
    - syslog-pppauth: PPP authentication logs
    - syslog-arp: ARP event logs (spoofing detection)
    - syslog-notice: push notification logs
    - syslog-apaction: wireless AP action logs
    - warning: system warnings (also supports TYPE:'level_total' for stats)

    Advanced Services:
    - ftp_server: ftp_status / total,data
    - docker: docker_status
    - docker_server: overview / disks
    - qemu: total,data
    - backup: total,data
    - pppoe_server: data
    - webauth: data

    Args:
        params: func_name, action, and param dict.

    Returns:
        str: JSON-formatted API response.
    """
    result = await _client.api_call(params.func_name, params.action, params.param)
    return _json_result(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--http" in sys.argv:
        port = 8000
        ssl_certfile = None
        ssl_keyfile = None
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
            elif arg == "--ssl-certfile" and i + 1 < len(sys.argv):
                ssl_certfile = sys.argv[i + 1]
            elif arg == "--ssl-keyfile" and i + 1 < len(sys.argv):
                ssl_keyfile = sys.argv[i + 1]
        mcp.settings.port = port
        if ssl_certfile and ssl_keyfile:
            import uvicorn
            app = mcp.streamable_http_app()
            uvicorn.run(
                app,
                host=mcp.settings.host,
                port=port,
                ssl_certfile=ssl_certfile,
                ssl_keyfile=ssl_keyfile,
            )
        else:
            mcp.run(transport="streamable-http")
    else:
        mcp.run()
