"""HTML parsers for each switch CGI page.

Each function takes raw HTML and returns plain Python data (dicts/lists)
that the MCP tool layer can serialise straight to JSON. Parsers are
defensive: missing tables yield empty lists rather than raising, so the
caller still gets a useful response even if firmware tweaks the layout.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(node: Any) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _rows(table: Tag) -> list[list[str]]:
    out: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        out.append([_text(c) for c in cells])
    return out


def _option_text(opt: Tag | None) -> str:
    """Return just the visible label of an <option>.

    The firmware emits unclosed `<option>` tags, so BeautifulSoup nests every
    subsequent option as a child of the first one. We want only the direct
    text node, not the whole subtree.
    """
    if opt is None:
        return ""
    for child in opt.children:
        if isinstance(child, str):
            s = child.strip()
            if s:
                return s
    return _text(opt).split()[0] if _text(opt) else ""


def _selected_option(select: Tag | None) -> str:
    """Find the selected option's label in a (possibly malformed) <select>.

    Falls back to the first option when none is marked selected.
    """
    if select is None:
        return ""
    opt = select.find("option", selected=True)
    if opt is None:
        opt = select.find("option")
    return _option_text(opt)


# ---------- info.cgi ----------

_UPTIME_RE = re.compile(r"(\d+)Day(\d+)Hour(\d+)Minute(\d+)Second")


def parse_uptime(s: str) -> dict[str, int] | None:
    m = _UPTIME_RE.match(s.replace(" ", ""))
    if not m:
        return None
    d, h, mi, sec = (int(x) for x in m.groups())
    total = ((d * 24 + h) * 60 + mi) * 60 + sec
    return {"days": d, "hours": h, "minutes": mi, "seconds": sec, "total_seconds": total}


def parse_device_info(html: str) -> dict[str, Any]:
    soup = _soup(html)
    info: dict[str, Any] = {}
    # The firmware mis-closes some <th> tags as </td>, which breaks structural
    # parsing — pull the well-known fields out with targeted regexes instead.
    patterns = {
        "device_name": r'name="devName"\s+value="([^"]*)"',
        "sys_uptime": r"(\d+Day\d+Hour\d+Minute\d+Second)",
        "device_model": r"Device Model:[^<]*</td>\s*<td[^>]*>([^<]+)",
        "firmware_version": r"Firmware Version:[^<]*</td>\s*<td[^>]*>([^<]+)",
        "ip_address": r"IP Address:[^<]*</td>\s*<td[^>]*>([^<]+)",
        "netmask": r"Netmask:[^<]*</td>\s*<td[^>]*>([^<]+)",
        "mac_address": r"MAC Address:[^<]*</td>\s*<td[^>]*>([^<]+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, html)
        if m:
            info[key] = m.group(1).strip()

    up = info.get("sys_uptime")
    if isinstance(up, str):
        parsed = parse_uptime(up)
        if parsed:
            info["uptime"] = parsed

    # Port status table (the second <table> on the page, with Link Status header)
    ports: list[dict[str, str]] = []
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if "Link Status" in headers and "Port" in headers:
            for row in tbl.find_all("tr")[1:]:
                cells = [_text(td) for td in row.find_all("td")]
                if len(cells) >= 5:
                    ports.append({
                        "port": cells[0],
                        "link": cells[1],
                        "duplex": cells[2],
                        "speed": cells[3],
                        "flow_control": cells[4],
                    })
            break
    return {"device": info, "ports": ports}


# ---------- port.cgi (Port Setting) ----------

def parse_port_settings(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    target = None
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if "Config Attribute" in headers and "Actual Status" in headers:
            target = tbl
            break
    if target is None:
        return []
    out: list[dict[str, str]] = []
    for row in target.find_all("tr")[2:]:  # 2 header rows
        cells = [_text(td) for td in row.find_all("td")]
        if len(cells) >= 8:
            out.append({
                "port": cells[0],
                "state": cells[1],
                "duplex_config": cells[2],
                "duplex_actual": cells[3],
                "speed_config": cells[4],
                "speed_actual": cells[5],
                "flow_control_config": cells[6],
                "flow_control_actual": cells[7],
            })
    return out


# ---------- port.cgi?page=stats ----------

def _stat64(raw: str) -> int:
    """Convert the "hi-lo" 64-bit counter encoding used by the firmware."""
    raw = raw.strip()
    if "-" not in raw:
        try:
            return int(raw)
        except ValueError:
            return 0
    hi, lo = raw.split("-", 1)
    try:
        return int(hi) * (1 << 32) + int(lo)
    except ValueError:
        return 0


def parse_port_statistics(html: str) -> list[dict[str, Any]]:
    soup = _soup(html)
    target = None
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if "TxGoodPkt" in headers and "RxGoodPkt" in headers:
            target = tbl
            break
    if target is None:
        return []
    out: list[dict[str, Any]] = []
    for row in target.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        out.append({
            "port": _text(cells[0]),
            "state": _text(cells[1]),
            "link": _text(cells[2]),
            "tx_packets": _stat64(_text(cells[3])),
            "rx_packets": _stat64(_text(cells[4])),
            "tx_bytes": _stat64(_text(cells[5])),
            "rx_bytes": _stat64(_text(cells[6])),
        })
    return out


# ---------- mac.cgi?page=fwd_tbl ----------

_TOTAL_RE = re.compile(r"Total\s+(\d+)\s+Items")
_CURRENT_RE = re.compile(r"Current\s+(\d+)\s*-\s*(\d+)\s+Items")
_TOTALPAGE_RE = re.compile(r'id=[\'"]?totalpage[\'"]?[^>]*>\s*(\d+)')


def parse_mac_table_page(html: str) -> dict[str, Any]:
    soup = _soup(html)
    entries: list[dict[str, str]] = []
    target = None
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if headers[:4] == ["MAC Address", "Type", "Port", "VLAN ID"]:
            target = tbl
            break
    if target is not None:
        for row in target.find_all("tr")[1:]:
            cells = [_text(td) for td in row.find_all("td")]
            if len(cells) >= 4:
                entries.append({
                    "mac": cells[0],
                    "type": cells[1],
                    "port": cells[2],
                    "vlan": cells[3],
                })

    total_m = _TOTAL_RE.search(html)
    cur_m = _CURRENT_RE.search(html)
    pages_m = _TOTALPAGE_RE.search(html)
    return {
        "entries": entries,
        "total_items": int(total_m.group(1)) if total_m else len(entries),
        "current_range": (int(cur_m.group(1)), int(cur_m.group(2))) if cur_m else None,
        "total_pages": int(pages_m.group(1)) if pages_m else 1,
    }


# ---------- vlan.cgi?page=static ----------

def parse_static_vlans(html: str) -> dict[str, Any]:
    soup = _soup(html)
    vlan_enabled = "checked" in str(soup.find("input", {"name": "enable_vlan"}) or "")
    vlans: list[dict[str, str]] = []
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if "VLAN ID" in headers and "VLAN Name" in headers:
            for row in tbl.find_all("tr")[1:]:
                cells = [_text(td) for td in row.find_all("td")]
                if len(cells) >= 4:
                    vlans.append({"index": cells[1], "vlan_id": cells[2], "name": cells[3]})
    return {"vlan_management_enabled": vlan_enabled, "vlans": vlans}


# ---------- vlan.cgi?page=port_based ----------

def parse_port_vlan(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    target = None
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if "Port vlan type" in headers and "Access VLAN" in headers:
            target = tbl
    if target is None:
        return []
    rows: list[dict[str, str]] = []
    for row in target.find_all("tr")[1:]:
        cells = [_text(td) for td in row.find_all("td")]
        if len(cells) >= 5 and cells[0]:
            rows.append({
                "port": cells[0],
                "type": cells[1],
                "access_vlan": cells[2],
                "native_vlan": cells[3],
                "trunk_vlans": cells[4],
            })
    return rows


# ---------- trunk.cgi?page=group ----------

def parse_trunk_groups(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    target = None
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if "Aggregate Group ID" in headers and "Member port" in headers:
            target = tbl
    if target is None:
        return []
    groups: list[dict[str, str]] = []
    for row in target.find_all("tr")[1:]:
        cells = [_text(td) for td in row.find_all("td")]
        if len(cells) >= 5:
            groups.append({
                "group": cells[1],
                "type": cells[2],
                "member_ports": cells[3],
                "aggregated_ports": cells[4],
            })
    return groups


# ---------- port.cgi?page=mirroring ----------

def parse_port_mirror(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    rows: list[dict[str, str]] = []
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if headers[:4] == ["Mirror Group", "Source mirror port", "Mirror Direction", "Destination Port"]:
            for row in tbl.find_all("tr")[1:]:
                cells = [_text(td) for td in row.find_all("td")]
                if len(cells) >= 4 and not row.find("select"):
                    rows.append({
                        "group": cells[0],
                        "source": cells[1],
                        "direction": cells[2],
                        "destination": cells[3],
                    })
    return rows


# ---------- port.cgi?page=isolation ----------

def parse_port_isolation(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    rows: list[dict[str, str]] = []
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if headers[:2] == ["Port", "Port Isolation List"]:
            for row in tbl.find_all("tr")[1:]:
                cells = [_text(td) for td in row.find_all("td")]
                if len(cells) >= 2 and not row.find("select"):
                    rows.append({"port": cells[0], "isolated_from": cells[1]})
    return rows


# ---------- port.cgi?page=bw_ctrl ----------

def parse_rate_limits(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    rows: list[dict[str, str]] = []
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if "Ingress Rate Limit (Kbit/sec)" in headers and "Egress Rate Limit (Kbit/sec)" in headers:
            for row in tbl.find_all("tr")[1:]:
                cells = [_text(td) for td in row.find_all("td")]
                if len(cells) >= 3 and not row.find("input"):
                    rows.append({
                        "port": cells[0],
                        "ingress_kbps": cells[1],
                        "egress_kbps": cells[2],
                    })
    return rows


# ---------- loop.cgi ----------

def parse_loop_protocol(html: str) -> dict[str, str]:
    soup = _soup(html)
    out: dict[str, str] = {}
    sel = soup.find("select", {"name": "func_type"})
    if sel:
        out["function"] = _selected_option(sel)
    ival = soup.find("input", {"name": "interval_time"})
    rtime = soup.find("input", {"name": "recover_time"})
    if ival and ival.get("value"):
        out["interval_seconds"] = ival["value"]
    if rtime and rtime.get("value"):
        out["recover_seconds"] = rtime["value"]
    return out


# ---------- loop.cgi?page=stp_global ----------

def parse_stp_global(html: str) -> dict[str, Any]:
    soup = _soup(html)
    out: dict[str, Any] = {}
    rows: dict[str, str] = {}
    target = None
    for tbl in soup.find_all("table"):
        if tbl.find("th", string=re.compile("Spanning Tree", re.I)):
            target = tbl
            break
    if target is not None:
        for row in target.find_all("tr"):
            th = row.find("th")
            td = row.find("td")
            if not th or not td:
                continue
            label = _text(th).rstrip(":").lower().replace(" ", "_")
            sel = td.find("select")
            if sel is not None:
                rows[label] = _selected_option(sel)
            else:
                inp = td.find("input", {"type": "text"})
                rows[label] = inp["value"] if (inp and inp.get("value")) else _text(td)
    out.update(rows)
    return out


# ---------- loop.cgi?page=stp_port ----------

def parse_stp_port(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    rows: list[dict[str, str]] = []
    for tbl in soup.find_all("table", border="1"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if "State" in headers and "Role" in headers:
            data_rows = tbl.find_all("tr")[2:]  # 2 header rows
            for row in data_rows:
                cells = [_text(td) for td in row.find_all("td")]
                if len(cells) >= 10:
                    rows.append({
                        "port": cells[0],
                        "state": cells[1],
                        "role": cells[2],
                        "path_cost_config": cells[3],
                        "path_cost_actual": cells[4],
                        "priority": cells[5],
                        "p2p_config": cells[6],
                        "p2p_actual": cells[7],
                        "edge_config": cells[8],
                        "edge_actual": cells[9],
                    })
    return rows


# ---------- qos.cgi?page=port_pri ----------

def parse_qos_port_queue(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    rows: list[dict[str, str]] = []
    show = soup.find("div", class_="showdiv")
    if show:
        for tbl in show.find_all("table"):
            for row in tbl.find_all("tr")[1:]:
                cells = [_text(td) for td in row.find_all("td")]
                if len(cells) >= 2:
                    rows.append({"port": cells[0], "queue": cells[1]})
    return rows


# ---------- qos.cgi?page=queue_weight ----------

def parse_qos_queue_weights(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    rows: list[dict[str, str]] = []
    show = soup.find("div", class_="showdiv")
    if show:
        for row in show.find_all("tr")[1:]:
            cells = [_text(td) for td in row.find_all("td")]
            if len(cells) >= 2:
                rows.append({"queue": cells[0], "weight": cells[1]})
    return rows


# ---------- igmp.cgi?page=dump ----------

def parse_igmp(html: str) -> dict[str, Any]:
    soup = _soup(html)
    out: dict[str, Any] = {
        "igmp_enabled": "checked" in str(soup.find("input", {"name": "enable_igmp"}) or ""),
        "static_router_ports": [],
        "dynamic_router_ports": [],
        "entries": [],
    }
    # Router port table: two rows (static, dynamic) under one Router Port header
    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")
        if not rows:
            continue
        header_cells = [_text(c) for c in rows[0].find_all(["th", "td"])]
        if header_cells and header_cells[0] == "Router Port":
            port_labels = header_cells[1:]
            for r in rows[1:]:
                cells = r.find_all(["th", "td"])
                if not cells:
                    continue
                kind = _text(cells[0]).lower()
                if kind not in ("static", "dynamic"):
                    continue
                checked_ports: list[str] = []
                for label, cell in zip(port_labels, cells[1:]):
                    inp = cell.find("input")
                    if inp is not None and inp.has_attr("checked"):
                        checked_ports.append(label)
                key = "static_router_ports" if kind == "static" else "dynamic_router_ports"
                out[key] = checked_ports
            break
    # Dump entries table
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if headers[:3] == ["IP Address", "Port", "VLAN ID"]:
            for r in tbl.find_all("tr")[1:]:
                cells = [_text(td) for td in r.find_all("td")]
                if len(cells) >= 3:
                    out["entries"].append({"ip": cells[0], "port": cells[1], "vlan": cells[2]})
            break
    return out


# ---------- dhcp_snooping.cgi?page=dump ----------

def parse_dhcp_snooping(html: str) -> dict[str, bool]:
    soup = _soup(html)
    cb = soup.find("input", {"name": "enable_dhcpsnp"})
    return {"dhcp_snooping_enabled": bool(cb and cb.has_attr("checked"))}


# ---------- fwd.cgi?page=storm_ctrl ----------

def parse_storm_control(html: str) -> list[dict[str, str]]:
    soup = _soup(html)
    rows: list[dict[str, str]] = []
    for tbl in soup.find_all("table"):
        headers = [_text(th) for th in tbl.find_all("th")]
        if (
            "Broadcast Rate (Kbit/sec)" in headers
            and "Known Multicast Rate (Kbit/sec)" in headers
        ):
            for row in tbl.find_all("tr")[1:]:
                if row.find("input") or row.find("select"):
                    continue
                cells = [_text(td) for td in row.find_all("td")]
                if len(cells) >= 5:
                    rows.append({
                        "port": cells[0],
                        "broadcast_kbps": cells[1],
                        "known_multicast_kbps": cells[2],
                        "unknown_multicast_kbps": cells[3],
                        "unknown_unicast_kbps": cells[4],
                    })
    return rows


# ---------- fwd.cgi?page=jumboframe ----------

def parse_jumbo_frame(html: str) -> dict[str, Any]:
    soup = _soup(html)
    enabled = False
    cb = soup.find("input", {"name": "enable_jumbo"})
    if cb is not None:
        enabled = cb.has_attr("checked")
    size = None
    sel = soup.find("select", {"name": "jumboframe"})
    if sel is not None:
        size = _selected_option(sel) or None
    return {"jumbo_frame_enabled": enabled, "size": size}


# ---------- ip.cgi ----------

def parse_ip_config(html: str) -> dict[str, str]:
    soup = _soup(html)
    out: dict[str, str] = {}
    sel = soup.find("select", {"name": "dhcp_state"})
    if sel is not None:
        out["mode"] = _selected_option(sel)
    # Default values are embedded in the JS — pull them out
    txt = html
    for label, key in (("'ip'", "ip"), ("'mask'", "netmask"), ("'gateway'", "gateway")):
        # The IP page templates the static values literally into the JS strings
        pass
    m = re.search(r"id='ip' value='([^']+)'", txt)
    if m:
        out["ip_address"] = m.group(1)
    m = re.search(r"id='mask' value='([^']+)'", txt)
    if m:
        out["netmask"] = m.group(1)
    m = re.search(r"name='gateway' value='([^']+)'", txt)
    if m:
        out["gateway"] = m.group(1)
    return out


# ---------- user.cgi ----------

def parse_users(html: str) -> list[str]:
    soup = _soup(html)
    inp = soup.find("input", {"name": "mname"})
    if inp and inp.get("value"):
        return [inp["value"]]
    return []
