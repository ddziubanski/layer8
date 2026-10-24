// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from "react";

import type { Priority, Ticket, TicketSource, TicketStatus } from "../api/client";
import { PRIORITY_LABEL, PRIORITY_TONE, slaInfo, SOURCE_LABEL, STATUS_LABEL, STATUS_TONE } from "../format";

/** Current time, refreshed every 30 seconds so countdowns move. */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function PriorityChip({ priority }: { priority: Priority }) {
  return <span className={`chip ${PRIORITY_TONE[priority]}`}>{PRIORITY_LABEL[priority]}</span>;
}

export function StatusChip({ status }: { status: TicketStatus }) {
  return <span className={`chip ${STATUS_TONE[status]}`}>{STATUS_LABEL[status]}</span>;
}

export function SourceChip({ source }: { source: TicketSource }) {
  return <span className="chip n">{SOURCE_LABEL[source]}</span>;
}

export function SlaChip({ ticket, long = false }: { ticket: Pick<Ticket, "sla_state" | "resolution_due_at">; long?: boolean }) {
  const now = useNow();
  const info = slaInfo(ticket, now);
  return (
    <span className={`chip ${info.tone}`} title={info.long}>
      {long ? info.long : info.short}
    </span>
  );
}
