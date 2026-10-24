# Nutanix

Layer 8 can read your Nutanix AHV VMs from **Prism Central**:
- **VMs:** power state, host and cluster, vCPUs and memory, IP and MAC addresses, and whether Nutanix Guest Tools answer. **Devices > Virtual machines** lists them.
- **Devices:** a VM whose name matches a device (an AD computer or one added by hand) shows on that device. If two devices share the name, nothing is matched.
- **IP addresses:** each VM's MAC and IP show beside matching IP records, like UniFi and Juniper clients. Documented records are never changed.
- **Tickets:** link VMs to a ticket in its **Virtual machines** panel. VMs of the ticket's computer and linked devices show there too.
- **Power actions** (optional): restart, power on or power-cycle a ticket's VM. Each one is noted on the ticket, and so is how it ended.
- **Critical VMs** (optional): a ticket when one is down, and a tile and alert on remote displays.

Everything is off by default. **Settings > Nutanix** has every option.

## What you need

- Prism Central **pc.2024.1 or later**: Layer 8 uses the v4 REST API.
- Layer 8's server must reach Prism Central over HTTPS (port 9440 by default). Unlike Juniper, this doesn't go through a relay.
- A Prism Central account for Layer 8:
  - to read: a role that may view VMs, clusters and hosts (for example the built-in *Prism Viewer*);
  - for power actions as well: a role that may power VMs on and off and restart them (a custom role with the VM power operations, or *VM Admin* limited to the right VMs).

  Use a dedicated account, not a person's.

## Set it up

1. Settings > Nutanix: fill in the **Prism Central address** (for example `https://prism.corp.example:9440`), **Login** and **Password**. If Prism Central's certificate isn't from a public CA, paste the CA certificate into **Prism Central CA certificate**. For a quick test with its self-signed certificate on a network you trust, untick **Check Prism Central's certificate** (leave it ticked otherwise).
2. Tick **Read Nutanix** and save.
3. Press **Test connection**. It lists the clusters and how many VMs the account can see.
4. Press **Read Nutanix now**. Settings > Status shows how the read went. From then on it reads every **Read Nutanix every** minutes (15 by default).
5. Optional, power actions: tick **Power actions from tickets**, then choose who may use them in Settings > Permissions:
   - *Restart or power on Nutanix VMs* (admins by default);
   - *Power-cycle Nutanix VMs (hard restart)* (admins by default). This is a separate permission, because unsaved work in the VM is lost.

All settings can go in an answers file under `nutanix` (`python -m app.cli settings import`), and a `HELPDESK_NUTANIX_*` environment variable overrides any of them.

## Power actions

On a ticket, open **Virtual machines** (in the tools drawer: **Devices**). Each VM shows the actions you may take for its state:

| Action | When | What Prism Central does |
| --- | --- | --- |
| Restart | The VM is running | A graceful restart: through Nutanix Guest Tools when they answer (`guest-reboot`), otherwise through ACPI (`reboot`) |
| Power on | The VM isn't running | `power-on` |
| Power cycle | The VM is running, and you have the power-cycle permission | `power-cycle`: a hard restart |

You confirm each action first. Then:
- An internal note on the ticket says who asked for what.
- Prism Central gives a task. Layer 8's worker checks it every 30 seconds and adds a second note when it ends ("finished", "failed" with Nutanix's reason, or no known result after 30 minutes).
- Only one action per VM runs at a time.

Nutanix's own documentation warns that a graceful restart of a Windows VM can quietly do nothing while a screen saver is running. If a restart "finished" but the VM didn't restart, check that.

## Critical VMs

List the VMs that matter most in **Critical VMs**, one name per line (not case-sensitive). After each read:
- A critical VM that isn't running, or is gone from Prism Central, counts as **down**.
- With **Open tickets for critical VMs that are down** on, a VM down for longer than **Down for (minutes) before a ticket** (10 by default) gets one high-priority ticket, "Offline: Nutanix VM <name>", in the chosen queue. When it runs again, an internal note is added for review; the ticket isn't closed for you.
- A VM shut down on purpose still counts. Take it off the list first, or close the ticket.
- A name that was never seen in Prism Central is named in Settings > Status (most likely a typo).

With **Show critical VMs on remote displays** on, every remote display gets a "Critical VMs down" tile and a critical alert naming each VM that's down. It's off by default, because remote displays are often on screens anyone can read.

## What happens on each read

- Every VM page is read, plus cluster and host names. If the VM list can't be read in full, nothing changes and the error shows in Settings > Status. Cluster and host names are optional: an account that can't list them still reads VMs.
- A VM that's gone from Prism Central is kept, marked *Gone from Nutanix*, so ticket links and the action history stay readable.
- Data older than twice the read interval (at least 30 minutes) is marked stale.

## Limits

- Built against Nutanix's published v4 API reference and tested with a mocked Prism Central. Check it against yours with **Test connection** and a first **Read now** before relying on it, and try a power action on a test VM first.
- Prism Element on its own (without Prism Central), and the older v2 and v3 APIs, aren't supported.

Nutanix, Prism and AHV are trademarks of Nutanix, Inc.
