# SPDX-License-Identifier: AGPL-3.0-or-later
"""Global console availability, separate from authorization and integration/job controls."""
from app.config import settings

# ID matches personal navigation; settings remain explicit bools for API/UI/CLI parity.
MODULES = (
    ("dashboard", "workspace_dashboard_enabled", "Dashboard", "Workload and SLA overview."),
    ("devices", "workspace_devices_enabled", "Devices", "Standalone device inventory pages."),
    ("changes", "workspace_changes_enabled", "Changes", "Change calendar and change pages."),
    ("problems", "workspace_problems_enabled", "Problems", "Problem investigations, known errors and linked incidents."),
    ("purchase-orders", "workspace_purchasing_enabled", "Purchase orders", "Purchasing pages and printable receiving sheets."),
    ("vendors", "workspace_vendors_enabled", "Vendors", "Standalone vendor pages."),
    ("ipam", "workspace_ipam_enabled", "IP addresses (IPAM)", "Standalone subnet, VLAN and address pages."),
    ("kb", "workspace_kb_enabled", "Knowledge base", "Staff knowledge base pages; requester articles remain available."),
    ("scripts", "workspace_scripts_enabled", "Scripts", "Standalone script library pages."),
    ("reports", "workspace_reports_enabled", "Reports", "Standalone report pages."),
    ("settings", "workspace_settings_enabled", "Settings for technicians", "Admins always retain Settings access so they can restore modules."),
    ("portal", "workspace_portal_shortcut_enabled", "Self-service portal shortcut", "Staff navigation shortcut only. Requester self-service remains part of the core helpdesk."),
)


def disabled() -> list[str]:
    return [module for module, name, _, _ in MODULES if not getattr(settings, name)]
