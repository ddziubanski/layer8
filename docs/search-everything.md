# One search for everything (Ctrl K)

Press **Ctrl K** (⌘ K on a Mac) anywhere in the console and type. The box searches the following, with results grouped by type:

| Type | Searched by |
|---|---|
| Pages | Name |
| Tickets | Number or words, including closed tickets, with a preview while one is highlighted |
| Devices | Name, DNS name, serial or model |
| Devices (by address) | An IP address (whole or a prefix such as `10.20.0.`) or a MAC address in any common format |
| People | Name, email, UPN or account name |
| Problems | Number (`PRB-00012`), title or workaround |
| Changes | Number or title |
| Knowledge base articles | Title or text |
| Purchase orders | Number, title, vendor reference or vendor |
| Vendors and systems | Name |

An IP address with no device yet opens in IP addresses (IPAM).

Rules:
- Search is for staff only.
- Each type follows its usual access rules. A problem shows only if you can see every ticket it links.
- Modules switched off in Settings > Modules are left out.
- Up to five results are shown per type. For more, open that page's own search.

API: `GET /api/search?q=` returns `groups`, each with `kind`, `label` and `hits` (`label`, `detail` and `to`, the page to open). Ticket text search stays at `GET /api/tickets?q=`.
