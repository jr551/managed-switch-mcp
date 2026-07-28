# goodtop-switch-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets an LLM client read and configure a **GoodTop GT-ST018M** managed switch — an 8×2.5GbE + 1×10G-SFP+ box sold on AliExpress / Newegg under the GoodTop brand, ODM'd by Shenzhen HongRui (OUI `1C:2A:A3`). The same Realtek-SDK-style web UI ships on a few clones, so this server may work against those too — issues welcome.

It speaks to the device over plain HTTP, logs in with the OEM's quirky MD5 challenge, scrapes the CGI pages for reads, and posts the same form bodies the web UI uses for writes.

Ships with **22 read tools** and **28 write tools**. Three catastrophic write operations are gated behind an env var; factory reset and firmware upload are not exposed at all.

> ⚠️ The switch ships with **HTTP only** and a **default admin/admin** credential. Treat it as untrusted-network gear. See [`RESEARCH.md`](./RESEARCH.md) for what we learned poking at it.

## Safety model

The original ask was: *probe and look around, but don't break my network*. Writes were added later and are designed so that an LLM *cannot* brick the switch by accident:

- **Read tools** issue only `GET` requests, or `POST`s whose only effect is session-scoped display state (e.g. MAC-table pagination).
- **Normal write tools** mirror what a clued-up admin would do on the web UI — port speed, VLANs, STP, QoS, IGMP, jumbo frames, static MACs, LAGs, save, reboot. `reboot_device` requires `confirm="reboot"`.
- **Dangerous writes** (`clear_mac_table`, `set_ip_config`, `change_admin_password`) are gated behind `SWITCH_ALLOW_DANGEROUS=1` *and* an exact-match `confirm` arg. By default they refuse with `PermissionError`.
- **Factory reset and firmware upload are not exposed at all.** Use the web UI for those.

## Tools

### Read

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

### Write (normal)

| Tool | What it does |
|---|---|
| `set_device_name` | Rename the switch (1–32 chars) |
| `set_port_settings` | Apply state/duplex/speed/flow to one or more ports |
| `clear_port_statistics` | Zero every port's TX/RX counters |
| `set_port_rate_limit` | Cap port bandwidth in Kbit/sec |
| `set_storm_control` | Per-port storm-control rates |
| `set_port_isolation` | Deny forwarding from `ports` to `isolated_from` |
| `set_port_mirror`, `delete_port_mirror` | Configure / remove a SPAN session |
| `set_loop_protocol` | Off / Loop Detection / Spanning Tree + timers |
| `set_stp_global`, `set_stp_port` | STP parameters |
| `set_port_priority_queue`, `set_queue_weight` | QoS |
| `set_igmp_enabled`, `set_igmp_static_router_ports` | IGMP snooping |
| `set_dhcp_snooping_enabled` | DHCP snooping toggle |
| `set_jumbo_frame_enabled`, `set_jumbo_frame_size` | Jumbo frames |
| `add_static_mac`, `delete_static_mac` | Static MAC entries |
| `set_vlan_management_enabled`, `add_vlan`, `delete_vlan` | VLAN definitions |
| `set_port_vlan_access`, `set_port_vlan_trunk` | Per-port VLAN assignment |
| `create_port_aggregation`, `delete_port_aggregation` | LAGs |
| `save_running_config` | Persist current config to flash |
| `reboot_device` | Reboot (`confirm="reboot"`) |

### Write (dangerous — gated behind `SWITCH_ALLOW_DANGEROUS=1`)

| Tool | Why it's gated |
|---|---|
| `clear_mac_table` | Briefly drops everything until tables relearn |
| `set_ip_config` | Wrong settings → you can no longer reach the switch |
| `change_admin_password` | Wrong settings → you can no longer log in |

All three additionally require an exact-match `confirm` argument.

## Install

```bash
git clone https://github.com/jr551/goodtop-switch-mcp.git
cd goodtop-switch-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Configure

Set environment variables (or copy `.env.example` to `.env` and source it):

```bash
export SWITCH_HOST=192.0.2.1
export SWITCH_USERNAME=admin
export SWITCH_PASSWORD=admin
export SWITCH_TIMEOUT=10
```

`192.0.2.1` above is a documentation placeholder (RFC 5737 TEST-NET-1), not a
real device — set `SWITCH_HOST` to your switch's actual management IP. Keep
real values in a local `.env` (see `.env.example`); it's gitignored and
should never be committed. `admin`/`admin` is the switch vendor's documented
factory-default credential, not a secret — consider changing it via
`change_admin_password` (gated behind `SWITCH_ALLOW_DANGEROUS=1`) once you've
confirmed the server works.

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
        "SWITCH_HOST": "192.0.2.1",
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
├── parsers.py    # BeautifulSoup parsers, one per CGI page (reads)
├── writes.py     # form builders for every write endpoint
├── server.py     # FastMCP server + tool definitions
└── tests/smoke.py

skills/managed-switch/SKILL.md   # Claude Code skill that drives these tools
```

## Claude Code skill

There's also a [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills) bundled at
`skills/managed-switch/SKILL.md` that documents when to invoke the tools and how
to use them safely (read-before-write, confirm before applying, save after
finishing). Install it with:

```bash
mkdir -p ~/.claude/skills/managed-switch
cp skills/managed-switch/SKILL.md ~/.claude/skills/managed-switch/
```

## License

MIT — see [`LICENSE`](./LICENSE).
