// SPDX-License-Identifier: AGPL-3.0-or-later
/** Metadata only: the library never mounts a tool to discover what it does. */
const TICKET: Record<string, [string, string]> = {
  conversation: ["Service desk", "The request, replies and work notes. Always included."],
  assignment: ["Service desk", "Choose the queue and technician responsible for the ticket."],
  requester: ["Service desk", "Requester identity and contact information."],
  status: ["Service desk", "Ticket status, priority and SLA information."],
  actions: ["Service desk", "Existing escalation, linking and ticket actions, subject to your permissions."],
  attachments: ["Work", "Review and attach files using the existing ticket workflow."],
  children: ["Work", "Linked child tickets and progress."],
  related: ["Work", "Open tickets that may be the same problem, with why; link one as part of this ticket."],
  checklists: ["Work", "Start and follow the ticket's checklists."],
  expenses: ["Work", "Track ticket expenses and purchasing links."],
  time: ["Work", "Record time spent on this ticket."],
  changes: ["Work", "Review changes associated with the ticket."],
  history: ["Work", "Read the ticket's audit trail."],
  computer: ["Infrastructure", "The requester's assigned computer and its context."],
  devices: ["Infrastructure", "Devices linked to this ticket."],
  systems: ["Infrastructure", "Services and systems linked to the ticket."],
  vms: ["Infrastructure", "Nutanix virtual machines, when enabled and available."],
  network: ["Infrastructure", "Network context from linked devices and the requester's computer."],
  guest_wifi: ["Infrastructure", "Guest Wi-Fi tools when enabled and permitted."],
  ad_account: ["Identity", "Directory account information and permitted actions when configured."],
  scripts: ["Automation", "Existing script tools, previews and approvals."],
  copilot: ["Knowledge", "Copilot assistance when enabled and connected."],
  knowledge: ["Knowledge", "Suggested articles and linked knowledge."],
};
const PO: Record<string, [string, string]> = {
  order: ["Purchasing", "The order form and lines. Always included; fields remain configurable."],
  receive: ["Purchasing", "Receiving controls when the order's status permits them."],
  heads_up: ["Purchasing", "Send the existing receiving heads-up when ready."],
  files: ["Purchasing", "Files attached to this purchase order."],
  history: ["Purchasing", "Purchase order history."],
};
export function widgetInfo(page: string, id: string) {
  const [category, description] = (page === "ticket" ? TICKET : PO)[id] ?? ["Other", "An existing page widget. Its normal availability and permissions still apply."];
  return { category, description };
}
