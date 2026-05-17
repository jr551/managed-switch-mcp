"""End-to-end smoke test against a live switch.

Calls every read-only tool, prints a short summary, and exits non-zero on
any failure. Intended for development against your own hardware:

    SWITCH_HOST=192.168.16.3 python -m managed_switch_mcp.tests.smoke
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from typing import Any, Awaitable, Callable

from .. import server as srv


def _shape(value: Any) -> str:
    if isinstance(value, list):
        sample = value[0] if value else None
        return f"list[{len(value)}] e.g. {json.dumps(sample, default=str)[:140]}"
    if isinstance(value, dict):
        keys = list(value.keys())[:8]
        return f"dict keys={keys}"
    return repr(value)[:140]


async def _run() -> int:
    tools: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
        ("get_device_info", srv.get_device_info),
        ("list_ports", srv.list_ports),
        ("get_port_statistics", srv.get_port_statistics),
        ("get_mac_table", srv.get_mac_table),
        ("get_vlan_list", srv.get_vlan_list),
        ("get_port_vlan_config", srv.get_port_vlan_config),
        ("get_port_aggregation", srv.get_port_aggregation),
        ("get_port_mirror", srv.get_port_mirror),
        ("get_port_isolation", srv.get_port_isolation),
        ("get_port_rate_limits", srv.get_port_rate_limits),
        ("get_loop_protocol", srv.get_loop_protocol),
        ("get_stp_global", srv.get_stp_global),
        ("get_stp_port", srv.get_stp_port),
        ("get_qos_port_queue", srv.get_qos_port_queue),
        ("get_qos_queue_weights", srv.get_qos_queue_weights),
        ("get_igmp_config", srv.get_igmp_config),
        ("get_dhcp_snooping", srv.get_dhcp_snooping),
        ("get_storm_control", srv.get_storm_control),
        ("get_jumbo_frame", srv.get_jumbo_frame),
        ("get_ip_config", srv.get_ip_config),
        ("get_users", srv.get_users),
        ("backup_config", srv.backup_config),
    ]

    failures = 0
    for name, fn in tools:
        try:
            result = await fn()
        except Exception:
            failures += 1
            print(f"[FAIL] {name}")
            traceback.print_exc()
            continue
        # Don't dump huge base64 to stdout
        if name == "backup_config" and isinstance(result, dict):
            result = {k: v for k, v in result.items() if k != "base64"}
            result["base64"] = "<omitted>"
        print(f"[ ok ] {name}: {_shape(result)}")

    cli = srv._get_client()
    await cli.aclose()
    if failures:
        print(f"\n{failures} tool(s) failed.")
        return 1
    print("\nAll tools succeeded.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
