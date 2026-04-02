# iKuai API 非官方参考文档

本文档为社区整理的爱快 (iKuai) 路由器 Web 管理界面 API 参考，基于浏览器网络请求观察记录。
基于 iKuai OS 4.0.120-beta x64 免费版。

## 概述

爱快路由器使用统一的 HTTP JSON API，所有操作通过两个端点完成：

| 端点 | 用途 |
|------|------|
| `POST /Action/login` | 登录认证，获取 session cookie |
| `POST /Action/call` | 所有业务 API 调用 |
| `POST /Action/message` | 消息/通知轮询（长轮询） |

## 认证

### 登录请求

```
POST /Action/login
Content-Type: application/json

{
  "username": "admin",
  "passwd": "<md5_hash_of_password>",
  "pass": "<md5_hash_of_password>"
}
```

- 密码需要先做 MD5 哈希
- 成功后服务器通过 `Set-Cookie` 返回 `sess_key`
- 后续请求自动携带该 cookie
- Session 会超时，需要定期重新登录

### 登录响应

```json
{
  "Result": 10000,
  "ErrMsg": "Success"
}
```

## 统一 API 格式

### 请求格式

```json
{
  "func_name": "模块名称",
  "action": "操作类型",
  "param": {
    "TYPE": "数据类型",
    ...其他参数
  }
}
```

### action 类型

| action | 用途 |
|--------|------|
| `show` | 查询/读取数据 |
| `add` | 新增条目 |
| `del` | 删除条目 |
| `up` | 修改/更新条目 |
| `down` | 禁用条目（部分模块） |

### 响应格式

```json
{
  "code": 0,
  "message": "Success",
  "results": {
    // 业务数据
  }
}
```

- `code: 0` 表示成功
- `code: 非0` 表示失败，`message` 字段包含错误信息

### 分页参数

列表类接口支持分页：

```json
{
  "param": {
    "TYPE": "data,total",
    "limit": "offset,count",
    "ORDER_BY": "字段名",
    "ORDER": "asc|desc"
  }
}
```

- `limit`: 格式为 `"起始位置,数量"`，如 `"0,100"` 表示从第 0 条开始取 100 条
- `ORDER_BY`: 排序字段
- `ORDER`: `asc` 升序 / `desc` 降序

---

## API 模块详解

### 1. homepage — 系统概览

#### 1.1 系统状态 (sysstat)

```json
{
  "func_name": "homepage",
  "action": "show",
  "param": {
    "TYPE": "sysstat"
  }
}
```

响应示例：

```json
{
  "sysstat": {
    "hostname": "iKuai",
    "ip_addr": "192.168.1.1",
    "uptime": 851517,
    "cpu": ["9.98%", "11.11%", "12.87%", "10.89%", "5.94%"],
    "cputemp": [44],
    "freq": ["1919", "1745", "2089", "2049"],
    "memory": {
      "total": 7964272,
      "available": 6587660,
      "free": 6626916,
      "cached": 396696,
      "buffers": 77392,
      "used": "17%"
    },
    "online_user": {
      "count": 16,
      "count_wired": 16,
      "count_wireless": 0,
      "count_2g": 0,
      "count_5g": 0
    },
    "stream": {
      "connect_num": 542,
      "tcp_connect_num": 444,
      "udp_connect_num": 88,
      "icmp_connect_num": 9,
      "upload": 181116,
      "download": 231459,
      "total_up": 191374789174,
      "total_down": 514575602188
    },
    "verinfo": {
      "verstring": "4.0.120-beta x64 Build202603191336",
      "version": "4.0.120",
      "build_date": 202603191336,
      "arch": "x86",
      "sysbit": "x64",
      "is_enterprise": 0
    }
  }
}
```

字段说明：
- `cpu`: 数组，每个元素是一个核心的使用率
- `cputemp`: 数组，CPU 温度（摄氏度）
- `memory.total / available / free`: 单位 KB
- `memory.used`: 百分比字符串
- `stream.upload / download`: 当前速率，单位 Bytes/s
- `stream.total_up / total_down`: 累计流量，单位 Bytes
- `uptime`: 运行时间，单位秒

#### 1.2 WAN 线路状态 (wan_stat)

```json
{
  "func_name": "homepage",
  "action": "show",
  "param": {
    "TYPE": "wan_stat",
    "ifname": "adsl1",
    "interface": "adsl1"
  }
}
```

响应示例：

```json
{
  "wan_stat": {
    "interface": "adsl1",
    "parent_interface": "wan1",
    "ip_addr": "203.0.113.10",
    "gateway": "203.0.113.1",
    "internet": "PPPOE",
    "isp": "CTCC",
    "rtt": "7.99",
    "total": 73565185443,
    "upload": 394.27,
    "download": 231.49,
    "errmsg": "线路检测成功",
    "result": "success"
  }
}
```

字段说明：
- `isp`: ISP 代码（CTCC=中国电信, CUCC=中国联通, CMCC=中国移动）
- `rtt`: 延迟，单位 ms
- `upload / download`: 当前速率，单位 KB/s
- `total`: 累计流量，单位 Bytes

#### 1.3 WAN 实时速率 (wan_speed)

```json
{
  "func_name": "homepage",
  "action": "show",
  "param": {
    "TYPE": "wan_speed",
    "interface": "adsl1"
  }
}
```

#### 1.4 组合查询

可以用逗号分隔多个 TYPE 一次性查询：

```json
{
  "func_name": "homepage",
  "action": "show",
  "param": {
    "TYPE": "wan_stat,wan_speed,sysstat,ac_status",
    "ifname": "adsl1",
    "interface": "adsl1"
  }
}
```

---

### 2. monitor_lanip — 终端监控

#### 2.1 在线终端列表

```json
{
  "func_name": "monitor_lanip",
  "action": "show",
  "param": {
    "TYPE": "data,total",
    "limit": "0,500",
    "ORDER_BY": "ip_addr_int",
    "ORDER": "asc"
  }
}
```

可用排序字段：`ip_addr_int`, `connect_num`, `upload`, `download`, `total_up`, `total_down`, `today_total`

响应示例（单个设备）：

```json
{
  "interface": "lan1",
  "ip_addr": "192.168.1.12",
  "ip_addr_int": 3232237836,
  "mac": "aa:bb:cc:dd:ee:01",
  "connect_num": 228,
  "client_type": "Android",
  "comment": "飞牛os",
  "termname": "",
  "hostname": "",
  "upload": 0,
  "download": 0,
  "total_up": 78705047886,
  "total_down": 71663384695,
  "today_total": 150368374596,
  "uptime": "2026-03-23 15:29:57",
  "uplink_dev": "iKuai",
  "uplink_addr": "aa:bb:cc:dd:ee:02",
  "static_status": 0,
  "auth_type": 0,
  "ssid": "",
  "signal": 0,
  "channel": "--"
}
```

字段说明：
- `ip_addr_int`: IP 地址的整数表示，用于排序
- `client_type`: 设备类型识别（Android, iOS, Windows 等）
- `comment`: 用户自定义备注名
- `upload / download`: 实时速率 Bytes/s
- `total_up / total_down`: 累计流量 Bytes
- `today_total`: 今日累计流量 Bytes
- `uptime`: 上线时间
- `static_status`: 是否有静态绑定
- `ssid`: 连接的 WiFi SSID（有线设备为空）
- `signal`: WiFi 信号强度

#### 2.2 远程地址

```json
{
  "func_name": "monitor_lanip",
  "action": "show",
  "param": {
    "TYPE": "remote_addr"
  }
}
```

---

### 3. lan — 内外网设置

#### 3.1 物理端口信息

```json
{
  "func_name": "lan",
  "action": "show",
  "param": {
    "TYPE": "ether_info,snapshoot,wan_vlan_fail"
  }
}
```

#### 3.2 网段信息

```json
{
  "func_name": "lan",
  "action": "show",
  "param": {
    "TYPE": "netinfo,snapshoot",
    "limit": "0,500"
  }
}
```

#### 3.3 流量统计

```json
{
  "func_name": "lan",
  "action": "show",
  "param": {
    "TYPE": "stream"
  }
}
```

---

### 4. dhcp_server — DHCP 服务

#### 4.1 DHCP 地址池

```json
{
  "func_name": "dhcp_server",
  "action": "show",
  "param": {
    "TYPE": "total,data",
    "limit": "0,500"
  }
}
```

响应示例：

```json
{
  "total": 1,
  "data": [
    {
      "id": 1,
      "enabled": "yes",
      "tagname": "DHS_1",
      "interface": "lan1",
      "addr_pool": "192.168.1.100-192.168.1.200",
      "netmask": "255.255.255.0",
      "gateway": "192.168.1.1",
      "dns1": "223.5.5.5",
      "dns2": "223.6.6.6",
      "lease": 120,
      "available": 93
    }
  ]
}
```

#### 4.2 DHCP 静态绑定

```json
{
  "func_name": "dhcp_addr_bind",
  "action": "show",
  "param": {
    "TYPE": "total,data",
    "limit": "0,500"
  }
}
```

---

### 5. dns — DNS 服务

#### 5.1 DNS 配置

```json
{
  "func_name": "dns",
  "action": "show",
  "param": {
    "TYPE": "dns_config"
  }
}
```

#### 5.2 DNS 缓存

```json
{
  "func_name": "dns",
  "action": "show",
  "param": {
    "TYPE": "dns_cache,dns_cache_total"
  }
}
```

#### 5.3 DNS 代理规则

```json
{
  "func_name": "dns",
  "action": "show",
  "param": {
    "TYPE": "dns_proxy_total,dns_proxy",
    "FINDS": "domain,dns_addr,src_addr,comment",
    "limit": "0,500"
  }
}
```

---

### 6. acl — ACL 防火墙规则

```json
{
  "func_name": "acl",
  "action": "show",
  "param": {
    "TYPE": "total,data",
    "limit": "0,500"
  }
}
```

---

### 7. 行为记录（审计日志）— 用于二次数据分析

行为记录是爱快的核心数据分析模块，包含三类日志，均支持分页、排序、时间范围筛选和关键词搜索。

**审计配置查询**：

```json
{
  "func_name": "audit",
  "action": "show",
  "param": {}
}
```

响应示例：

```json
{
  "data": [{
    "open_url_record": 1,
    "open_im_record": 1,
    "open_terminal_record": 1,
    "open_appid_record": 0,
    "total_size": 4046560,
    "use_size": 970364
  }]
}
```

#### 7.1 audit_url_log — 网址浏览记录

```json
{
  "func_name": "audit_url_log",
  "action": "show",
  "param": {
    "TYPE": "data",
    "limit": "0,500",
    "ORDER_BY": "timestamp",
    "ORDER": "desc"
  }
}
```

支持搜索筛选（FINDS 指定搜索字段，KEYWORDS 指定关键词）：

```json
{
  "func_name": "audit_url_log",
  "action": "show",
  "param": {
    "TYPE": "data",
    "limit": "0,100",
    "FINDS": "host,ip_addr,mac,comment,appname",
    "KEYWORDS": "apple",
    "ORDER_BY": "timestamp",
    "ORDER": "desc"
  }
}
```

支持日期范围：

```json
{
  "param": {
    "TYPE": "data",
    "limit": "0,500",
    "start_time": "2026-04-01",
    "end_time": "2026-04-02"
  }
}
```

响应字段：

| 字段 | 说明 |
|------|------|
| `id` | 记录 ID |
| `timestamp` | Unix 时间戳 |
| `ip_addr` | 终端 IP |
| `mac` | 终端 MAC |
| `host` | 访问的域名/URL |
| `uri` | URI 路径 |
| `comment` | 设备备注名 |
| `appname` | 应用/协议名称（如 iCloud、SSL、微信消息） |
| `icon` | 图标 ID |
| `client_model` | 设备型号 |
| `client_type` | 设备系统类型（iOS、Android 等） |

**注意**：`audit_url_log` 不支持 `TYPE: "total,data"` 中的 `total`（会返回 2007 错误），仅支持 `TYPE: "data"`。

#### 7.2 audit_im_log — IM 即时通讯记录

```json
{
  "func_name": "audit_im_log",
  "action": "show",
  "param": {
    "TYPE": "data",
    "limit": "0,500",
    "ORDER_BY": "timestamp",
    "ORDER": "desc"
  }
}
```

响应字段：

| 字段 | 说明 |
|------|------|
| `id` | 记录 ID |
| `timestamp` | Unix 时间戳 |
| `date_time` | 人类可读时间 |
| `im_type` | IM 类型（QQ、微信等） |
| `account` | IM 账号 |
| `event` | 事件（登录、登出等） |
| `ip` | 终端 IP |
| `mac` | 终端 MAC |
| `comment` | 设备备注名 |
| `client_type` | 设备系统类型 |
| `client_model` | 设备型号 |
| `icon` | 图标 ID |

#### 7.3 audit_terminal_log — 终端上下线记录

```json
{
  "func_name": "audit_terminal_log",
  "action": "show",
  "param": {
    "TYPE": "data",
    "limit": "0,500",
    "ORDER_BY": "timestamp",
    "ORDER": "desc"
  }
}
```

响应字段：

| 字段 | 说明 |
|------|------|
| `id` | 记录 ID |
| `timestamp` | 上线时间（Unix） |
| `date_time` | 上线时间（人类可读） |
| `logout_time` | 下线时间（Unix） |
| `online_time` | 在线时长（秒） |
| `ip_addr` | 终端 IP |
| `mac` | 终端 MAC |
| `total_up` | 本次上行流量（Bytes） |
| `total_down` | 本次下行流量（Bytes） |
| `today_total` | 今日累计流量 |
| `auth` | 认证状态 |
| `systype` | 系统类型（Android、iOS 等） |
| `devtype` | 设备品牌（Redmi、Samsung 等） |
| `client_model` | 设备型号 |
| `comment` | 设备备注名 |
| `termname` | 终端名称 |
| `username` | 认证用户名 |
| `vlan_id` | VLAN ID |
| `ipv4_gnames` / `mac_gnames` | 所属分组名 |

---

### 8. 日志中心 — syslog-xxx 系列

日志中心使用 `syslog-` 前缀的 func_name，这是爱快独特的命名格式。

所有 syslog API 统一参数格式：

```json
{
  "func_name": "syslog-xxx",
  "action": "show",
  "param": {
    "TYPE": "total,data",
    "ORDER": "desc",
    "ORDER_BY": "timestamp",
    "limit": "0,500"
  }
}
```

支持 `FILTER1` 过滤：

```json
{
  "param": {
    "TYPE": "total,data",
    "ORDER": "desc",
    "ORDER_BY": "timestamp",
    "limit": "0,500",
    "FILTER1": "interface,==,adsl3"
  }
}
```

#### 8.1 已确认的 syslog func_name

| func_name | 所属菜单 | 描述 | 响应字段 |
|-----------|---------|------|----------|
| `syslog-pppauth` | 用户日志 > PPP认证 | PPPoE 用户认证日志 | timestamp, content, id |
| `syslog-arp` | 用户日志 > ARP | ARP 事件（含欺骗检测） | timestamp, content, id |
| `syslog-apaction` | 用户日志 > 无线终端 | AP/无线终端上下线 | timestamp, content, id |
| `syslog-dhcpd` | 功能日志 > DHCP日志 | DHCP 请求日志 | timestamp, id, mac, event, msgtype, ip_addr, interface |
| `syslog-ddns` | 功能日志 > 动态域名日志 | DDNS 更新记录 | timestamp, id, ip_addr, interface, event, domain, result |
| `syslog-wanpppoe` | 功能日志 > 外网拨号日志 | WAN PPPoE 拨号事件 | timestamp, id, content, interface |
| `syslog-notice` | 功能日志 > 推送通知日志 | 系统推送通知 | timestamp, content, id |
| `syslog-sysevent` | 系统日志 | 系统事件（线路检测等） | timestamp, content, id |
| `warning` | 告警信息 | 系统告警（支持 `TYPE: "level_total"` 查看告警级别统计） | timestamp, content, id |

#### 8.2 syslog-dhcpd 响应示例

```json
{
  "mac": "aa:bb:cc:dd:ee:03",
  "event": "--",
  "id": 311164,
  "timestamp": 1775107425,
  "msgtype": "DHCPREQUEST",
  "ip_addr": "192.168.1.105",
  "interface": "lan1"
}
```

#### 8.3 syslog-ddns 响应示例

```json
{
  "ip_addr": "203.0.113.50",
  "id": 6323,
  "timestamp": 1775071752,
  "interface": "adsl3",
  "event": "更新成功",
  "domain": "sub.example.com",
  "result": "成功"
}
```

---

## 通用查询参数汇总

| 参数 | 说明 | 适用范围 |
|------|------|----------|
| `TYPE` | 数据类型（逗号分隔多个） | 所有 API |
| `limit` | 分页 `"offset,count"` | 列表类 API |
| `ORDER_BY` | 排序字段 | 列表类 API |
| `ORDER` | `asc` / `desc` | 列表类 API |
| `FINDS` | 搜索字段（逗号分隔） | audit_url_log, monitor_lanip 等 |
| `KEYWORDS` | 搜索关键词 | 配合 FINDS 使用 |
| `FILTER1` | 精确过滤 `"字段,==,值"` | syslog 系列 |
| `start_time` / `end_time` | 时间范围 `"YYYY-MM-DD"` | audit 系列 |
| `datetype` | 时间粒度 `hour/day/week` | monitor_system |
| `math` | 聚合方式 `avg/max/min` | monitor_system |

---

## 路由器管理界面模块全景

以下是从 iKuai 4.0.120 版本 Web 界面中提取的完整模块树：

```
├── 系统概览
├── 监控中心
│   ├── 线路监控
│   │   ├── 线路监控 (tab)
│   │   ├── 线路状态检测 (tab)
│   │   └── IPv6 线路详情 (tab)
│   ├── 无线监控
│   ├── 终端监控
│   ├── 行为洞察
│   ├── 策略监控
│   ├── 负载监控
│   ├── 分流监控
│   └── 下联设备
├── 网络配置
│   ├── 内外网设置
│   │   ├── 内外网设置 (tab)
│   │   ├── IPv6 设置 (tab)
│   │   └── VPN 客户端 (tab)
│   ├── VLAN 设置
│   ├── 智能流控
│   ├── 终端限速
│   ├── 分流策略
│   ├── 静态路由
│   ├── 跨三层服务
│   ├── 路由对象
│   ├── 自定义协议
│   ├── 组播管理
│   ├── DHCP 服务
│   ├── DNS 服务
│   ├── SD-WAN
│   └── UPnP/NAT
├── 无线服务
├── 安全中心
│   ├── ACL 规则
│   ├── 连接数限制
│   ├── 云防火墙
│   ├── 威胁情报库
│   ├── ARP 设置
│   ├── MAC 访问控制
│   ├── 网址浏览控制
│   ├── URL 控制
│   ├── 应用协议控制
│   ├── 其他控制
│   ├── 行为记录
│   ├── 流量审计
│   ├── 终端名称管理
│   └── 高级设置
├── 认证服务
├── 高级服务
│   ├── 本地服务
│   ├── 内网穿透
│   ├── 工具包
│   ├── 应用市场
│   ├── Docker
│   └── 虚拟机
├── 日志中心
├── 设备设置
└── 消息通知
```

## 完整 func_name 速查表（36 个已确认）

以下所有 func_name 均通过实际页面抓包或 API 调用验证，在 iKuai 4.0.120-beta 免费版上可用。

### 系统 & 监控

| func_name | 对应模块 | 已知 TYPE 参数 |
|-----------|----------|----------------|
| `homepage` | 系统概览 | `sysstat`, `wan_stat`, `wan_speed`, `ac_status`（可逗号组合） |
| `monitor_lanip` | 终端监控 | `data,total`（支持分页排序）, `remote_addr` |
| `monitor_system` | 设备设置/系统监控 | `cputemp_support`, `cpu,memory,disk_space_used,on_terminal,conn_num,cputemp1,cputemp2`（支持 datetype/math）, `rate_stat` |
| `faststart` | 快捷入口/引导 | `register` |
| `register` | 云服务绑定 | `data` |

### 网络配置

| func_name | 对应模块 | 已知 TYPE 参数 |
|-----------|----------|----------------|
| `lan` | 内外网设置 | `ether_info,snapshoot,wan_vlan_fail`, `netinfo,snapshoot`, `stream` |
| `vlan` | VLAN 设置 | `total,data` |
| `simple_qos` | 智能流控/终端限速 | `total,data` |
| `flow_control` | 智能流控（高级） | `total,data` |
| `lb_pcc` | 分流策略/负载均衡 | `total,data` |
| `route_object` | 路由对象 | `timegroup`, `ipgroup` |
| `acl_l2route` | 跨三层服务 | `data` |
| `igmp_proxy` | 组播管理 | `data,lan_interface,wan_interface` |
| `dhcp_server` | DHCP 服务 | `total,data` |
| `dhcp_addr_bind` | DHCP 静态绑定 | `total,data` |
| `dns` | DNS 服务 | `dns_config`, `dns_cache,dns_cache_total`, `dns_proxy_total,dns_proxy` |
| `ik_web_sdwan` | SD-WAN | `data,bind_status`（含 method:'local_info'） |
| `upnpd` | UPnP/NAT | `ifconf_data,ifconf_total` |
| `ddns` | DDNS 动态域名 | `total,data` |

### 安全中心

| func_name | 对应模块 | 已知 TYPE 参数 |
|-----------|----------|----------------|
| `acl` | ACL 规则 | `total,data` |
| `acl_mac` | MAC 访问控制 | `total,data`, `acl_mac` |
| `conn_limit` | 连接数限制 | `total,data` |
| `firewall` | 云防火墙 | `status` |
| `domain_blacklist` | 网址黑名单 | `total,data` |
| `url_redirect` | URL 控制 | `total,data` |
| `mac_app` | 应用协议控制/其他控制 | `parental_mode`, `total,data` |
| `audit` | 行为记录/流量审计 | `{}`（空参数） |
| `audit_url_log` | 流量审计日志 | `data` |
| `mac_comment` | 终端名称管理 | `total,data` |

### 高级服务

| func_name | 对应模块 | 已知 TYPE 参数 |
|-----------|----------|----------------|
| `ftp_server` | 本地服务 (FTP) | `ftp_status`, `total,data` |
| `docker` | Docker | `docker_status` |
| `docker_server` | Docker 服务 | `overview`, `disks` |
| `qemu` | 虚拟机 | `total,data` |
| `backup` | 系统备份 | `total,data` |
| `pppoe_server` | PPPoE 服务 | `data`（已确认存在） |
| `webauth` | 认证服务 | `data`（已确认存在） |

### monitor_system 详细参数

`monitor_system` 支持历史数据查询，参数格式如下：

```json
{
  "func_name": "monitor_system",
  "action": "show",
  "param": {
    "TYPE": "cpu,memory,disk_space_used,on_terminal,conn_num,cputemp1,cputemp2",
    "datetype": "hour",
    "time_range": "",
    "start_time": "",
    "end_time": "",
    "math": "avg"
  }
}
```

- `datetype`: `hour` / `day` / `week`
- `math`: `avg` / `max` / `min`
- `TYPE` 还支持 `rate_stat`（速率统计）

### ddns 响应字段

```json
{
  "type": "A",
  "id": 1,
  "enabled": "yes",
  "ipaddress": "x.x.x.x",
  "tagname": "DD_1",
  "server": "cloudflare.com",
  "top_domain": "example.com",
  "domain": "sub.example.com",
  "interface": "adsl3",
  "result": "成功"
}
```

### backup 响应字段

```json
{
  "valid_days": 30,
  "id": 1,
  "tagname": "BK_1",
  "strategy": "...",
  "enabled": "yes",
  "cycle_time": "...",
  "time": "..."
}
```

### docker_server 响应

```json
// TYPE: "overview" — Docker 总览
// TYPE: "disks" — Docker 存储磁盘信息
```

### ftp_server 响应

```json
// TYPE: "ftp_status" — FTP 服务运行状态
// TYPE: "total,data" — FTP 共享目录列表
```

## 注意事项

1. **Session 管理**: Cookie 会超时，建议每 10 分钟检查一次 session 有效性
2. **并发**: 爱快路由器的 Web 服务器并发能力有限，建议串行调用 API
3. **密码**: 登录时密码需要 MD5 哈希，不要传明文
4. **免费版 vs 企业版**: 部分功能（如终端识别、行为洞察高级功能）仅企业版支持
5. **版本差异**: 不同版本的 iKuai OS 可能有 API 差异，本文档基于 4.0.120-beta