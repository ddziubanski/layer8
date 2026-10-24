# Global module review

The real React app uses synthetic local API fixtures and the backend's actual
module settings descriptions. No production settings or credentials were used.

- `layer8-global-modules-admin.jpg`: IPAM, Scripts and technician Settings saved
  off; their sidebar links disappear while the admin retains Settings recovery.
- `layer8-global-modules-preferences.jpg`: globally disabled choices are unchecked
  and locked in My preferences. Show all leaves them unavailable; Ctrl+K omits
  Scripts. Personal hiding remains a separate account preference.
- `layer8-global-modules-disabled-route.jpg`: an agent's bookmarked IPAM path
  displays the unavailable page and a return-to-Tickets link. Core navigation and
  the AGPL/source-offer links remain available.

Save updates the admin's sidebar without a page reload, and a fresh technician
preview receives the same policy. Desktop width/scroll width both measured 1447
CSS pixels in the settings view (no horizontal overflow). The disabled-route
capture used a 2560 CSS pixel viewport. A requested tablet viewport did not apply
reliably on this host, so these captures do not establish tablet validation.

Automated tests separately check global-over-personal precedence, admin-only
settings changes, CLI import/environment locks, direct-route blocking before page
API queries, immediate navigation updates, core helpdesk/source availability and
preservation of existing permissions and integration feature flags.
