// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type User, type Wallboard, type WallboardIn, type WallboardMade } from "../../api/client";
import { when } from "../../format";

const link = (token: string) => `${window.location.origin}/wallboard#${token}`;

/** Settings > Wallboards: read-only boards for a big screen, opened by a secret link. */
export function WallboardsAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const boards = useQuery({ queryKey: ["wallboards"], queryFn: api.wallboards.list, enabled: me.role === "admin" });
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues, enabled: me.role === "admin" });
  const [editing, setEditing] = useState<Wallboard | "new" | null>(null);
  const [made, setMade] = useState<WallboardMade | null>(null);
  const [confirm, setConfirm] = useState<{ id: number; what: "revoke" | "link" } | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["wallboards"] });
  const newLink = useMutation({ mutationFn: api.wallboards.newLink, onSuccess: (b) => (setMade(b), setConfirm(null)), onSettled: refresh });
  const revoke = useMutation({ mutationFn: api.wallboards.revoke, onSuccess: () => setConfirm(null), onSettled: refresh });
  if (me.role !== "admin") return <p>Wallboards are managed by admins.</p>;
  const queueName = (id: number) => queues.data?.find((q) => q.id === id)?.name ?? `#${id}`;

  return (
    <div className="stack">
      <div className="row-gap">
        <div className="spacer">
          <h1 style={{ margin: 0 }}>Wallboards</h1>
          <p className="muted" style={{ margin: "4px 0 0", maxWidth: "75ch" }}>
            A live board for a big screen: counts, alerts (SLA breaches, unassigned tickets, a stopped worker) and the tickets
            that need someone, with requester and technician names. It opens from a secret link with no sign-in, so put it only
            on screens where everyone who can see them may see those tickets. It refreshes every 30 seconds.
          </p>
        </div>
        {editing === null && (
          <button className="btn" type="button" onClick={() => (setEditing("new"), setMade(null))}>
            New wallboard
          </button>
        )}
      </div>

      {made && (
        <section className="card note stack" style={{ margin: 0 }} aria-label="Wallboard link">
          <strong>Link for {made.name}: copy it now, it isn&apos;t shown again.</strong>
          <pre className="log-details code" style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {link(made.token)}
          </pre>
          <span className="small">Open it in the screen&apos;s browser (full screen: F11). Anyone with the link can see the board until you revoke it or make a new link.</span>
          <div className="row-gap">
            <button className="btn2" type="button" onClick={() => navigator.clipboard?.writeText(link(made.token)).catch(() => undefined)}>
              Copy link
            </button>
            <a className="btn2" href={link(made.token)} target="_blank" rel="noreferrer">
              Open
            </a>
            <button className="linkish" type="button" onClick={() => setMade(null)}>
              Done
            </button>
          </div>
        </section>
      )}

      {editing !== null && (
        <BoardForm
          board={editing === "new" ? null : editing}
          queues={queues.data ?? []}
          onDone={(m) => {
            setEditing(null);
            if (m) setMade(m);
            refresh();
          }}
        />
      )}

      <table className="grid">
        <thead>
          <tr>
            <th>Name</th>
            <th>Queues</th>
            <th>Link</th>
            <th>Last shown</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {boards.data?.map((b) => (
            <tr key={b.id} className={b.is_active ? undefined : "muted"}>
              <td>{b.name}</td>
              <td className="small">
                {b.queue_ids.length ? b.queue_ids.map(queueName).join(", ") : "All queues that aren't restricted"}
                <div className="muted">Alert when unassigned over {b.unassigned_alert_minutes} min</div>
              </td>
              <td className="mono small">{b.is_active ? `${b.prefix}…` : "Revoked"}</td>
              <td className="small">{b.last_seen_at ? when(b.last_seen_at) : "never"}</td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                {confirm?.id === b.id ? (
                  <>
                    <span className="small">The current link stops working at once. </span>
                    <button
                      className="btn2"
                      type="button"
                      disabled={revoke.isPending || newLink.isPending}
                      onClick={() => (confirm.what === "revoke" ? revoke.mutate(b.id) : newLink.mutate(b.id))}
                    >
                      {confirm.what === "revoke" ? "Revoke" : "Make a new link"}
                    </button>{" "}
                    <button className="linkish small" type="button" onClick={() => setConfirm(null)}>
                      Keep
                    </button>
                  </>
                ) : (
                  <>
                    <button className="linkish small" type="button" onClick={() => (setEditing(b), setMade(null))}>
                      Edit
                    </button>{" "}
                    <button className="linkish small" type="button" onClick={() => setConfirm({ id: b.id, what: "link" })}>
                      {b.is_active ? "New link…" : "Turn back on…"}
                    </button>{" "}
                    {b.is_active && (
                      <button className="linkish small" type="button" onClick={() => setConfirm({ id: b.id, what: "revoke" })}>
                        Revoke…
                      </button>
                    )}
                  </>
                )}
              </td>
            </tr>
          ))}
          {boards.data?.length === 0 && (
            <tr>
              <td colSpan={5} className="muted small">
                No wallboards yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {(revoke.error || newLink.error) && <p className="error">{(revoke.error ?? newLink.error)!.message}</p>}
    </div>
  );
}

function BoardForm({
  board,
  queues,
  onDone,
}: {
  board: Wallboard | null;
  queues: { id: number; name: string; restricted: boolean; is_active: boolean }[];
  onDone: (made?: WallboardMade) => void;
}) {
  const [b, setB] = useState<WallboardIn>(
    board ? { name: board.name, queue_ids: board.queue_ids, unassigned_alert_minutes: board.unassigned_alert_minutes } : { name: "", queue_ids: [], unassigned_alert_minutes: 30 },
  );
  const save = useMutation({
    mutationFn: () => (board ? api.wallboards.update(board.id, b) : api.wallboards.create(b)),
    onSuccess: (r) => onDone(board ? undefined : (r as WallboardMade)),
  });
  const ids = b.queue_ids ?? [];
  const toggle = (id: number) => setB({ ...b, queue_ids: ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id] });
  return (
    <form
      className="card stack settings-form"
      style={{ margin: 0 }}
      aria-label={board ? `Edit ${board.name}` : "New wallboard"}
      onSubmit={(e) => {
        e.preventDefault();
        if (b.name.trim()) save.mutate();
      }}
    >
      <label className="field">
        Name
        <input value={b.name} maxLength={100} placeholder="e.g. Service desk TV" onChange={(e) => setB({ ...b, name: e.target.value })} />
      </label>
      <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend>Queues</legend>
        <span className="muted small">None ticked: every active queue that isn&apos;t restricted. A restricted queue shows only when ticked here.</span>
        <div className="row-gap" style={{ flexWrap: "wrap" }}>
          {queues
            .filter((q) => q.is_active)
            .map((q) => (
              <label key={q.id} className="inline">
                <input type="checkbox" checked={ids.includes(q.id)} onChange={() => toggle(q.id)} /> {q.name}
                {q.restricted && <span className="muted small"> (restricted)</span>}
              </label>
            ))}
        </div>
      </fieldset>
      <label className="field" style={{ maxWidth: 320 }}>
        Alert when a ticket is unassigned for (minutes)
        <input
          type="number"
          min={1}
          max={1440}
          value={b.unassigned_alert_minutes ?? 30}
          onChange={(e) => setB({ ...b, unassigned_alert_minutes: Number(e.target.value) })}
        />
      </label>
      {save.error && <p className="error">{save.error.message}</p>}
      <div className="row-gap">
        <button className="btn" type="submit" disabled={!b.name.trim() || save.isPending}>
          {board ? "Save" : "Make the wallboard"}
        </button>
        <button className="btn2" type="button" onClick={() => onDone()}>
          Cancel
        </button>
      </div>
    </form>
  );
}
