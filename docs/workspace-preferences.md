# Personal workspace

Open **My preferences > My workspace** to choose what appears in your sidebar. The full sidebar is the default.

- **Use minimal workspace** keeps Tickets, Dashboard, Devices and Knowledge base.
- Use **Show in my sidebar** checkboxes to choose individual optional modules.
- **Show all modules** restores the complete sidebar.
- **Save** applies the choice immediately. **Undo changes** restores your last saved preferences. Unsaved changes do not affect navigation.

Tickets, My preferences and sign-out always remain available. This choice follows your Layer 8 account across browsers; it is not a shared setting for other technicians. The portal's requester navigation is unchanged.

## Hiding and access

Hiding a module changes its sidebar entry only. It does not disable the module, change another technician's workspace, change permissions, stop background jobs or remove APIs. Direct links keep working, and the **Ctrl+K / ⌘K** palette can find hidden modules under the same existing permissions. Administrators still control integration-wide feature switches in Settings.

## API and storage

`GET /api/auth/me` includes `hidden_modules`, an array of sidebar module IDs. Save your own choices with `PUT /api/auth/me/preferences`, alongside or independently of `ticket_color` and `color_rules`:

```json
{"hidden_modules": ["changes", "purchase-orders", "vendors", "ipam", "scripts", "reports", "settings", "portal"]}
```

The allowed IDs are `dashboard`, `devices`, `changes`, `purchase-orders`, `vendors`, `ipam`, `kb`, `scripts`, `reports`, `settings` and `portal`. Unknown IDs, including the fixed Tickets and My preferences links, are rejected. Duplicates are removed. An omitted field preserves the saved value, so older clients saving colours do not erase navigation; `[]` or `null` restores all modules.

The authenticated account determines ownership; the API accepts no target user ID. Preferences use the existing `app_settings` table with a `personal_navigation:<user id>` key and no secrets. Backups and full-install exports preserve this table; no database migration or installer configuration is required. Existing users see the full sidebar until they save a personal selection.
