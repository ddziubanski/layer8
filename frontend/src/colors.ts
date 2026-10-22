import type { ColorRule, Ticket } from "./api/client";

/** The fields a personal colour rule can test, with the values to offer. */
export const RULE_FIELDS: { key: ColorRule["field"]; label: string; values?: [string, string][] }[] = [
  { key: "priority", label: "Priority is", values: [["critical", "Critical"], ["high", "High"], ["medium", "Medium"], ["low", "Low"]] },
  { key: "status", label: "Status is", values: [["new", "New"], ["open", "Open"], ["waiting_on_user", "Waiting on user"], ["resolved", "Resolved"], ["closed", "Closed"]] },
  { key: "sla", label: "SLA is", values: [["at_risk", "At risk"], ["breached", "Breached"]] },
  { key: "assignee", label: "Assignee is", values: [["me", "Me"], ["unassigned", "Nobody"]] },
  { key: "source", label: "Came in by", values: [["email", "Email"], ["portal", "Portal"], ["agent", "Agent"], ["api", "API"]] },
  { key: "queue", label: "Queue is" },
  { key: "requester", label: "Requester is" },
];

/** Colours that read well as a stripe and a light tint, in light and dark mode. */
export const PALETTE = ["#2563eb", "#0891b2", "#059669", "#65a30d", "#d97706", "#ea580c", "#dc2626", "#db2777", "#9333ea", "#4b5563"];

/** My colour for this ticket: the first of my rules it matches, or null. */
export function ruleColor(t: Ticket, rules: ColorRule[], meId: number): string | null {
  for (const r of rules) {
    const hit =
      (r.field === "priority" && t.priority === r.value) ||
      (r.field === "status" && t.status === r.value) ||
      (r.field === "source" && t.source === r.value) ||
      (r.field === "queue" && String(t.queue.id) === r.value) ||
      (r.field === "requester" && String(t.requester.id) === r.value) ||
      (r.field === "sla" && (t.sla_state === r.value || (r.value === "at_risk" && t.sla_state === "breached"))) ||
      (r.field === "assignee" && ((r.value === "me" && t.assignee?.id === meId) || (r.value === "unassigned" && !t.assignee)));
    if (hit) return r.color;
  }
  return null;
}
