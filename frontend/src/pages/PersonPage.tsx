// SPDX-License-Identifier: AGPL-3.0-or-later
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api/client";
import { PriorityChip, StatusChip } from "../components/Chips";
import { PersonDetails } from "../components/PersonCard";
import { WorkspaceTitle } from "../components/WorkspaceTitle";
import { when } from "../format";

const BATCH = 50;

/** Everything the service desk knows about one person: contact details, devices, AD and their tickets. */
export function PersonPage() {
  const id = Number(useParams().personId);
  const person = useQuery({ queryKey: ["person", id], queryFn: () => api.person(id) });
  const [showOpen, setShowOpen] = useState(false);
  const tickets = useInfiniteQuery({
    queryKey: ["tickets", "person", id, showOpen],
    queryFn: ({ pageParam }) => api.tickets({ scope: "all", requester_id: id, ...(showOpen ? { view: "open" as const } : {}), sort: "id", dir: "desc", limit: BATCH, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total && last.items.length > 0 ? last.offset + last.items.length : undefined),
  });
  const [groupsWanted, setGroupsWanted] = useState(false);
  const groups = useQuery({ queryKey: ["person-groups", id], queryFn: () => api.ad.userGroups(id), enabled: groupsWanted, retry: false });

  if (person.error) return <p className="error">{person.error.message}</p>;
  const p = person.data;
  if (!p) return null;
  const rows = tickets.data?.pages.flatMap((page) => page.items) ?? [];
  const total = tickets.data?.pages[0]?.total ?? 0;

  return (
    <>
      <div className="page-head">
        <WorkspaceTitle title={p.display_name} area="PEOPLE" />
      </div>
      <div className="person-page">
        <section className="card stack" aria-label="Contact">
          <h2>Contact</h2>
          <PersonDetails p={p} />
          {p.last_synced_at && <p className="muted small">Directory sync last saw this account {when(p.last_synced_at)}.</p>}
        </section>

        <section className="card stack" aria-label="Devices">
          <h2>Devices</h2>
          {p.devices.length === 0 ? (
            <p className="muted">No devices assigned.</p>
          ) : (
            <ul className="plain-list">
              {p.devices.map((d) => (
                <li key={d.id}>
                  <Link to={`/devices/${d.id}`}>{d.name}</Link>
                  {d.os && <span className="muted small"> · {d.os}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>

        {p.in_directory && (
          <section className="card stack" aria-label="Directory">
            <h2>Directory (AD)</h2>
            {p.ou && <p className="small"><span className="muted">OU</span> {p.ou}</p>}
            {!groupsWanted ? (
              <button className="btn2" type="button" onClick={() => setGroupsWanted(true)}>Show AD groups</button>
            ) : groups.isPending ? (
              <p className="muted small">Asking AD…</p>
            ) : groups.error ? (
              <p className="error small">{groups.error.message}</p>
            ) : (
              <ul className="plain-list">
                {groups.data?.map((g) => <li key={g.dn}>{g.name}</li>)}
                {groups.data?.length === 0 && <li className="muted">No direct groups.</li>}
              </ul>
            )}
          </section>
        )}

        <section className="card stack person-tickets" aria-label="Tickets">
          <div className="row-gap">
            <h2 className="spacer">Tickets</h2>
            <div className="views" role="group" aria-label="Which tickets">
              <button type="button" aria-pressed={!showOpen} onClick={() => setShowOpen(false)}>All {p.all_tickets}</button>
              <button type="button" aria-pressed={showOpen} onClick={() => setShowOpen(true)}>Open {p.open_tickets}</button>
            </div>
          </div>
          {tickets.error && <p className="error">{tickets.error.message}</p>}
          {tickets.data && rows.length === 0 && <p className="muted">No tickets{showOpen ? " open" : ""} in the queues you can see.</p>}
          <ul className="plain-list person-ticket-list">
            {rows.map((t) => (
              <li key={t.id}>
                <span className="muted mono">#{t.id}</span> <Link to={`/tickets/${t.id}`}>{t.subject}</Link>
                <span className="person-ticket-meta">
                  <StatusChip status={t.status} /> <PriorityChip priority={t.priority} /> <span className="muted small">{when(t.created_at)}</span>
                </span>
              </li>
            ))}
          </ul>
          {total > 0 && (
            <div className="pager">
              <span className="muted" role="status">{rows.length < total ? `${rows.length} of ${total} shown` : `All ${total} shown`}</span>
              {tickets.hasNextPage && (
                <button className="btn2" type="button" disabled={tickets.isFetchingNextPage} onClick={() => void tickets.fetchNextPage()}>
                  {tickets.isFetchingNextPage ? "Loading…" : "Show more"}
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
