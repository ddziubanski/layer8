// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api } from "../api/client";

/** Top bar: tickets nobody has taken past Settings > Desk hours > Not taken for (hidden when off or none). */
export function UnacceptedCounter() {
  const q = useQuery({ queryKey: ["unaccepted"], queryFn: api.unaccepted, refetchInterval: 60_000 });
  if (!q.data?.enabled || q.data.count === 0) return null;
  const n = q.data.count;
  return (
    <Link to="/?view=unassigned" className="chip r unaccepted-counter" title={`Nobody has taken ${n === 1 ? "this ticket" : "these tickets"} for over ${q.data.minutes} min`}>
      {n} not taken
    </Link>
  );
}
