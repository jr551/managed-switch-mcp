"""MCP server exposing read-only tools for the GT-ST018M managed switch."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import parsers, writes
from .client import SwitchClient, SwitchConfig

log = logging.getLogger("managed-switch-mcp")

mcp = FastMCP("managed-switch")

_client: SwitchClient | None = None


def _get_client() -> SwitchClient:
    global _client
    if _client is None:
        _client = SwitchClient(SwitchConfig.from_env())
    return _client


# ---------- tools ----------

@mcp.tool()
async def get_device_info() -> dict[str, Any]:
    """Device identity (model, firmware, IP, MAC, uptime) and link status of every port.

    This is the safest "what is this switch and what's plugged in" call — use it first.
    """
    html = await _get_client().fetch("/info.cgi")
    return parsers.parse_device_info(html)


@mcp.tool()
async def list_ports() -> list[dict[str, str]]:
    """Per-port configuration and actual status: state, duplex, speed, flow control."""
    html = await _get_client().fetch("/port.cgi")
    return parsers.parse_port_settings(html)


@mcp.tool()
async def get_port_statistics() -> list[dict[str, Any]]:
    """Per-port traffic counters: TxGoodPkt, RxGoodPkt, TxGoodBytes, RxGoodBytes.

    Counters in the HTML are encoded as `hi-lo` 32-bit halves of a 64-bit value;
    this tool decodes them to integers.
    """
    html = await _get_client().fetch("/port.cgi?page=stats")
    return parsers.parse_port_statistics(html)


@mcp.tool()
async def get_mac_table(max_pages: int = 10) -> dict[str, Any]:
    """Full dynamic + static MAC forwarding table.

    The web UI paginates the table; this tool sets the page size to the maximum
    (30/page) and walks every page until exhausted. The POST it issues to do
    so only mutates session display state, never device configuration.

    Args:
        max_pages: Safety cap so a runaway table can't loop forever. Default 10
            (= up to 300 entries).
    """
    cli = _get_client()
    # Jump to page 1 with 30 entries/page, then walk forward with nextpage.
    html = await cli.post(
        "/mac.cgi?page=fwd_tbl",
        {"cmd": "firstpage", "perpage": "3"},
    )
    page = parsers.parse_mac_table_page(html)
    all_entries = list(page["entries"])
    total = page["total_items"]
    for _ in range(max_pages - 1):
        if len(all_entries) >= total:
            break
        html = await cli.post(
            "/mac.cgi?page=fwd_tbl",
            {"cmd": "nextpage", "perpage": "3"},
        )
        nxt = parsers.parse_mac_table_page(html)
        if not nxt["entries"]:
            break
        # nextpage past the last page returns the same page again — detect that.
        new_macs = [e for e in nxt["entries"] if not any(a["mac"] == e["mac"] for a in all_entries)]
        if not new_macs:
            break
        all_entries.extend(new_macs)
    return {"total_items": total, "fetched": len(all_entries), "entries": all_entries}


@mcp.tool()
async def get_vlan_list() -> dict[str, Any]:
    """All statically configured VLANs and the global VLAN-management toggle."""
    html = await _get_client().fetch("/vlan.cgi?page=static")
    return parsers.parse_static_vlans(html)


@mcp.tool()
async def get_port_vlan_config() -> list[dict[str, str]]:
    """Per-port VLAN assignments: access vs trunk, native VLAN, allowed trunk VLANs."""
    html = await _get_client().fetch("/vlan.cgi?page=port_based")
    return parsers.parse_port_vlan(html)


@mcp.tool()
async def get_port_aggregation() -> list[dict[str, str]]:
    """Configured LACP / static trunk (port-channel) groups and their member ports."""
    html = await _get_client().fetch("/trunk.cgi?page=group")
    return parsers.parse_trunk_groups(html)


@mcp.tool()
async def get_port_mirror() -> list[dict[str, str]]:
    """Active port-mirroring (SPAN) configurations."""
    html = await _get_client().fetch("/port.cgi?page=mirroring")
    return parsers.parse_port_mirror(html)


@mcp.tool()
async def get_port_isolation() -> list[dict[str, str]]:
    """Per-port isolation lists (which other ports each port may not talk to)."""
    html = await _get_client().fetch("/port.cgi?page=isolation")
    return parsers.parse_port_isolation(html)


@mcp.tool()
async def get_port_rate_limits() -> list[dict[str, str]]:
    """Per-port ingress and egress rate-limit configuration."""
    html = await _get_client().fetch("/port.cgi?page=bw_ctrl")
    return parsers.parse_rate_limits(html)


@mcp.tool()
async def get_loop_protocol() -> dict[str, str]:
    """Loop-prevention setting: Off, Loop Detection, or Spanning Tree, plus timers."""
    html = await _get_client().fetch("/loop.cgi")
    return parsers.parse_loop_protocol(html)


@mcp.tool()
async def get_stp_global() -> dict[str, Any]:
    """Global STP/RSTP parameters and the elected root-bridge info."""
    html = await _get_client().fetch("/loop.cgi?page=stp_global")
    return parsers.parse_stp_global(html)


@mcp.tool()
async def get_stp_port() -> list[dict[str, str]]:
    """Per-port STP state, role, path cost, priority, P2P and edge flags."""
    html = await _get_client().fetch("/loop.cgi?page=stp_port")
    return parsers.parse_stp_port(html)


@mcp.tool()
async def get_qos_port_queue() -> list[dict[str, str]]:
    """Port-to-egress-queue mapping (port-based QoS priority)."""
    html = await _get_client().fetch("/qos.cgi?page=port_pri")
    return parsers.parse_qos_port_queue(html)


@mcp.tool()
async def get_qos_queue_weights() -> list[dict[str, str]]:
    """Egress-queue weights (strict priority or WRR weight per queue)."""
    html = await _get_client().fetch("/qos.cgi?page=queue_weight")
    return parsers.parse_qos_queue_weights(html)


@mcp.tool()
async def get_igmp_config() -> dict[str, Any]:
    """IGMP snooping enable state, static/dynamic router ports, and learned entries."""
    html = await _get_client().fetch("/igmp.cgi?page=dump")
    return parsers.parse_igmp(html)


@mcp.tool()
async def get_dhcp_snooping() -> dict[str, bool]:
    """Whether DHCP snooping is enabled globally."""
    html = await _get_client().fetch("/dhcp_snooping.cgi?page=dump")
    return parsers.parse_dhcp_snooping(html)


@mcp.tool()
async def get_storm_control() -> list[dict[str, str]]:
    """Per-port broadcast / multicast / unknown-unicast storm-control rates."""
    html = await _get_client().fetch("/fwd.cgi?page=storm_ctrl")
    return parsers.parse_storm_control(html)


@mcp.tool()
async def get_jumbo_frame() -> dict[str, Any]:
    """Jumbo-frame enable state and selected MTU."""
    html = await _get_client().fetch("/fwd.cgi?page=jumboframe")
    return parsers.parse_jumbo_frame(html)


@mcp.tool()
async def get_ip_config() -> dict[str, str]:
    """Management IP, netmask, gateway, and DHCP-vs-static mode."""
    html = await _get_client().fetch("/ip.cgi")
    return parsers.parse_ip_config(html)


@mcp.tool()
async def get_users() -> list[str]:
    """List of configured admin usernames. Passwords are never returned."""
    html = await _get_client().fetch("/user.cgi")
    return parsers.parse_users(html)


# ---------- write tools ----------
#
# Every tool below MUTATES the running configuration. Three "catastrophic"
# operations (set_ip_config, change_admin_password, clear_mac_table) require
# both `SWITCH_ALLOW_DANGEROUS=1` to be set in the server's environment AND
# an explicit `confirm` argument. Factory reset and firmware upload are
# deliberately not exposed at all — do those via the web UI.

@mcp.tool()
async def set_device_name(name: str) -> dict[str, str]:
    """Rename the switch (1-32 chars). Reversible, low risk."""
    await writes.set_device_name(_get_client(), name)
    return {"status": "ok", "name": name}


@mcp.tool()
async def set_port_settings(
    ports: list[str],
    state: str | None = None,
    duplex: str | None = None,
    speed: str | None = None,
    flow_control: str | None = None,
) -> dict[str, Any]:
    """Apply state/duplex/speed/flow-control to one or more ports.

    `state` ∈ {Enable, Disable}; `duplex` ∈ {Auto, Full Duplex, Half Duplex};
    regular ports `speed` ∈ {Auto, 10M, 100M, 1000M, 2500M}; Port 9 (SFP+)
    `speed` ∈ {Auto, 100M, 1000M, 2500M, 10G}; `flow_control` ∈ {On, Off}.
    Port 9 cannot be combined with other ports in one call.
    """
    await writes.set_port_settings(
        _get_client(),
        ports=ports,
        state=state,
        duplex=duplex,
        speed=speed,
        flow_control=flow_control,
    )
    return {"status": "ok", "ports": ports}


@mcp.tool()
async def clear_port_statistics() -> dict[str, str]:
    """Reset every port's TX/RX counter to zero. Does not drop traffic."""
    await writes.clear_port_statistics(_get_client())
    return {"status": "ok"}


@mcp.tool()
async def set_port_rate_limit(
    ports: list[str],
    ingress_kbps: int | None = None,
    egress_kbps: int | None = None,
) -> dict[str, Any]:
    """Cap port bandwidth in Kbit/sec. Pass null to clear that direction.

    Range 16-2,500,000 for regular ports, 16-10,000,000 for Port 9 (SFP+).
    """
    await writes.set_port_rate_limit(
        _get_client(),
        ports=ports,
        ingress_kbps=ingress_kbps,
        egress_kbps=egress_kbps,
    )
    return {"status": "ok"}


@mcp.tool()
async def set_storm_control(
    ports: list[str],
    broadcast_kbps: int | None = None,
    known_multicast_kbps: int | None = None,
    unknown_multicast_kbps: int | None = None,
    unknown_unicast_kbps: int | None = None,
) -> dict[str, Any]:
    """Per-port storm-control rates. Same Kbit/sec ranges as rate-limit."""
    await writes.set_storm_control(
        _get_client(),
        ports=ports,
        broadcast_kbps=broadcast_kbps,
        known_multicast_kbps=known_multicast_kbps,
        unknown_multicast_kbps=unknown_multicast_kbps,
        unknown_unicast_kbps=unknown_unicast_kbps,
    )
    return {"status": "ok"}


@mcp.tool()
async def set_port_isolation(ports: list[str], isolated_from: list[str]) -> dict[str, Any]:
    """For each of `ports`, deny forwarding to every port in `isolated_from`."""
    await writes.set_port_isolation(
        _get_client(), ports=ports, isolated_from=isolated_from
    )
    return {"status": "ok"}


@mcp.tool()
async def set_port_mirror(
    source_ports: list[str],
    destination_port: str,
    direction: str = "Both",
    group: int = 1,
) -> dict[str, Any]:
    """Configure a SPAN session. `direction` ∈ {Both, Rx, Tx}."""
    await writes.set_port_mirror(
        _get_client(),
        group=group,
        source_ports=source_ports,
        direction=direction,
        destination_port=destination_port,
    )
    return {"status": "ok"}


@mcp.tool()
async def delete_port_mirror() -> dict[str, str]:
    """Remove the active port-mirror configuration."""
    await writes.delete_port_mirror(_get_client())
    return {"status": "ok"}


@mcp.tool()
async def set_loop_protocol(
    function: str,
    interval_seconds: int = 2,
    recover_seconds: int = 10,
) -> dict[str, Any]:
    """Set loop-prevention mode. `function` ∈ {Off, Loop Detection, Spanning Tree}."""
    await writes.set_loop_protocol(
        _get_client(),
        function=function,
        interval_seconds=interval_seconds,
        recover_seconds=recover_seconds,
    )
    return {"status": "ok"}


@mcp.tool()
async def set_stp_global(
    version: str = "RSTP",
    priority: int = 32768,
    max_age: int = 20,
    hello_time: int = 2,
    forward_delay: int = 15,
) -> dict[str, Any]:
    """Global STP parameters. Constraint: 2*(forward_delay-1) ≥ max_age ≥ 2*(hello_time+1)."""
    await writes.set_stp_global(
        _get_client(),
        version=version,
        priority=priority,
        max_age=max_age,
        hello_time=hello_time,
        forward_delay=forward_delay,
    )
    return {"status": "ok"}


@mcp.tool()
async def set_stp_port(
    ports: list[str],
    path_cost: int = 0,
    priority: int = 128,
    p2p: str = "Auto",
    edge: str = "No",
) -> dict[str, Any]:
    """Per-port STP. `path_cost`=0 means Auto. `p2p` ∈ {Yes,No,Auto}; `edge` ∈ {Yes,No}."""
    await writes.set_stp_port(
        _get_client(),
        ports=ports,
        path_cost=path_cost,
        priority=priority,
        p2p=p2p,
        edge=edge,
    )
    return {"status": "ok"}


@mcp.tool()
async def set_port_priority_queue(ports: list[str], queue: int) -> dict[str, Any]:
    """Map ports to a QoS egress queue (1..8)."""
    await writes.set_port_priority_queue(_get_client(), ports=ports, queue=queue)
    return {"status": "ok"}


@mcp.tool()
async def set_queue_weight(queues: list[int], weight: int | str) -> dict[str, Any]:
    """Set queue scheduling weight (1..15) or "strict" for strict priority."""
    await writes.set_queue_weight(_get_client(), queues=queues, weight=weight)
    return {"status": "ok"}


@mcp.tool()
async def set_igmp_enabled(enabled: bool) -> dict[str, Any]:
    """Toggle IGMP snooping."""
    await writes.set_igmp_enabled(_get_client(), enabled=enabled)
    return {"status": "ok", "igmp_enabled": enabled}


@mcp.tool()
async def set_igmp_static_router_ports(ports: list[str]) -> dict[str, Any]:
    """Replace the static IGMP router-port list (pass [] to clear)."""
    await writes.set_igmp_static_router_ports(_get_client(), ports=ports)
    return {"status": "ok", "ports": ports}


@mcp.tool()
async def set_dhcp_snooping_enabled(enabled: bool) -> dict[str, Any]:
    """Toggle DHCP snooping."""
    await writes.set_dhcp_snooping_enabled(_get_client(), enabled=enabled)
    return {"status": "ok", "dhcp_snooping_enabled": enabled}


@mcp.tool()
async def set_jumbo_frame_enabled(enabled: bool) -> dict[str, Any]:
    """Toggle jumbo frame support."""
    await writes.set_jumbo_frame_enabled(_get_client(), enabled=enabled)
    return {"status": "ok", "jumbo_frame_enabled": enabled}


@mcp.tool()
async def set_jumbo_frame_size(size_bytes: int) -> dict[str, Any]:
    """Set max jumbo size. Allowed: 1522, 1536, 1552, 9216, 10000, 12000."""
    await writes.set_jumbo_frame_size(_get_client(), size_bytes=size_bytes)
    return {"status": "ok", "size_bytes": size_bytes}


@mcp.tool()
async def add_static_mac(mac: str, vlan: int, port: str) -> dict[str, Any]:
    """Pin a MAC to a port in a VLAN. MAC format AA:BB:CC:DD:EE:FF."""
    await writes.add_static_mac(_get_client(), mac=mac, vlan=vlan, port=port)
    return {"status": "ok", "mac": mac, "vlan": vlan, "port": port}


@mcp.tool()
async def delete_static_mac(mac: str, vlan: int) -> dict[str, Any]:
    """Remove a static MAC entry."""
    await writes.delete_static_mac(_get_client(), mac=mac, vlan=vlan)
    return {"status": "ok", "mac": mac, "vlan": vlan}


@mcp.tool()
async def clear_mac_table(confirm: str) -> dict[str, str]:
    """Flush every learned MAC. Pass `confirm="clear_mac_table"`. Requires SWITCH_ALLOW_DANGEROUS=1."""
    await writes.clear_mac_table(_get_client(), confirm=confirm)
    return {"status": "ok"}


@mcp.tool()
async def set_vlan_management_enabled(enabled: bool) -> dict[str, Any]:
    """Globally enable/disable VLAN management."""
    await writes.set_vlan_management_enabled(_get_client(), enabled=enabled)
    return {"status": "ok", "vlan_management_enabled": enabled}


@mcp.tool()
async def add_vlan(vid: int, name: str = "") -> dict[str, Any]:
    """Create a static VLAN."""
    await writes.add_vlan(_get_client(), vid=vid, name=name)
    return {"status": "ok", "vid": vid, "name": name}


@mcp.tool()
async def delete_vlan(vid: int) -> dict[str, Any]:
    """Remove a static VLAN."""
    await writes.delete_vlan(_get_client(), vid=vid)
    return {"status": "ok", "vid": vid}


@mcp.tool()
async def set_port_vlan_access(ports: list[str], access_vlan: int) -> dict[str, Any]:
    """Set ports as untagged access members of `access_vlan`."""
    await writes.set_port_vlan_access(
        _get_client(), ports=ports, access_vlan=access_vlan
    )
    return {"status": "ok"}


@mcp.tool()
async def set_port_vlan_trunk(
    ports: list[str],
    native_vlan: int,
    allowed_vlans: list[int],
) -> dict[str, Any]:
    """Configure ports as 802.1Q trunks with a native VLAN and an allowed set."""
    await writes.set_port_vlan_trunk(
        _get_client(),
        ports=ports,
        native_vlan=native_vlan,
        allowed_vlans=allowed_vlans,
    )
    return {"status": "ok"}


@mcp.tool()
async def create_port_aggregation(
    group: int, trunk_type: str, ports: list[str]
) -> dict[str, Any]:
    """Create a static or LACP link-aggregation group (group ∈ {1,2})."""
    await writes.create_port_aggregation(
        _get_client(), group=group, trunk_type=trunk_type, ports=ports
    )
    return {"status": "ok"}


@mcp.tool()
async def delete_port_aggregation(group: int) -> dict[str, Any]:
    """Remove a LAG."""
    await writes.delete_port_aggregation(_get_client(), group=group)
    return {"status": "ok"}


@mcp.tool()
async def save_running_config() -> dict[str, str]:
    """Persist running config to flash. Without this, changes revert on reboot."""
    await writes.save_running_config(_get_client())
    return {"status": "ok"}


@mcp.tool()
async def reboot_device(confirm: str) -> dict[str, str]:
    """Reboot the switch. Pass `confirm="reboot"`. Disrupts traffic for ~30s."""
    await writes.reboot_device(_get_client(), confirm=confirm)
    return {"status": "ok"}


@mcp.tool()
async def set_ip_config(
    mode: str,
    confirm: str,
    ip: str | None = None,
    netmask: str | None = None,
    gateway: str | None = None,
) -> dict[str, Any]:
    """Change the management IP. **Can lock you out**.

    `mode` ∈ {static, dhcp}. Requires SWITCH_ALLOW_DANGEROUS=1 and
    `confirm="set_ip_config"`.
    """
    await writes.set_ip_config(
        _get_client(),
        mode=mode,
        ip=ip,
        netmask=netmask,
        gateway=gateway,
        confirm=confirm,
    )
    return {"status": "ok"}


@mcp.tool()
async def change_admin_password(
    username: str, new_password: str, confirm: str
) -> dict[str, Any]:
    """Replace the admin credentials. **Can lock you out.** Requires
    SWITCH_ALLOW_DANGEROUS=1 and `confirm="change_admin_password"`."""
    await writes.change_admin_password(
        _get_client(),
        username=username,
        new_password=new_password,
        confirm=confirm,
    )
    return {"status": "ok", "username": username}


# ---------- read tool: backup_config ----------

@mcp.tool()
async def backup_config(save_path: str | None = None) -> dict[str, Any]:
    """Download the binary configuration file.

    The switch exposes this as a one-shot read-only endpoint. The returned
    payload is the same opaque blob the OEM UI saves as `switch_cfg.bin`.

    Args:
        save_path: Optional path on the *local* filesystem to write the file
            to. When omitted, the tool returns the bytes as base64 instead so
            an LLM client can stash it. The first 64 bytes of the file are
            also returned as a hex preview either way, for sanity checks.
    """
    body, fname = await _get_client().fetch_bytes("/config_back.cgi?cmd=conf_backup")
    out: dict[str, Any] = {
        "filename": fname,
        "size_bytes": len(body),
        "head_hex": body[:64].hex(),
    }
    if save_path:
        p = Path(os.path.expanduser(save_path))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        out["saved_to"] = str(p)
    else:
        out["base64"] = base64.b64encode(body).decode("ascii")
    return out


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
