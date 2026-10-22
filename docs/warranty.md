# Warranty lookups (Dell, Lenovo)

Layer 8 can ask Dell and Lenovo for a device's warranty using their own APIs, with your company's credentials. It only reads. It doesn't use the makers' support websites or any undocumented endpoint, and nothing is sent to them except serial numbers.

Which devices: active ones whose **manufacturer** contains "Dell" or "Lenovo" and that have a **serial number** (for Dell, the service tag). Action1 fills both for the machines it manages; otherwise type them on the device page.

## Getting credentials

- **Dell:** sign in to TechDirect (https://tdm.dell.com) with a company account and request the Warranty API under APIs. Dell reviews the request by hand, which usually takes a few days. Once approved, TechDirect shows a client ID and secret.
- **Lenovo:** the warranty API needs a ClientID token issued by Lenovo. Ask your Lenovo account representative, or your partner manager if you buy through a partner. There's no self-service sign-up.

Enter them under Settings > Warranty lookups. The Dell client secret and the Lenovo ClientID are stored as secret files and never shown again. The server needs outbound HTTPS to `apigtwb2c.us.dell.com` and `supportapi.lenovo.com`.

## What happens

- **When:** the worker job "warranty" runs every 6 hours. It looks up devices that were never looked up, whose serial has changed, or that were last looked up more than "Look each device up again after (days)" ago (30 by default). It handles up to 1,000 devices per run, 100 serials per request.
- **What's kept:** the maker's answer is stored per device: product, ship date, purchase date (Lenovo, when registered), and each entitlement with its dates. The warranty end is the latest end of any entitlement, contracts and extended cover included.
- **Filling gaps:** if the device's own warranty end, purchase date or model is empty, it's filled from the answer. The ship date is used for the purchase date when the maker has no purchase date.
- **Differences:** a date that differs from the device's is not overwritten. The device page shows "Warranty from Dell" with the cover list, and a **Use Dell's dates** button.
- **Look up now:** a button on the device page looks the device up straight away.
- **Errors:** if a maker refuses (bad credentials, or Dell's Warranty API not approved), the job says so in Settings > Status and the system log, and the other maker carries on. A serial the maker doesn't know is shown on the device.

Devices > Lifecycle (warranties ending, the replacement forecast) works from the device's own dates, so filled-in dates feed it straight away.

API: `GET /api/devices/{id}/warranty`, `POST /api/devices/{id}/warranty/lookup`, `POST /api/devices/{id}/warranty/apply`.

Not yet: HP (its warranty API also needs a key from HP).
