// SPDX-License-Identifier: AGPL-3.0-or-later
import type { TicketEvent } from "../../api/client";
import { PRIORITY_LABEL, SOURCE_LABEL, STATUS_LABEL } from "../../format";

export type Names = { queue: (id: number | null | undefined) => string; user: (id: number | null | undefined) => string };

type Data = Record<string, unknown>;

const label = <T extends string>(map: Record<T, string>, v: unknown) => map[v as T] ?? String(v);

function assignment(d: Data, names: Names): string {
  const before = (d.before ?? {}) as Data;
  const after = (d.after ?? {}) as Data;
  const parts: string[] = [];
  if (before.queue_id !== after.queue_id) {
    parts.push(`moved from ${names.queue(before.queue_id as number)} to ${names.queue(after.queue_id as number)}`);
  }
  if (before.assignee_id !== after.assignee_id) {
    parts.push(after.assignee_id ? `assigned to ${names.user(after.assignee_id as number)}` : "unassigned");
  }
  const text = parts.join(", ") || "assignment unchanged";
  return text[0].toUpperCase() + text.slice(1);
}

/** One line of plain English per ticket event. */
export function describe(e: TicketEvent, names: Names): string {
  const d = e.data as Data;
  switch (e.type) {
    case "created":
      return `Created via ${label(SOURCE_LABEL, d.source).toLowerCase()}`;
    case "routed":
      return d.rule_name
        ? `Routed to ${d.queue_name} by rule "${d.rule_name}"`
        : `Routed to ${d.queue_name} (no rule matched)`;
    case "assigned":
      return assignment(d, names);
    case "escalated": {
      const moved = d.from_queue_id !== d.to_queue_id ? ` to ${names.queue(d.to_queue_id as number)}` : "";
      return `Escalated${moved}, priority ${label(PRIORITY_LABEL, d.to_priority)}: ${d.reason}`;
    }
    case "status_changed":
      return `Status ${label(STATUS_LABEL, d.before)} → ${label(STATUS_LABEL, d.after)}`;
    case "priority_changed":
      return `Priority ${label(PRIORITY_LABEL, d.before)} → ${label(PRIORITY_LABEL, d.after)}`;
    case "updated":
      if (d.teams_delivery_id && d.teams_status === "started") return `Started a Teams message to ${d.recipient} as ${d.sender}`;
      if (d.teams_delivery_id) return d.teams_status === "sent" ? `Sent a Teams message to ${d.recipient} as ${d.sender}` : `Teams delivery ${d.teams_status === "pending" ? "pending confirmation" : "not confirmed; check Teams"}`;
      if (d.copilot === "asked") return d.shared_internal ? "Asked Copilot, including internal notes" : d.shared_ticket ? "Asked Copilot, with this ticket's text" : "Asked Copilot";
      return `Edited ${Object.keys((d.fields ?? {}) as Data).join(", ")}`;
    case "comment_added":
      return d.is_internal ? "Added an internal note" : "Replied";
    case "attachment_added":
      return `Attached ${d.filename}${d.is_internal ? " (internal)" : ""}`;
    case "requester_changed":
      return `Requester changed from ${names.user(d.before as number)} to ${names.user(d.after as number)}`;
    case "device_linked":
      return `Linked device ${d.device}`;
    case "device_unlinked":
      return `Unlinked device ${d.device}`;
    case "script_run":
      return `Ran script "${d.script}" (version ${d.version}) on ${d.targets}`;
    case "system_linked":
      return `Linked system ${d.system}`;
    case "system_unlinked":
      return `Unlinked system ${d.system}`;
    case "merged":
      return d.direction === "in" ? `Ticket #${d.other} merged into this one (${d.reason})` : `Merged into ticket #${d.other} (${d.reason})`;
    case "parent_changed":
      return d.after ? `Made part of ticket #${d.after}` + (d.before ? ` (was #${d.before})` : "") : `No longer part of ticket #${d.before}`;
    case "child_changed":
      return d.action === "added" ? `Child ticket #${d.child} added: ${d.subject}` : `Child ticket #${d.child} moved elsewhere`;
    case "kb_linked":
      return d.drafted ? `Drafted knowledge base article "${d.title}" from this ticket` : `Used knowledge base article "${d.title}"`;
    case "survey_sent":
      return d.emailed ? "Satisfaction survey emailed to the requester" : "Satisfaction survey offered in the portal (not emailed)";
    case "survey_answered":
      return `Survey ${d.changed ? "answer changed" : "answered"}: ${d.rating}/5 (${d.label})` + (d.comment ? `: "${d.comment}"` : "");
    case "remote_session":
      if (d.tool === "action1") {
        const actions: Record<string, string> = { requested: "Requested Action1 remote assistance", launch_link_returned: "Opened the Action1 remote assistance link", failed: "Action1 remote request failed", unknown: "Action1 remote request outcome unknown; check Action1", tracking_closed: "Closed Layer 8 Action1 tracking (connection ended separately in Action1)" };
        return actions[String(d.action)] ?? "Action1 remote assistance updated";
      }
      return d.action === "ended" ? `Remote Assistance with ${d.target} ended after ${d.duration}` : `Offered Remote Assistance to ${d.target}`;
  }
}
