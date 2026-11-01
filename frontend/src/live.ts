// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

type LiveEvent = { t: "ticket" | "device" | "outage"; id: number };

/** What to refetch for a batch of change notices (ids only: data comes through the normal API). */
export function refreshFor(client: QueryClient, events: LiveEvent[]) {
  if (!events.length) return;
  const tickets = new Set<number>(), devices = new Set<number>();
  let outage = false;
  for (const e of events) {
    if (e.t === "ticket") tickets.add(e.id);
    else if (e.t === "device") devices.add(e.id);
    else outage = true;
  }
  if (tickets.size || outage) {
    void client.invalidateQueries({ queryKey: ["tickets"] });
    void client.invalidateQueries({ queryKey: ["views"] });
  }
  for (const id of tickets) void client.invalidateQueries({ queryKey: ["ticket", id] });
  for (const id of devices) void client.invalidateQueries({ queryKey: ["device", id] });
  if (outage) void client.invalidateQueries({ queryKey: ["problems"] });
}

/** Listen for changes as they're committed (GET /api/live). Without it, pages keep polling as before. */
export function useLiveUpdates() {
  const client = useQueryClient();
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const source = new EventSource("/api/live");
    let pending: LiveEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | undefined;
    source.onmessage = (m) => {
      try {
        pending.push(JSON.parse(m.data) as LiveEvent);
      } catch {
        return;
      }
      // A burst (a bulk change, a sync) becomes one round of refetches.
      timer ??= setTimeout(() => { const batch = pending; pending = []; timer = undefined; refreshFor(client, batch); }, 400);
    };
    source.addEventListener("signed-out", () => source.close());
    return () => { source.close(); if (timer) clearTimeout(timer); };
  }, [client]);
}
