# GoodTop GT-ST018M research notes

Notes from poking at a **GoodTop GT-ST018M** managed switch (firmware **V200.1.8**). Hardware is ODM'd by **Shenzhen HongRui Optical Technology** (registered to OUI `1C:2A:A3`, MA-L block issued 2019-08-13). GoodTop sells the unit on AliExpress and Newegg; the manual and firmware live at `goodtop-tech.com`. The web UI is a stock Realtek-SDK affair, so this very likely also works against AliExpress clones that ship the same UI — issues welcome if you have one.

> Everything here was learned with **owner-authorised** read-only probing of one switch on a private LAN. Nothing here is an unpatched zero-day or a remote exploit — it's local hardware-hacking against a device you have on your desk. Don't aim any of this at gear you don't own.

## Hardware

What the box tells us about itself (the firmware refuses to be specific, but the symptoms are unambiguous):

- **9 user-facing ports**, indexed from the UI as Port 1–Port 9. Ports 1–2 are physically bonded into the default **Trunk1** (so the UI presents 8 logical ports + Trunk1).
- **Ports 1–8 are 2.5GBASE-T** (negotiated speeds advertised include 10/100/1000/2500M).
- **Port 9 is "extended"** with 100M / 1G / 2.5G / **10G** options — almost certainly a 10G SFP+ cage.
- Form-factor smells strongly like a **Realtek-managed-switch reference design** — likely an **RTL8373/RTL8254x** family SoC with a small MIPS/lexra-style CPU and an external SPI NOR flash. The firmware-side hints (the `hi-lo` 64-bit counter splitting, the way ports are internally numbered `port0..port10` while displayed as Port 1..9 + Trunk1, the menu structure, the binary config layout) all line up with Realtek's stock managed-switch SDK.
- Management is **HTTP only** on TCP/80. A port sweep of the most common service ports (22, 23, 80, 161, 443, 4444, 8080, 9999) showed **only 80 open**. No SSH, no telnet, no SNMP, no HTTPS, no syslog daemon. The "managed" surface is entirely the web UI.

## Firmware: what the web UI exposes

| Area | Pages | Read | Write |
|---|---|---|---|
| Identity | `/info.cgi` | model, FW ver, IP, MAC, uptime, port link table | rename device |
| Ports | `/port.cgi` (+ `?page=stats`, `mirroring`, `isolation`, `bw_ctrl`) | per-port state/duplex/speed/flow-control + Tx/Rx counters | enable/disable, force speed, mirror, isolate, rate-limit |
| MAC | `/mac.cgi?page=fwd_tbl` / `static` | dynamic + static fwd table | add/remove static, clear table |
| Trunks | `/trunk.cgi?page=group` | LACP / static groups | create/remove |
| VLAN | `/vlan.cgi?page=static` + `?page=port_based` | static VLAN list, per-port type | add/remove VLAN, set access/trunk |
| STP/loop | `/loop.cgi` + `?page=stp_global` + `?page=stp_port` | loop-prev mode, STP root + per-port | full STP config |
| QoS | `/qos.cgi?page=port_pri` + `?page=queue_weight` | port→queue, queue weights | reassign |
| IGMP | `/igmp.cgi?page=dump` | snooping enable, router ports, entries | enable/disable + static router port |
| DHCP snoop | `/dhcp_snooping.cgi?page=dump` | enable state | toggle |
| Storm ctrl | `/fwd.cgi?page=storm_ctrl` | per-port rates | set / clear |
| Jumbo | `/fwd.cgi?page=jumboframe` | enable + MTU | toggle / set |
| IP | `/ip.cgi` | mgmt IP/mask/gw/DHCP | reconfigure |
| Users | `/user.cgi` | usernames | set password (single account, `admin`) |
| Config | `/config_back.cgi` | binary backup | restore + firmware upgrade |
| Reboot | `/reboot.cgi`, `/reset.cgi`, `/save.cgi` | — | reboot / factory reset / commit-to-flash |
| Upgrade | `/fwug.cgi` | shows current FW | `cmd=enter_loader` → uploads firmware blob |

## Security observations

Things that stand out poking at it. None of these are scandals — they're typical of $50 AliExpress managed switches — but worth knowing before you put one anywhere except a lab:

1. **HTTP only.** Credentials, session cookie, and the config blob all cross the wire in plaintext.
2. **MD5 challenge in JavaScript, not the server.** The login flow is: client computes `md5(username + password)`, sets a cookie `admin=<md5>`, **and** posts that same hash in a `Response` form field along with username + password in cleartext. The server reads the cookie alone for subsequent requests, so anyone who sniffs one login knows the long-term auth token for that account.
3. **One account.** `/user.cgi` only supports a single `admin` user. No roles, no per-feature auth.
4. **Referer-only XSRF defence.** Most CGI handlers (every `?page=...` view tested) return `404` when the request has no `Referer:` header, but accept any value as long as one is present. That makes accidental remote CSRF unlikely *only* because browsers send Referer; it is no defence against a logged-in user being phished into clicking a malicious link.
5. **Plaintext config dump.** `GET /config_back.cgi?cmd=conf_backup` returns `switch_cfg.bin` (~2.6 KB). The first 32 bytes are the network settings in plain binary; the username is plaintext; the password slot is the MD5 hash of `username+password` lightly obfuscated with what looks like a fixed-length XOR mask. With one known plaintext/hash pair you can recover the mask offline and decode any other captured config blob.
6. **No CSRF token, no rate-limit on `/login.cgi`.** Online password guessing is trivially possible.
7. **`/reset.cgi`, `/reboot.cgi`, `/fwug.cgi`** are all reachable to anyone logged in; there's no second-factor or confirmation challenge server-side (the "Confirm?" dialog is JavaScript-only).

If you absolutely must use this thing in production, put it on a management VLAN with no L3 path from anywhere else and treat the web UI as compromised.

## Getting root / dumping firmware

The vendor doesn't ship root or a CLI — there's no telnet/SSH and no debug page. So all the standard approaches are hardware-side:

### 1. UART serial console (most common starting point)

Almost every Realtek-SDK managed switch in this price bracket exposes a 3.3 V UART on a 4-pin (TX/RX/GND/VCC) header or set of test pads on the PCB. Typical baud is **115200 8N1**.

- Pop the case (usually four screws under rubber feet).
- Look for an unpopulated 4-pin header near the SoC, or a row of small square pads labelled `TX`, `RX`, `GND`. A cheap USB-UART (CP2102 / CH340 / FT232) at 3.3 V is enough.
- Boot the device; you'll see a U-Boot banner. If it's the stock Realtek/Lexra U-Boot you'll usually get **stop autoboot with any key** and a prompt like `RTL838x#` or `<RealtekSwitch>`.
- From U-Boot you can: dump flash to memory and tftpput it out, set `bootargs` to start `init=/bin/sh`, or load a custom boot image over TFTP. This is the safest non-destructive path because you don't have to write to flash.

If autoboot can't be interrupted, the next escalation is **glitching the boot ROM's serial check** or **shorting flash MISO during boot** — both well documented in OpenWrt's "porting a generic Realtek switch" pages.

### 2. Dump the SPI flash directly

If the SoC won't give up its bootloader prompt, the flash chip almost certainly will:

- Identify the **SPI NOR** chip on the board (usually a small SOIC-8 next to the SoC, marked W25Q*, MX25L*, GD25Q*, etc.).
- Read it in-circuit with a cheap **CH341A programmer** and an SOIC-8 clip. For Realtek-managed switches the CPU usually tri-states its flash bus while held in reset, so you don't even need to desolder; if you get garbled reads, lift the chip with hot-air.
- The resulting image is usually a concatenation of `u-boot` + `u-boot env` + one or two squashfs filesystems + a config partition. `binwalk -e` will decompose it and `fakeroot unsquashfs` will give you the rootfs.

That rootfs is what you want for **finding default credentials**, **understanding the CGI handlers** (the binaries serving `/info.cgi` and friends are usually a single `httpd` linked against a `libswitch.so`-style SDK blob), and reverse-engineering the binary config's password obfuscation.

### 3. Web-UI firmware upgrade (risky — last resort)

`/fwug.cgi` posts `cmd=enter_loader` then expects a firmware blob upload. The vendor never published the firmware image format, so flashing a custom build through this path is a **brick-the-switch-on-the-first-try** kind of operation. Don't go here without first having UART access so you can recover via U-Boot.

### 4. OpenWrt / community firmware

OpenWrt has growing support for Realtek-managed-switch SoCs (`realtek` target — see the upstream OpenWrt wiki and the rtl838x-support thread). Whether *this exact* model can run a community firmware depends on which SoC it's actually using; that determination needs the UART log or a flash dump from steps 1–2. Once a board is known to use a supported SoC, the porting process is:

1. Boot the original FW once, capture the boot log over UART (clocks, board ID, MDIO/SerDes topology).
2. Build an OpenWrt initramfs image for the matching subtarget.
3. Boot it over TFTP from U-Boot (no flash writes — fully reversible).
4. Verify all ports, then commit to flash.

The community is the best place to share findings — there's no reason this particular OEM blob has to be on the box.

## Useful references for similar gear

- OpenWrt's `realtek` target docs and the long-running rtl838x community thread (search "OpenWrt Realtek switch support") for SoC family details.
- Hardkernel / Sven Roederer's writeups on dumping CH341A images from in-circuit SPI NOR.
- `binwalk`, `squashfs-tools`, `ghidra` / `radare2` for binary RE of the recovered httpd.

## What this MCP server uses

This server only touches the **read-only** parts of the table above. Every dangerous endpoint (`/reboot.cgi`, `/reset.cgi`, `/fwug.cgi`, `/save.cgi`, `/ip.cgi POST`, `/user.cgi POST`, port-state changes, VLAN edits, …) is deliberately *not* wrapped, so the LLM client can introspect the switch but cannot meaningfully modify it.

If you want to extend it, the safest next-step tools to add are also read-only:

- `parse_config_backup` — decode the binary `switch_cfg.bin` and return its known fields as JSON.
- `port_traffic_delta` — call `get_port_statistics` twice and return the per-port pps/bps over the interval.
- Compare-vs-baseline tools (e.g. flag MAC table churn).

Anything that *writes* should be a separate, explicitly-named server with explicit per-call confirmation.
