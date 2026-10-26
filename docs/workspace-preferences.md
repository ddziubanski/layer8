# Workspace modules

## Global modules (administrators)

Open **Settings > Modules** to choose the workspace areas available to your organization. All modules default on. Each switch is a checkbox; save to apply. The saving admin sees navigation update immediately; other signed-in clients refresh within 30 seconds while active or when they regain focus.

Global-off areas disappear from workspace navigation and Ctrl+K, and direct console links show an unavailable page without loading that page's API queries. A technician's **Show all modules** cannot restore them. Existing personal hidden choices are retained, so restoring a global module does not undo someone's workspace preferences.

**Tickets, My preferences and sign-out remain available.** Admins always retain Settings access to restore modules. **Settings for technicians** affects staff console settings pages, not administrator recovery access. **Self-service portal shortcut** affects staff navigation only: requester self-service and public helpdesk links remain available. Knowledge base controls staff pages; requester articles remain part of the portal. Source code links remain available regardless of module choices.

These controls govern the console workspace, not authorization or a service shutdown. Data, embedded ticket context and existing API permissions are preserved. Use the existing integration and worker settings to stop background reads, scheduled reports/scripts or other jobs. Public receiving links, remote displays, surveys and the requester portal keep their existing controls. No database migration is needed.

The settings API is admin-only: `GET/PUT /api/settings/modules`, using `{"values": {...}}` for updates. `GET /api/auth/me` returns the effective `disabled_modules` list alongside personal `hidden_modules`; clients must apply the global list first. The admin Settings exception is intentional. `HELPDESK_WORKSPACE_*` environment overrides show as locked in the form.

Installer answers import supports the same section: `python -m app.cli settings import answers.json`:

```json
{
  "modules": {
    "workspace_dashboard_enabled": true,
    "workspace_devices_enabled": true,
    "workspace_changes_enabled": false,
    "workspace_purchasing_enabled": false,
    "workspace_vendors_enabled": false,
    "workspace_ipam_enabled": false,
    "workspace_kb_enabled": true,
    "workspace_scripts_enabled": false,
    "workspace_reports_enabled": true,
    "workspace_settings_enabled": true,
    "workspace_portal_shortcut_enabled": true
  }
}
```

## Personal workspace

Open **My preferences > My workspace** to choose what appears in your sidebar. The full sidebar is the default.

- **Use minimal workspace** keeps Tickets, Dashboard, Devices and Knowledge base.
- Use **Show in my sidebar** checkboxes to choose individual optional modules.
- **Show all modules** restores all globally available sidebar areas.
- **Save** applies the choice immediately. **Undo changes** restores your last saved preferences. Unsaved changes do not affect navigation.

Tickets, My preferences and sign-out always remain available. This choice follows your Layer 8 account across browsers; it is not a shared setting for other technicians. The portal's requester navigation is unchanged.

## Ticket list

**My preferences > Ticket list** sets how your queue looks and loads. Only you see these, and they follow your account.

- **Density:** *Comfortable* (the default) or *Compact*, which fits about twice as many tickets on screen.
- **Long lists:** *Keep scrolling* (the default) loads the next batch as you reach the end of the list, with a **Load more** button as well. *Pages* shows Previous and Next instead.
- **Tickets loaded at a time** (or **per page**): 25, 50, 100 or 200.

While scrolling, the list refreshes every 30 seconds until you've loaded more than five batches; after that, changing the view or queue starts it fresh. **Select all loaded** selects every ticket loaded so far.

The API field is `queue_list` on `GET /api/auth/me` and `PUT /api/auth/me/preferences`, for example `{"queue_list": {"density": "compact", "paging": "scroll", "page_size": 50}}`; `null` restores the defaults. It's stored under `personal_queue_list:<user id>` in `app_settings`. Requesters can't set it.

## Hiding and access

Personally hiding a module changes its sidebar entry only. It does not disable the module, change another technician's workspace, change permissions, stop background jobs or remove APIs. Direct links keep working, and the **Ctrl+K / ⌘K** palette can find personally hidden modules while they are globally available. Administrators control global workspace availability in Settings > Modules and integration-wide feature switches in their existing Settings tabs.

## API and storage

`GET /api/auth/me` includes `hidden_modules`, an array of sidebar module IDs. Save your own choices with `PUT /api/auth/me/preferences`, alongside or independently of `ticket_color` and `color_rules`:

```json
{"hidden_modules": ["changes", "purchase-orders", "vendors", "ipam", "scripts", "reports", "settings", "portal"]}
```

The allowed IDs are `dashboard`, `devices`, `changes`, `purchase-orders`, `vendors`, `ipam`, `kb`, `scripts`, `reports`, `settings` and `portal`. Unknown IDs, including the fixed Tickets and My preferences links, are rejected. Duplicates are removed. An omitted field preserves the saved value, so older clients saving colours do not erase navigation; `[]` or `null` restores all modules.

The authenticated account determines ownership; the API accepts no target user ID. Preferences use the existing `app_settings` table with a `personal_navigation:<user id>` key and no secrets. Backups and full-install exports preserve this table; no database migration or installer configuration is required. Existing users see the full sidebar until they save a personal selection.

## Ticket Focus navigation and themes

The standard console uses workspace navigation. When every destination in a
workspace is hidden or globally unavailable, that workspace disappears. A
personal hide still permits Ctrl+K/direct navigation; a global-off module stays
unavailable through console routes. Appearance choices are in **My preferences >
Appearance**, with five paired palettes and an organization-default reset. See
[ticket workspace](ticket-workspace.md) for the complete theme and tab behavior.
