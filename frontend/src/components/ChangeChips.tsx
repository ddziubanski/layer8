// SPDX-License-Identifier: AGPL-3.0-or-later
/** Status and risk chips for changes (shared by the Changes page and the ticket's Changes panel). */

export const CHANGE_STATUS: Record<string, [string, string]> = {
  draft: ["Draft", "n"],
  awaiting_approval: ["Awaiting approval", "a"],
  approved: ["Approved", "g"],
  rejected: ["Rejected", "r"],
  in_progress: ["In progress", "a"],
  completed: ["Completed", "g"],
  failed: ["Failed", "r"],
  rolled_back: ["Rolled back", "r"],
  cancelled: ["Cancelled", "n"],
};

export function ChangeStatusChip({ status }: { status: string }) {
  const [label, tone] = CHANGE_STATUS[status] ?? [status, "n"];
  return <span className={`chip ${tone}`}>{label}</span>;
}

export function RiskChip({ level, risk }: { level: string; risk: number }) {
  const tone = level === "high" ? "r" : level === "medium" ? "a" : "g";
  return (
    <span className={`chip ${tone}`} title={`Risk ${risk} (impact x likelihood)`}>
      {level[0].toUpperCase() + level.slice(1)} risk
    </span>
  );
}
