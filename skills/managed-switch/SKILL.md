---
name: goodtop-switch
description: Inspect and configure a GoodTop GT-ST018M (or compatible Realtek-SDK based AliExpress) managed switch via the goodtop-switch MCP server. Use when the user asks about port status, MAC table / who is plugged in where, VLANs, STP/loop prevention, traffic counters, IGMP / DHCP snooping, jumbo frames, link aggregation, port mirroring, rate limits, storm control, the management IP, backing up the config — or when they want to *change* any of those things on the switch.
---

# GoodTop Switch

This skill drives a GoodTop GT-ST018M (8×2.5GbE + 1×10G SFP+, sold by
GoodTop on AliExpress / Newegg, ODM'd by Shenzhen HongRui) via the
`managed-switch` MCP server. The server exposes ~22 read tools and ~28
write tools, all over the OEM HTTP web UI.

## When to invoke

- User mentions "the switch", "the managed switch", "192.168.16.3" (the
  default mgmt IP), GT-ST018M, or specific switch concepts (VLAN, MAC table,
  STP, port mirror, LACP, IGMP snooping, jumbo frames, port channel).
- User wants to know who is plugged in where, see traffic counters, find a
  MAC, or audit VLAN assignments.
- User wants to configure any of the above — but read the **safety rules**
  below before calling any `set_*` / `add_*` / `delete_*` / `clear_*` tool.

If the MCP server isn't connected (tools missing), tell the user to start
it — see `README.md` in the `managed-switch-mcp` repo.

## Tool catalogue

### Read (always safe to call)

`get_device_info`, `list_ports`, `get_port_statistics`, `get_mac_table`,
`get_vlan_list`, `get_port_vlan_config`, `get_port_aggregation`,
`get_port_mirror`, `get_port_isolation`, `get_port_rate_limits`,
`get_loop_protocol`, `get_stp_global`, `get_stp_port`,
`get_qos_port_queue`, `get_qos_queue_weights`, `get_igmp_config`,
`get_dhcp_snooping`, `get_storm_control`, `get_jumbo_frame`,
`get_ip_config`, `get_users`, `backup_config`.

### Write — normal admin operations (apply after confirming intent)

Port: `set_port_settings`, `set_port_rate_limit`, `set_port_isolation`,
`set_port_mirror`, `delete_port_mirror`, `clear_port_statistics`.

VLAN: `add_vlan`, `delete_vlan`, `set_vlan_management_enabled`,
`set_port_vlan_access`, `set_port_vlan_trunk`.

LAG: `create_port_aggregation`, `delete_port_aggregation`.

L2: `add_static_mac`, `delete_static_mac`,
`set_loop_protocol`, `set_stp_global`, `set_stp_port`.

QoS / multicast / sec: `set_port_priority_queue`, `set_queue_weight`,
`set_igmp_enabled`, `set_igmp_static_router_ports`,
`set_dhcp_snooping_enabled`, `set_storm_control`,
`set_jumbo_frame_enabled`, `set_jumbo_frame_size`.

Identity: `set_device_name`.

System: `save_running_config`, `reboot_device` (confirm="reboot").

### Write — dangerous (gated behind `SWITCH_ALLOW_DANGEROUS=1`)

`clear_mac_table`, `set_ip_config`, `change_admin_password`.

Factory reset and firmware upload are deliberately **not** exposed — do those
through the web UI on the user's screen.

## Safety rules

1. **Default to read tools.** When the user asks an open question ("how's
   the switch?", "what's connected?", "is anything weird?"), answer with
   reads. Don't volunteer to change config.
2. **Confirm before any write.** Before calling any `set_*` / `add_*` /
   `delete_*` / `clear_*` tool, restate exactly what will change and which
   port/VLAN/feature it touches, and wait for explicit user confirmation.
   This is non-negotiable for `reboot_device`, anything that touches the
   management VLAN or its port, and anything LACP / STP / VLAN related.
3. **Reads before writes.** Before applying a port/VLAN change, call the
   matching read tool to see the *current* state. Surface the diff.
4. **Save when finished.** After a successful set of changes the user is
   happy with, offer `save_running_config` so the changes survive a power
   cycle. Don't auto-save mid-session.
5. **Never use the dangerous tools** unless the user has explicitly named
   the operation in this conversation. The env-var gate is a safety net,
   not a license.

## Common workflows

### "Where is `<MAC>` plugged in?"

1. `get_mac_table` → search for the MAC.
2. If found, the `port` field maps to the visible label (e.g. "3", "Trunk1"
   — the firmware drops the "Port " prefix in this table).
3. Cross-reference with `get_port_vlan_config` to give VLAN context.

### "What's plugged into port N?"

1. `get_mac_table` → entries with `port == "<N>"`.
2. `list_ports` → confirm the port is up.
3. `get_port_statistics` → if user wants traffic levels.

### "Audit my VLAN config"

1. `get_vlan_list` (statically defined VLANs).
2. `get_port_vlan_config` (per-port assignments).
3. Optionally cross-reference `get_mac_table` to find MACs assigned to
   unexpected VLANs.

### "Cap port X at Y Mbps"

1. `get_port_rate_limits` to show current.
2. Confirm with user: "I'll set Port X ingress=Y kbps egress=Y kbps; OK?"
3. `set_port_rate_limit(ports=["Port X"], ingress_kbps=Y*1000, egress_kbps=Y*1000)`.
4. Re-read to confirm.
5. Offer `save_running_config`.

### "Take a config backup"

`backup_config(save_path="~/Desktop/switch_cfg_<date>.bin")` — drops a
binary on disk. Note that the password is stored obfuscated in the blob;
treat the file as a credential.

## Port naming convention

The firmware exposes 9 physical ports + Trunk1. Use exactly these strings
in tool arguments: `"Port 1"`, `"Port 2"`, …, `"Port 9"`, `"Trunk1"`.

- Ports 1 & 2 are bonded into the default `Trunk1` and don't appear in most
  read tables.
- Port 9 is the 10G SFP+ cage. Its allowed speeds differ from the others
  (`Auto/100M/1000M/2500M/10G` vs. `Auto/10M/100M/1000M/2500M`) and it lives
  in a separate form section — call port-config tools on Port 9 *alone*,
  not bundled with other ports.

## If something goes wrong

- Symptom: tool returns "Login rejected" → the credentials in the MCP
  server env are wrong. The user needs to update `SWITCH_*` env vars and
  restart Claude Code.
- Symptom: tool hangs / connection refused → the switch is unreachable
  from this machine. Have the user confirm the LAN connection and
  management IP via `ping <SWITCH_HOST>`.
- Symptom: 404 on a CGI page → the firmware version differs from
  V200.1.8 (the version this server was reverse-engineered against). File
  an issue with the failing tool name and the user's firmware version
  (`get_device_info` → `firmware_version`).
