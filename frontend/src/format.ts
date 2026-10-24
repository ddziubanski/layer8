// SPDX-License-Identifier: AGPL-3.0-or-later
import type { Priority, SavedView, Ticket, TicketSource, TicketStatus } from "./api/client";

export type Tone = "g" | "a" | "r" | "n";

export const PRIORITY_LABEL: Record<Priority, string> = { low: "Low", medium: "Medium", high: "High", critical: "Critical" };
export const PRIORITY_TONE: Record<Priority, Tone> = { low: "n", medium: "a", high: "r", critical: "r" };

export const STATUS_LABEL: Record<TicketStatus, string> = {
  new: "New",
  open: "Open",
  waiting_on_user: "Waiting on user",
  resolved: "Resolved",
  closed: "Closed",
};
export const STATUS_TONE: Record<TicketStatus, Tone> = {
  new: "a",
  open: "g",
  waiting_on_user: "a",
  resolved: "n",
  closed: "n",
};

export const SOURCE_LABEL: Record<TicketSource, string> = { email: "Email", portal: "Portal", agent: "Agent", api: "API" };

export const VIEWS: { key: SavedView; label: string }[] = [
  { key: "open", label: "All open" },
  { key: "unassigned", label: "Unassigned" },
  { key: "sla_at_risk", label: "SLA at risk" },
  { key: "waiting_on_user", label: "Waiting on user" },
  { key: "from_email", label: "From email" },
  { key: "from_portal", label: "From portal" },
];

/** "0h 42m", or "2d 4h" past a day. */
export function duration(ms: number): string {
  const minutes = Math.max(0, Math.floor(ms / 60000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return `${days}d ${hours}h`;
  return `${hours}h ${String(mins).padStart(2, "0")}m`;
}

export type SlaInfo = { tone: Tone; short: string; long: string };

export function slaInfo(t: Pick<Ticket, "sla_state" | "resolution_due_at">, now: number): SlaInfo {
  const due = t.resolution_due_at ? Date.parse(t.resolution_due_at) : null;
  switch (t.sla_state) {
    case "none":
      return { tone: "n", short: "None", long: "No SLA" };
    case "met":
      return { tone: "g", short: "Met", long: "SLA met" };
    case "breached":
      if (due !== null && now > due) {
        const ago = duration(now - due);
        return { tone: "r", short: `-${ago}`, long: `SLA breached ${ago} ago` };
      }
      return { tone: "r", short: "Breached", long: "SLA breached" };
    case "at_risk":
    case "ok": {
      const left = due !== null ? duration(due - now) : "";
      return { tone: t.sla_state === "at_risk" ? "a" : "g", short: left, long: `SLA breach in ${left}` };
    }
  }
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

/** Time only for today, date and time otherwise. */
export function when(iso: string, now: number = Date.now()): string {
  const d = new Date(iso);
  return new Date(now).toDateString() === d.toDateString() ? timeFmt.format(d) : dateTimeFmt.format(d);
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
