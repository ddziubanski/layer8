// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, type Group, type User } from "../../api/client";

/** Members of a local group as checkboxes, searchable. AD groups are read-only (they sync). */
function GroupEditor({ group, users, canEdit }: { group: Group; users: User[]; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const local = group.source === "local";
  const [members, setMembers] = useState(() => new Set(group.members.map((m) => m.id)));
  const [q, setQ] = useState("");
  useEffect(() => setMembers(new Set(group.members.map((m) => m.id))), [group]);
  const done = () => queryClient.invalidateQueries({ queryKey: ["groups"] });
  const save = useMutation({ mutationFn: () => api.setGroupMembers(group.id, [...members]), onSettled: done });
  const toggleActive = useMutation({ mutationFn: () => api.updateGroup(group.id, { is_active: !group.is_active }), onSettled: done });

  const shown = useMemo(() => {
    const v = q.trim().toLowerCase();
    const pool = local ? users.filter((u) => u.is_active) : users.filter((u) => members.has(u.id));
    return pool.filter((u) => !v || `${u.display_name} ${u.email}`.toLowerCase().includes(v));
  }, [users, q, local, members]);

  return (
    <section className="stack">
      <div className="row-gap">
        <h2 className="spacer">{group.name}</h2>
        <span className={`chip ${local ? "n" : "g"}`}>{local ? "Made here" : "From AD"}</span>
        {!group.is_active && <span className="chip r">Inactive</span>}
      </div>
      <p className="muted small" style={{ margin: 0 }}>
        {local
          ? "Members are picked here. Use groups like this for queue owners, managers and viewers."
          : "Members come from Active Directory and update with each sync."}{" "}
        {members.size} member{members.size === 1 ? "" : "s"}.
      </p>
      <input aria-label="Filter people" placeholder="Filter by name or email" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 360 }} />
      <div className="checks" style={{ maxHeight: 360 }}>
        {shown.map((u) => (
          <label key={u.id}>
            <input
              type="checkbox"
              disabled={!local || !canEdit}
              checked={members.has(u.id)}
              onChange={() => {
                const next = new Set(members);
                if (next.has(u.id)) next.delete(u.id);
                else next.add(u.id);
                setMembers(next);
              }}
            />
            {u.display_name} <span className="muted small">{u.role !== "requester" ? u.role : ""}</span>
          </label>
        ))}
        {shown.length === 0 && <span className="muted small">No one matches.</span>}
      </div>
      {(save.error || toggleActive.error) && <p className="error">{(save.error ?? toggleActive.error)!.message}</p>}
      {canEdit && (
        <div className="row-gap">
          {local && (
            <button className="btn" type="button" disabled={save.isPending} onClick={() => save.mutate()}>
              Save members
            </button>
          )}
          <button className="btn2" type="button" disabled={toggleActive.isPending} onClick={() => toggleActive.mutate()}>
            {group.is_active ? "Deactivate group" : "Reactivate group"}
          </button>
        </div>
      )}
    </section>
  );
}

export function GroupsAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const groups = useQuery({ queryKey: ["groups"], queryFn: api.groups });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [selected, setSelected] = useState<number | null>(null);
  const [name, setName] = useState("");
  const isAdmin = me.role === "admin";
  const create = useMutation({
    mutationFn: () => api.createGroup(name.trim(), null),
    onSuccess: (g) => {
      setName("");
      setSelected(g.id);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["groups"] }),
  });

  const list = groups.data ?? [];
  const current = list.find((g) => g.id === selected) ?? list[0];
  return (
    <>
      <div className="page-head">
        <h1>Groups</h1>
      </div>
      {groups.error && <p className="error">{groups.error.message}</p>}
      <div className="admin">
        <div className="stack">
          <ul className="admin-list">
            {list.map((g) => (
              <li key={g.id}>
                <button type="button" aria-current={g.id === current?.id} onClick={() => setSelected(g.id)}>
                  {g.name}
                  <span className="muted small">
                    {" "}
                    · {g.source === "local" ? "here" : "AD"} · {g.members.length}
                    {!g.is_active ? " · off" : ""}
                  </span>
                </button>
              </li>
            ))}
            {list.length === 0 && <li className="muted small">No groups yet.</li>}
          </ul>
          {isAdmin && (
            <form
              className="stack"
              style={{ gap: 8 }}
              onSubmit={(e) => {
                e.preventDefault();
                create.mutate();
              }}
            >
              <label className="field">
                New group
                <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. HR team" />
              </label>
              {create.error && <p className="error small">{create.error.message}</p>}
              <button className="btn2" type="submit" disabled={create.isPending}>
                Add group
              </button>
            </form>
          )}
        </div>
        {current && <GroupEditor key={current.id} group={current} users={users.data ?? []} canEdit={isAdmin} />}
      </div>
    </>
  );
}
