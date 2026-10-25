# Ubiquiti UniFi

Layer 8 can read UniFi networks:
- **Equipment:** gateways, switches and access points, with their state and firmware.
- **Clients:** MAC, IP, the access point or switch they're on, network and SSID, shown beside matching IP records, devices and tickets.
- **Outages:** a ticket for equipment that stays offline, and an internal note when it's back.
- **Guest Wi-Fi vouchers** (optional): technicians make hotspot vouchers from a ticket.

Everything is off by default. Apart from vouchers, Layer 8 only reads, and what it reads never changes your documented IP records.

**Settings > UniFi** has every option. **IP addresses > UniFi** shows what was read.

## Choose how to connect

| | Cloud (recommended) | Local console |
| --- | --- | --- |
| How | Ubiquiti's **Site Manager API** at `api.ui.com` lists your consoles; the **Connector Proxy** reaches each console's own Network API through the cloud | The console's **Network Integration API** at `https://<console>/proxy/network/integration/v1` |
| Needs | Console firmware 5.0.3 or later; outbound HTTPS from Layer 8 to `api.ui.com` | UniFi Network 9 or later; Layer 8 able to reach the console over HTTPS |
| API key | unifi.ui.com > **API** > Create API key | UniFi Network > **Settings > Integrations** > Create API key |
| Consoles | Test connection lists those the key can see; paste their IDs into **Consoles** | The one console at **Console address** |
| Certificate | Public, nothing to do | UniFi consoles use their own self-signed certificate out of the box: paste it into **Console CA certificate** (see below) |

Use a key from an account with read-only access where you can. Guest vouchers need a key that may manage the hotspot.

## Set it up

1. Settings > UniFi: choose **Connection**, paste the **API key**, and fill in **Consoles** (cloud) or **Console address** (local, plus the CA certificate if needed). Save.

   **The console's own certificate (local).** A UniFi console's factory certificate is self-signed and names only `unifi.local`, so neither the server's trusted CAs nor the console's IP address will match it. Paste the certificate itself into **Console CA certificate**; Layer 8 then trusts exactly that certificate, at whatever address you use, and checks it before sending the API key. To get it, from a machine on the same network:

   ```
   openssl s_client -connect 192.168.1.1:443 </dev/null 2>/dev/null | openssl x509
   ```

   Compare its fingerprint (`openssl x509 -noout -fingerprint -sha256`) with the one the console shows, if you can, before trusting it. If the console's certificate changes (a reset, or a new one installed), paste the new one. If you paste the CA of a certificate your own CA issued instead, the usual checks apply, including the name.
2. Press **Test connection**. In cloud mode it lists every console the key can see, with its ID, and the sites on the consoles you chose. Optionally limit **Sites** to some of them.
3. Tick **Read UniFi networks**, save, and press **Read UniFi now**. Settings > Status shows how the read went. From then on it reads every **Read UniFi every** minutes (15 by default).
4. Optional: **Open tickets for offline UniFi devices**. A device offline longer than the chosen minutes (10 by default, to ride out reboots and firmware updates) gets one high-priority ticket in the chosen queue. When it comes back online, an internal note is added for review; the ticket isn't closed for you. UniFi's official API has no alerts it can push, so this follows each read.
5. Optional: **Guest Wi-Fi vouchers from tickets**, plus the permission *Make UniFi guest Wi-Fi vouchers* (Settings > Permissions, admins by default). A ticket then has a **Guest Wi-Fi** panel: choose how long and how many guests, and the voucher code shows there and as an internal note on the ticket.

All the settings can go in an answers file under `unifi` (`python -m app.cli settings import`), and a `HELPDESK_UNIFI_*` environment variable overrides any of them.

## What happens on each read

- Every selected console and site is read in full: sites, equipment and clients, page by page. Only then does the new snapshot replace the old one. If anything fails (the console is offline, the key is refused, an answer looks wrong), the last complete read is kept and the error shows in Settings > Status.
- Clients without a MAC (VPN users) are skipped. Matching to IP records is by exact MAC only, never by name or IP.
- Observations older than twice the read interval (at least 30 minutes) are marked stale.

## Limits and checks

- This was built against Ubiquiti's published API and tested with mocked responses. Check it against your console with **Test connection** and a first **Read now** before relying on it. UniFi Network's API has grown with each release, so some fields (for example a client's switch port) appear only on newer versions.
- The classic, unofficial controller API (cookie sign-in with an admin password) isn't used.

## Credits

The map of UniFi's APIs (Site Manager, Connector Proxy, Network Integration and the classic API), and their paging and rate-limit behaviour, comes from **"UniFi best practices" by uchkunr**: https://github.com/uchkunr/unifi-best-practices, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Ubiquiti's own documentation is at https://developer.ui.com. Layer 8's code is its own. UniFi and Ubiquiti are trademarks of Ubiquiti Inc.
