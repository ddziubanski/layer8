# Juniper EX switches

For Juniper EX switches configured one by one (not managed in Juniper Mist). A runner on your LAN reads each switch's **Junos REST API** for its MAC and ARP tables. Each client (a MAC and its IP) then shows with the switch, port and VLAN it's on: beside matching IP records, on devices and tickets, and under **IP addresses > Juniper**.

It only reads, and it never changes your documented IP records. It's off by default. **Settings > Juniper** has every option.

Switches managed in Mist would be read through Mist's cloud API instead. That isn't built.

## On each switch

1. A login for Layer 8, with a read-only class:

   ```
   set system login user layer8-read class read-only authentication plain-text-password
   ```

2. The REST API over HTTPS. It needs a certificate, either your own or one generated on the switch:

   ```
   set system services rest https port 3443
   set system services rest https server-certificate <certificate-name>
   set system services rest control allowed-sources <the runner's address>
   ```

   `allowed-sources` keeps the API to the runner only. Commit.

## In Layer 8

1. A **runner** that can reach the switches over HTTPS and runs Python (runner 1.2 or later, so secrets reach the script sealed). See [runner.md](runner.md).
2. **Settings > Juniper:**
   - **Switches:** one per line, a name and the address, plus the port if it isn't 3443:
     ```
     core-1 10.0.0.2
     idf2-sw idf2-sw.corp.example:8443
     ```
   - **Login** and **Password:** the read-only login above. The password is stored as a secret and reaches the runner sealed for each read; it's never in the run's values or output.
   - **Switches' CA certificate:** the CA that signed the switches' REST certificates, if it isn't publicly trusted. For a quick test with self-signed certificates on a network you trust, untick **Check the switches' certificates** (leave it ticked otherwise).
   - **Runner:** the runner that reaches the switches. Empty: any runner that takes Python.
   - Tick **Read Juniper switches** and save.
3. Press **Read the switches now**. Scripts > All runs shows the run; Settings > Juniper shows the result, e.g. "2 of 2 switches answered, 140 clients". From then on it reads every **Read every** minutes (30 by default).

All settings can go in an answers file under `junos`, and `HELPDESK_JUNOS_*` environment variables override them.

## What it reads

For each switch, as JSON, at `https://<switch>:<port>/rpc/<name>`:

| RPC | Used for |
| --- | --- |
| `get-software-information` | Host name, model, Junos version (shown under IP addresses > Juniper) |
| `get-arp-table-information` | MAC → IP (switches that route have the clients' ARP entries) |
| `get-ethernet-switching-table-information` | MAC → VLAN and port. The ELS output (EX2300, EX3400, EX4300 and later) and the older format are both read |

Each MAC is matched to an IP from any switch's ARP table. A port with **at least** the uplink number of MACs (20 by default, see **A port with this many MACs is an uplink**), and aggregated, IRB and management interfaces, count as uplinks and are skipped, so each client shows on its access port.

## When a switch doesn't answer

The other switches' results are still stored. The silent switch keeps its last clients, which turn stale after twice the read interval, and it shows as not answering. With **Open tickets for unreachable switches** on, a switch that stays silent past the grace period (10 minutes by default) gets one ticket. When it answers again, an internal note is added for review; the ticket isn't closed for you.

## Not yet

- On-demand port diagnostics (interface status and error counters) from a ticket.
- LLDP neighbours through REST. For now, SNMP discovery (IP addresses > Network gear) already reads LLDP and MAC tables from Juniper switches.
- Juniper Mist.

This was built against Juniper's documented Junos REST API and tested with a local HTTPS server answering in the Junos JSON format. Check it against your own switches before relying on it. Juniper and Junos are trademarks of Juniper Networks, Inc.
