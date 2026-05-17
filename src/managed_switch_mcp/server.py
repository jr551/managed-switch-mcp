"""MCP server exposing read-only tools for the GT-ST018M managed switch."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import parsers
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
