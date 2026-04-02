# iKuai Router MCP Server (爱快路由器 MCP 服务)

一个用于通过 MCP (Model Context Protocol) 与爱快路由器交互的服务，让 Claude 能够直接查询和管理你的路由器。

## 功能

| 工具名 | 描述 |
|--------|------|
| `ikuai_get_system_status` | 获取系统状态：CPU、内存、温度、在线终端数、连接数、运行时间、固件版本 |
| `ikuai_get_wan_info` | 获取 WAN 线路信息：IP、网关、ISP、延迟、速率 |
| `ikuai_get_online_devices` | 列出在线终端：IP、MAC、设备名、流量、连接数（支持分页和排序）|
| `ikuai_get_dhcp_config` | 获取 DHCP 地址池配置 |
| `ikuai_get_dhcp_bindings` | 获取 DHCP 静态绑定列表 |
| `ikuai_get_dns_config` | 获取 DNS 配置和代理规则 |
| `ikuai_get_acl_rules` | 获取 ACL 防火墙规则 |
| `ikuai_get_traffic_stats` | 获取流量统计和终端排行 |
| `ikuai_get_lan_info` | 获取 LAN/WAN 接口和物理端口信息 |
| `ikuai_get_ddns_config` | 获取 DDNS 动态域名配置和状态 |
| `ikuai_get_docker_status` | 获取 Docker 服务状态、概览和磁盘信息 |
| `ikuai_get_vlan_config` | 获取 VLAN 配置列表 |
| `ikuai_get_system_monitor` | 获取历史监控数据（CPU/内存/连接数趋势） |
| `ikuai_get_conn_limits` | 获取连接数限制规则 |
| `ikuai_get_firewall_status` | 获取云防火墙和网址黑名单状态 |
| `ikuai_get_virtual_machines` | 获取 QEMU 虚拟机列表和状态 |
| `ikuai_get_url_records` | 获取网址浏览记录（支持关键词搜索、时间范围、分页） |
| `ikuai_get_im_records` | 获取 IM 即时通讯登录/登出记录（QQ、微信等） |
| `ikuai_get_terminal_records` | 获取终端上下线记录（含在线时长、流量统计） |
| `ikuai_get_audit_config` | 获取行为审计配置和存储使用情况 |
| `ikuai_get_syslog` | 获取系统日志（支持 8 种日志类型：系统事件、DHCP、DDNS、拨号、ARP 等） |
| `ikuai_get_warnings` | 获取系统告警信息和告警级别统计 |
| `ikuai_get_simple_qos` | 获取终端限速 (QoS) 规则 |
| `ikuai_get_flow_control` | 获取智能流控规则 |
| `ikuai_get_load_balance` | 获取分流策略/负载均衡规则 |
| `ikuai_get_upnp` | 获取 UPnP/NAT 端口映射状态 |
| `ikuai_get_acl_mac` | 获取 MAC 访问控制规则 |
| `ikuai_get_url_redirect` | 获取 URL 控制规则 |
| `ikuai_get_mac_app` | 获取应用协议控制规则 |
| `ikuai_get_mac_comment` | 获取终端名称管理列表 |
| `ikuai_get_route_objects` | 获取路由对象（时间组、IP 组） |
| `ikuai_get_sdwan` | 获取 SD-WAN 配置和绑定状态 |
| `ikuai_get_backup` | 获取系统备份配置 |
| `ikuai_get_ftp_server` | 获取 FTP 服务状态和共享目录 |
| `ikuai_get_acl_l2route` | 获取跨三层服务配置 |
| `ikuai_get_igmp_proxy` | 获取组播管理 (IGMP) 配置 |
| `ikuai_get_pppoe_server` | 获取 PPPoE 服务配置 |
| `ikuai_get_webauth` | 获取认证服务配置 |
| `ikuai_raw_api_call` | 原始 API 调用（支持所有 45+ 个已确认的爱快 API 端点）|

## 安装

```bash
pip install -r requirements.txt
```

## 配置

设置环境变量：

```bash
export IKUAI_URL="http://192.168.9.1:81"   # 路由器完整 URL
export IKUAI_USERNAME="admin"               # 管理员用户名
export IKUAI_PASSWORD="your_password"       # 管理员密码
```

## 运行

### stdio 模式（本地，推荐用于 Claude Desktop / Claude Code）

```bash
python ikuai_mcp.py
```

### HTTP 模式（远程访问）

```bash
python ikuai_mcp.py --http                  # 默认端口 8000
python ikuai_mcp.py --http --port 9000      # 自定义端口
```

### HTTPS 模式（带 TLS 证书）

```bash
python ikuai_mcp.py --http --port 8443 --ssl-certfile cert.pem --ssl-keyfile key.pem
```

## Claude Desktop 配置

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "ikuai": {
      "command": "python",
      "args": ["/path/to/ikuai_mcp.py"],
      "env": {
        "IKUAI_URL": "http://192.168.9.1:81",
        "IKUAI_USERNAME": "admin",
        "IKUAI_PASSWORD": "your_password"
      }
    }
  }
}
```

> **提示**：建议将 `command` 设为 Python 的绝对路径（如 `C:\\Python314\\python.exe`），避免 Claude Desktop 找不到正确的 Python 环境。

### Streamable HTTP 模式

先启动服务，然后在配置中指定 URL：

```json
{
  "mcpServers": {
    "ikuai": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

## Claude Code 配置

```bash
claude mcp add ikuai -- python /path/to/ikuai_mcp.py
```

需要先设置好环境变量 `IKUAI_URL`、`IKUAI_USERNAME`、`IKUAI_PASSWORD`。

## API 原理

爱快路由器提供统一的 HTTP JSON API：

- **端点**: `POST {IKUAI_URL}/Action/call`
- **认证**: `POST {IKUAI_URL}/Action/login` 获取 session cookie
- **请求格式**:
  ```json
  {
    "func_name": "模块名",
    "action": "show",
    "param": { "TYPE": "数据类型", ... }
  }
  ```
- **响应格式**:
  ```json
  {
    "code": 0,
    "message": "Success",
    "results": { ... }
  }
  ```

### 已知 func_name 列表

| func_name | 描述 |
|-----------|------|
| `homepage` | 系统概览 (sysstat, wan_stat, wan_speed, ac_status) |
| `monitor_lanip` | 终端监控 |
| `lan` | 内外网设置 (ether_info, netinfo, stream) |
| `dhcp_server` | DHCP 服务器 |
| `dhcp_addr_bind` | DHCP 静态绑定 |
| `dns` | DNS 服务 (dns_config, dns_cache, dns_proxy) |
| `acl` | ACL 防火墙规则 |
| `domain_blacklist` | 网址黑名单 |
| `mac_bind` | MAC 访问控制 |
| `static_routing` | 静态路由 |
| `port_mapping` | 端口映射 |
| `flow_control` | 智能流控 |
| `docker` | Docker 管理 |

## 许可证

MIT License