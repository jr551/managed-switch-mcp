# managed-switch-mcp

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server that lets an LLM client query an OEM Realtek-based managed switch shipped on AliExpress as the **GT-ST018M** (and clones with the same web UI).

It speaks to the device over plain HTTP, logs in with the OEM's quirky MD5 challenge, scrapes the CGI pages, and exposes the data as a tidy set of MCP tools — without ever changing a single config value on the switch.

> ⚠️ The switch ships with **HTTP only** and a **default admin/admin** credential. Treat it as untrusted-network gear. See [`RESEARCH.md`](./RESEARCH.md) for what we learned poking at it.

## Why "read-only"?

The original ask was: *probe and look around, but don't break my network*. So every tool here either:

1. Issues a plain `GET` to a CGI page, or
2. Issues a `POST` whose **only** side-effect is on the current HTTP session's display state (e.g. setting the MAC table page size to 30).

No tool ever writes config, reboots, factory-resets, modifies users, or applies anything. The dangerous endpoints (`/reboot.cgi`, `/reset.cgi`, `/fwug.cgi`, `/save.cgi`, `/user.cgi` POST, `/ip.cgi` POST, port-state changes, VLAN changes…) are deliberately **not** wired up.

## Tools

| Tool | What it returns |
|---|---|
| `get_device_info` | Model, firmware, IP/MAC, uptime, port link table |
| `list_ports` | Per-port state, duplex (config + actual), speed, flow control |
| `get_port_statistics` | TX/RX packet & byte counters (64-bit, decoded from the firmware's `hi-lo` quirk) |
| `get_mac_table` | All learned MAC entries (walks paginated view) |
| `get_vlan_list` | Static VLANs + global VLAN-management state |
| `get_port_vlan_config` | Access/trunk per port, native VLAN, allowed trunk VLANs |
| `get_port_aggregation` | LACP / static trunk groups |
| `get_port_mirror` | Active SPAN configurations |
| `get_port_isolation` | Per-port isolation matrix |
| `get_port_rate_limits` | Ingress/egress rate limits per port |
| `get_loop_protocol` | Loop prevention / STP mode + timers |
| `get_stp_global` | STP/RSTP globals & root info |
| `get_stp_port` | Per-port STP state, role, path cost, P2P, edge |
| `get_qos_port_queue` | Port → egress-queue mapping |
| `get_qos_queue_weights` | Strict-priority / WRR weights |
| `get_igmp_config` | IGMP snooping enable, router ports, learned entries |
| `get_dhcp_snooping` | DHCP-snooping enable state |
| `get_storm_control` | Per-port storm-control rates |
| `get_jumbo_frame` | Jumbo-frame enable + MTU |
| `get_ip_config` | Management IP / mask / gateway / DHCP mode |
| `get_users` | Configured admin usernames (never passwords) |
| `backup_config` | Download the binary `switch_cfg.bin` (base64 in response, or save to disk) |

## Install

```bash
git clone https://github.com/<you>/managed-switch-mcp.git
cd managed-switch-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Configure

Set environment variables (or copy `.env.example` to `.env` and source it):

```bash
export SWITCH_HOST=192.168.16.3
export SWITCH_USERNAME=admin
export SWITCH_PASSWORD=admin
export SWITCH_TIMEOUT=10
```

## Run

The server speaks MCP over stdio:

```bash
.venv/bin/managed-switch-mcp
```

### Claude Desktop / Claude Code config

```json
{
  "mcpServers": {
    "managed-switch": {
      "command": "/absolute/path/to/managed-switch-mcp/.venv/bin/managed-switch-mcp",
      "env": {
        "SWITCH_HOST": "192.168.16.3",
        "SWITCH_USERNAME": "admin",
        "SWITCH_PASSWORD": "admin"
      }
    }
  }
}
```

### Quick smoke test

```bash
.venv/bin/python -m managed_switch_mcp.tests.smoke
```

(see `src/managed_switch_mcp/tests/smoke.py` — calls every tool against the live switch and pretty-prints the result.)

## How the auth works

The login page computes `md5(username + password)` in JavaScript, drops it into a cookie called `admin`, *also* POSTs it as the `Response` form field, and submits the credentials in cleartext to `/login.cgi`. The cookie alone is sufficient for subsequent requests. This client replicates that behaviour and silently re-logs in if a request comes back as the login redirect.

## Layout

```
src/managed_switch_mcp/
├── client.py     # auth + HTTP session
├── parsers.py    # BeautifulSoup parsers, one per CGI page
├── server.py     # FastMCP server + tool definitions
└── tests/smoke.py
```

## License

MIT — see [`LICENSE`](./LICENSE).
