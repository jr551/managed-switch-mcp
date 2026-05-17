"""Write helpers for the GT-ST018M.

Each function builds the POST body the OEM web UI would have sent and dispatches
it via the shared :class:`SwitchClient`. Validation is **intentionally minimal** —
the device's own form handlers will reject anything truly out of range — but each
function still maps the human-friendly enum strings ("Enable" / "Auto" / "RSTP")
to the integer codes the firmware expects.

A handful of catastrophic operations (factory reset, firmware upload, change
management IP, change admin password) are gated behind the
``SWITCH_ALLOW_DANGEROUS=1`` environment variable AND a per-call ``confirm`` arg
that must match a fixed string. There is no way to factory-reset or brick this
device "by accident" via this module.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Iterable

from .client import SwitchClient

PORT_NAME_RE = re.compile(r"^(?:Port\s*(\d+)|Trunk\s*(\d+))$", re.IGNORECASE)


def port_to_formkey(port_name: str) -> str:
    """Map a user-facing port label to the firmware's `portN` form key.

    ``"Port 3"`` → ``"port2"``; ``"Port 9"`` → ``"port8"``; ``"Trunk1"`` → ``"port10"``.

    Raises:
        ValueError: if ``port_name`` isn't recognised.
    """
    m = PORT_NAME_RE.match(port_name.strip())
    if not m:
        raise ValueError(f"Unrecognised port label: {port_name!r}")
    if m.group(1):
        n = int(m.group(1))
        if not 1 <= n <= 9:
            raise ValueError(f"Port number out of range: {n}")
        return f"port{n - 1}"
    n = int(m.group(2))
    if not 1 <= n <= 2:
        raise ValueError(f"Trunk number out of range: {n}")
    return f"port{9 + n}"


def _is_ext_port(port_name: str) -> bool:
    """Port 9 (the 10G SFP+ cage) lives in a separate "ext" form on a few pages."""
    return port_name.strip().lower().replace(" ", "") == "port9"


# Enum maps copied verbatim from the firmware's <option> tags.
STATE_MAP = {"enable": "1", "disable": "0"}
DUPLEX_MAP = {"auto": "2", "full": "1", "full duplex": "1", "half": "0", "half duplex": "0"}
SPEED_MAP_REGULAR = {"auto": "10", "10m": "0", "100m": "1", "1000m": "2", "2500m": "5"}
SPEED_MAP_EXT = {"auto": "10", "100m": "1", "1000m": "2", "2500m": "5", "10g": "4"}
FLOW_MAP = {"off": "0", "on": "1"}
LOOP_FUNC_MAP = {"off": "0", "loop detection": "1", "spanning tree": "3"}
STP_VERSION_MAP = {"stp": "0", "rstp": "1"}
P2P_MAP = {"yes": "true", "no": "false", "auto": "auto", "true": "true", "false": "false"}
EDGE_MAP = {"yes": "true", "no": "false", "true": "true", "false": "false"}
JUMBO_SIZE_MAP = {
    "1522": "1", "1536": "2", "1552": "3",
    "9216": "4", "10000": "5", "12000": "6",
}
TRUNK_TYPE_MAP = {"static": "0", "lacp": "1"}
VLAN_TYPE_MAP = {"access": "0", "trunk": "1"}
MIRROR_DIRECTION_MAP = {"both": "3", "rx": "1", "tx": "2"}


def _enum(value: str, mapping: dict[str, str], label: str) -> str:
    key = value.strip().lower()
    if key in mapping:
        return mapping[key]
    raise ValueError(f"Invalid {label} {value!r}; expected one of: {sorted(mapping)}")


def _require_dangerous(action: str, confirm: str) -> None:
    if os.environ.get("SWITCH_ALLOW_DANGEROUS") != "1":
        raise PermissionError(
            f"{action} is disabled. Set SWITCH_ALLOW_DANGEROUS=1 in the server env to enable."
        )
    if confirm != action:
        raise ValueError(
            f"{action} requires confirm={action!r} (got {confirm!r})."
        )


# ----------------------------------------------------------------------
# Identity / sysadmin
# ----------------------------------------------------------------------

async def set_device_name(client: SwitchClient, name: str) -> None:
    if not 1 <= len(name) <= 32:
        raise ValueError("Device name must be 1-32 characters.")
    await client.post("/info.cgi", {"devName": name})


async def save_running_config(client: SwitchClient) -> None:
    """Persist the running configuration to flash (`save.cgi`)."""
    await client.post("/save.cgi", {"cmd": "save"})


async def reboot_device(client: SwitchClient, confirm: str) -> None:
    """Reboot the switch. Required `confirm` == "reboot"."""
    if confirm != "reboot":
        raise ValueError('reboot requires confirm="reboot"')
    await client.post("/reboot.cgi", {"cmd": "reboot"})


# Dangerous — gated behind env var + confirm
async def set_ip_config(
    client: SwitchClient,
    *,
    mode: str,
    ip: str | None = None,
    netmask: str | None = None,
    gateway: str | None = None,
    confirm: str,
) -> None:
    """Change the management IP. **Can lock you out** of the switch.

    ``mode`` must be "static" or "dhcp". Other fields are required iff mode=static.
    """
    _require_dangerous("set_ip_config", confirm)
    mode_val = {"static": "0", "dhcp": "1"}[mode.strip().lower()]
    data = {"cmd": "ip", "dhcp_state": mode_val}
    if mode_val == "0":
        if not (ip and netmask and gateway):
            raise ValueError("Static mode requires ip, netmask and gateway")
        data.update({"ip": ip, "netmask": netmask, "gateway": gateway})
    await client.post("/ip.cgi", data)


async def change_admin_password(
    client: SwitchClient, *, username: str, new_password: str, confirm: str
) -> None:
    """Replace the single admin account's credentials. **Can lock you out.**

    The firmware stores ``md5(username + password)``; this function performs
    the same hashing client-side before posting.
    """
    _require_dangerous("change_admin_password", confirm)
    if not 1 <= len(username) <= 16:
        raise ValueError("Username must be 1-16 characters.")
    if not 1 <= len(new_password) <= 64:
        raise ValueError("Password must be 1-64 characters.")
    digest = hashlib.md5((username + new_password).encode("ascii")).hexdigest()
    await client.post(
        "/user.cgi",
        {"cmd": "passwd", "mname": username, "mpass": digest, "mpass2": "0"},
    )


# ----------------------------------------------------------------------
# Port settings
# ----------------------------------------------------------------------

async def set_port_settings(
    client: SwitchClient,
    *,
    ports: Iterable[str],
    state: str | None = None,
    duplex: str | None = None,
    speed: str | None = None,
    flow_control: str | None = None,
) -> None:
    """Apply state/duplex/speed/flow-control to one or more ports.

    Any field left as ``None`` defaults to "Auto"/"Enable" (the firmware POSTs
    these even when the user "didn't touch them" in the UI).
    """
    ports = list(ports)
    ext = any(_is_ext_port(p) for p in ports)
    if ext and not all(_is_ext_port(p) for p in ports):
        raise ValueError(
            "Port 9 (SFP+) lives in a separate form section and must be configured "
            "on its own — call this once for Port 9 alone, again for the others."
        )

    speed_map = SPEED_MAP_EXT if ext else SPEED_MAP_REGULAR
    data: dict[str, str] = {"cmd": "port"}
    for p in ports:
        data[port_to_formkey(p)] = "on"
    data["state"] = _enum(state or "enable", STATE_MAP, "state")
    data["duplex"] = _enum(duplex or "auto", DUPLEX_MAP, "duplex")
    data["speed"] = _enum(speed or "auto", speed_map, "speed")
    data["flowctrl"] = _enum(flow_control or "off", FLOW_MAP, "flow_control")
    await client.post("/port.cgi", data)


async def clear_port_statistics(client: SwitchClient) -> None:
    """Reset all per-port traffic counters to zero."""
    await client.post("/port.cgi?page=stats", {"cmd": "stats", "submit": "Clear"})


async def set_port_rate_limit(
    client: SwitchClient,
    *,
    ports: Iterable[str],
    ingress_kbps: int | None = None,
    egress_kbps: int | None = None,
) -> None:
    """Set ingress/egress rate caps (Kbit/sec). ``None`` clears that direction."""
    ports = list(ports)
    ext = any(_is_ext_port(p) for p in ports)
    if ext and not all(_is_ext_port(p) for p in ports):
        raise ValueError("Port 9 (SFP+) must be set separately from other ports.")
    data: dict[str, str] = {"cmd": "bw_ctrl"}
    for p in ports:
        data[port_to_formkey(p)] = "on"
    if ext:
        data["bc_rate"] = ""  # ext form re-uses some field names but they're empty
        data["ext_in_rate"] = "" if ingress_kbps is None else str(int(ingress_kbps))
        data["ext_out_rate"] = "" if egress_kbps is None else str(int(egress_kbps))
    else:
        data["in_rate"] = "" if ingress_kbps is None else str(int(ingress_kbps))
        data["out_rate"] = "" if egress_kbps is None else str(int(egress_kbps))
    await client.post("/port.cgi?page=bwctrl", data)


async def set_storm_control(
    client: SwitchClient,
    *,
    ports: Iterable[str],
    broadcast_kbps: int | None = None,
    known_multicast_kbps: int | None = None,
    unknown_multicast_kbps: int | None = None,
    unknown_unicast_kbps: int | None = None,
) -> None:
    ports = list(ports)
    ext = any(_is_ext_port(p) for p in ports)
    if ext and not all(_is_ext_port(p) for p in ports):
        raise ValueError("Port 9 (SFP+) must be set separately from other ports.")
    data: dict[str, str] = {"cmd": "storm"}
    for p in ports:
        data[port_to_formkey(p)] = "on"
    prefix = "ext_" if ext else ""
    data[f"{prefix}bc_rate"] = "" if broadcast_kbps is None else str(int(broadcast_kbps))
    data[f"{prefix}mc_rate"] = "" if known_multicast_kbps is None else str(int(known_multicast_kbps))
    data[f"{prefix}uuc_rate"] = "" if unknown_unicast_kbps is None else str(int(unknown_unicast_kbps))
    data[f"{prefix}umc_rate"] = "" if unknown_multicast_kbps is None else str(int(unknown_multicast_kbps))
    await client.post("/fwd.cgi?page=storm_ctrl", data)


async def set_port_isolation(
    client: SwitchClient, *, ports: Iterable[str], isolated_from: Iterable[str]
) -> None:
    """Set each `port` so it cannot forward to any of `isolated_from`."""
    data: list[tuple[str, str]] = [("cmd", "portisolation")]
    for p in ports:
        data.append(("port", p))
    for p in isolated_from:
        data.append(("isolationlist", p))
    # httpx accepts list-of-tuples for repeated keys
    await client.post("/port.cgi?page=isolation", dict(data))


async def set_port_mirror(
    client: SwitchClient,
    *,
    group: int = 1,
    source_ports: Iterable[str],
    direction: str,
    destination_port: str,
) -> None:
    """Configure a SPAN session sending `source_ports` traffic to `destination_port`."""
    data: dict[str, str] = {
        "cmd": "mirror",
        "mirror_group": str(group),
        "mirror_direction": _enum(direction, MIRROR_DIRECTION_MAP, "direction"),
    }
    for p in source_ports:
        data[port_to_formkey(p)] = "on"
    # destination_port uses the internal port number directly
    dest_key = port_to_formkey(destination_port)
    data["mirroring_port"] = dest_key.removeprefix("port")
    await client.post("/port.cgi?page=mirroring", data)


async def delete_port_mirror(client: SwitchClient) -> None:
    await client.post("/port.cgi?page=delete_mirror", {"cmd": "del_mirror"})


# ----------------------------------------------------------------------
# Loop / STP
# ----------------------------------------------------------------------

async def set_loop_protocol(
    client: SwitchClient,
    *,
    function: str,
    interval_seconds: int = 2,
    recover_seconds: int = 10,
) -> None:
    await client.post(
        "/loop.cgi",
        {
            "cmd": "loop",
            "func_type": _enum(function, LOOP_FUNC_MAP, "function"),
            "interval_time": str(int(interval_seconds)),
            "recover_time": str(int(recover_seconds)),
        },
    )


async def set_stp_global(
    client: SwitchClient,
    *,
    version: str = "rstp",
    priority: int = 32768,
    max_age: int = 20,
    hello_time: int = 2,
    forward_delay: int = 15,
) -> None:
    if not (2 * (forward_delay - 1) >= max_age >= 2 * (hello_time + 1)):
        raise ValueError(
            "STP timer constraint failed: 2*(forward_delay-1) >= max_age >= 2*(hello_time+1)"
        )
    await client.post(
        "/loop.cgi?page=stp_global",
        {
            "cmd": "stp",
            "version": _enum(version, STP_VERSION_MAP, "version"),
            "priority": str(int(priority)),
            "maxage": str(int(max_age)),
            "hello": str(int(hello_time)),
            "delay": str(int(forward_delay)),
        },
    )


async def set_stp_port(
    client: SwitchClient,
    *,
    ports: Iterable[str],
    path_cost: int = 0,
    priority: int = 128,
    p2p: str = "auto",
    edge: str = "no",
) -> None:
    if not 0 <= path_cost <= 200_000_000:
        raise ValueError("path_cost out of range 0-200000000")
    data: dict[str, str] = {
        "cmd": "stp_port",
        "cost": str(int(path_cost)),
        "priority": str(int(priority)),
        "p2p": _enum(p2p, P2P_MAP, "p2p"),
        "edge": _enum(edge, EDGE_MAP, "edge"),
    }
    for p in ports:
        data[port_to_formkey(p)] = "on"
    await client.post("/loop.cgi?page=stp_port", data)


# ----------------------------------------------------------------------
# QoS
# ----------------------------------------------------------------------

async def set_port_priority_queue(
    client: SwitchClient, *, ports: Iterable[str], queue: int
) -> None:
    """Map ports to an egress queue (1..8)."""
    if not 1 <= queue <= 8:
        raise ValueError("queue must be 1..8")
    data = {"cmd": "portprio", "port_priority": str(queue - 1)}
    for p in ports:
        data[port_to_formkey(p)] = "on"
    await client.post("/qos.cgi?page=port_pri", data)


async def set_queue_weight(
    client: SwitchClient, *, queues: Iterable[int], weight: int | str
) -> None:
    """Set queue scheduling weight. ``weight="strict"`` enables strict priority."""
    if weight == "strict" or weight == 0:
        w = "0"
    else:
        w = str(int(weight))
        if not 1 <= int(w) <= 15:
            raise ValueError("weight must be 'strict' or 1..15")
    data = [("cmd", "qweight"), ("weight", w)]
    for q in queues:
        if not 1 <= int(q) <= 8:
            raise ValueError("queue must be 1..8")
        data.append(("queueid", str(int(q) - 1)))
    # repeated `queueid` requires list-of-tuples; httpx handles it
    await client._ensure_authed()  # type: ignore[attr-defined]
    cli = await client._ensure_client()  # type: ignore[attr-defined]
    r = await cli.post(
        "/qos.cgi?page=queue_weight",
        data=data,
        headers={"Referer": f"{client.base_url}/qos.cgi?page=queue_weight"},
    )
    r.raise_for_status()


# ----------------------------------------------------------------------
# IGMP / DHCP snooping / Jumbo
# ----------------------------------------------------------------------

async def set_igmp_enabled(client: SwitchClient, *, enabled: bool) -> None:
    data = {}
    if enabled:
        data["enable_igmp"] = "on"
    await client.post("/igmp.cgi?page=enable_igmp", data)


async def set_igmp_static_router_ports(
    client: SwitchClient, *, ports: Iterable[str]
) -> None:
    """Set the *complete* set of static router ports. Pass [] to clear."""
    data: dict[str, str] = {"cmd": "set"}
    for p in ports:
        key = port_to_formkey(p).replace("port", "lPort_")
        data[key] = "on"
    await client.post("/igmp.cgi?page=igmp_static_router", data)


async def set_dhcp_snooping_enabled(client: SwitchClient, *, enabled: bool) -> None:
    data = {}
    if enabled:
        data["enable_dhcpsnp"] = "on"
    await client.post("/dhcp_snooping.cgi?page=enable_dhcpsnooping", data)


async def set_jumbo_frame_enabled(client: SwitchClient, *, enabled: bool) -> None:
    data = {}
    if enabled:
        data["enable_jumbo"] = "on"
    await client.post("/fwd.cgi?page=jumboframe", data)


async def set_jumbo_frame_size(client: SwitchClient, *, size_bytes: int) -> None:
    key = str(int(size_bytes))
    if key not in JUMBO_SIZE_MAP:
        raise ValueError(
            f"Unsupported jumbo size {size_bytes}; pick one of: {sorted(JUMBO_SIZE_MAP)}"
        )
    await client.post(
        "/fwd.cgi?page=jumboframe",
        {"cmd": "jumboframe", "jumboframe": JUMBO_SIZE_MAP[key]},
    )


# ----------------------------------------------------------------------
# MAC table / static
# ----------------------------------------------------------------------

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", re.IGNORECASE)


async def add_static_mac(
    client: SwitchClient, *, mac: str, vlan: int, port: str
) -> None:
    if not _MAC_RE.match(mac):
        raise ValueError("MAC must look like AA:BB:CC:DD:EE:FF")
    src = port_to_formkey(port).removeprefix("port")
    await client.post(
        "/mac.cgi?page=static",
        {"cmd": "macstatic", "mac": mac, "vlan": str(int(vlan)), "src": src},
    )


async def delete_static_mac(client: SwitchClient, *, mac: str, vlan: int) -> None:
    """The web UI deletes via `remove_<index>` checkboxes; the firmware exposes
    a removal-by-key endpoint at the same URL with the MAC+VLAN posted directly."""
    if not _MAC_RE.match(mac):
        raise ValueError("MAC must look like AA:BB:CC:DD:EE:FF")
    await client.post(
        "/mac.cgi?page=staticdel",
        {"cmd": "macstatictbl", "mac": mac, "vlan": str(int(vlan))},
    )


async def clear_mac_table(client: SwitchClient, *, confirm: str) -> None:
    if confirm != "clear_mac_table":
        raise ValueError('clear_mac_table requires confirm="clear_mac_table"')
    await client.post("/mac.cgi?page=fwd_tbl", {"cmd": "mactblclr", "perpage": "3"})


# ----------------------------------------------------------------------
# VLAN
# ----------------------------------------------------------------------

async def set_vlan_management_enabled(client: SwitchClient, *, enabled: bool) -> None:
    data = {}
    if enabled:
        data["enable_vlan"] = "on"
    await client.post("/vlan.cgi?page=state", data)


async def add_vlan(client: SwitchClient, *, vid: int, name: str = "") -> None:
    if not 1 <= vid <= 4094:
        raise ValueError("VLAN ID must be 1..4094")
    if len(name) > 14:
        raise ValueError("VLAN name max 14 chars")
    await client.post(
        "/vlan.cgi?page=static",
        {"vid": str(vid), "name": name},
    )


async def delete_vlan(client: SwitchClient, *, vid: int) -> None:
    if not 1 <= vid <= 4094:
        raise ValueError("VLAN ID must be 1..4094")
    await client.post(
        "/vlan.cgi?page=getRmvVlanEntry",
        {f"remove_{vid}": "on"},
    )


async def set_port_vlan_access(
    client: SwitchClient, *, ports: Iterable[str], access_vlan: int
) -> None:
    """Put ``ports`` into access mode on ``access_vlan``."""
    data: dict[str, str] = {
        "vlan_type": VLAN_TYPE_MAP["access"],
        "vlan_access": str(int(access_vlan)),
    }
    for p in ports:
        data[port_to_formkey(p)] = "on"
    await client.post("/vlan.cgi?page=port_based", data)


async def set_port_vlan_trunk(
    client: SwitchClient,
    *,
    ports: Iterable[str],
    native_vlan: int,
    allowed_vlans: Iterable[int],
) -> None:
    """Configure ``ports`` as 802.1Q trunks with native + allowed VLANs."""
    data: dict[str, str] = {
        "vlan_type": VLAN_TYPE_MAP["trunk"],
        "vlan_trunk": str(int(native_vlan)),
    }
    for p in ports:
        data[port_to_formkey(p)] = "on"
    for v in allowed_vlans:
        data[f"VLAN{int(v)}"] = "on"
    await client.post("/vlan.cgi?page=port_based", data)


# ----------------------------------------------------------------------
# Port aggregation
# ----------------------------------------------------------------------

async def create_port_aggregation(
    client: SwitchClient,
    *,
    group: int,
    trunk_type: str,
    ports: Iterable[str],
) -> None:
    """Create a LAG. `trunk_type` is "static" or "lacp"."""
    if not 1 <= group <= 2:
        raise ValueError("group must be 1 or 2")
    data: dict[str, str] = {
        "cmd": "trunk",
        "id": str(group),
        "trunk_type": _enum(trunk_type, TRUNK_TYPE_MAP, "trunk_type"),
    }
    for p in ports:
        data[port_to_formkey(p)] = "on"
    await client.post("/trunk.cgi?page=group", data)


async def delete_port_aggregation(client: SwitchClient, *, group: int) -> None:
    if not 1 <= group <= 2:
        raise ValueError("group must be 1 or 2")
    await client.post(
        "/trunk.cgi?page=group_remove",
        {"cmd": "group_remove", f"remove_{group - 1}": "on"},
    )
