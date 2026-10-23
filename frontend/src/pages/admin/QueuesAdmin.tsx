import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { api, type Group, type Queue, type User } from "../../api/client";
import { can } from "../../permissions";

type PeoplePick = { users: Set<number>; groups: Set<number> };

function pick(users: { id: number }[], groups: { id: number }[]): PeoplePick {
  return { users: new Set(users.map((u) => u.id)), groups: new Set(groups.map((g) => g.id)) };
}

function toggle(set: Set<number>, id: number): Set<number> {
  const next = new Set(set);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** Users and groups as checkboxes; for owners or managers. */
function PeoplePicker({
  legend,
  agents,
  groups,
  value,
  onChange,
  disabled,
}: {
  legend: string;
  agents: User[];
  groups: Group[];
  value: PeoplePick;
  onChange: (v: PeoplePick) => void;
  disabled: boolean;
}) {
  return (
    <fieldset disabled={disabled} style={{ border: 0, padding: 0, margin: 0 }}>
      <legend className="lbl">{legend}</legend>
      <div className="checks">
        {groups.map((g) => (
          <label key={`g${g.id}`}>
            <input type="checkbox" checked={value.groups.has(g.id)} onChange={() => onChange({ ...value, groups: toggle(value.groups, g.id) })} />
            {g.name} <span className="muted small">({g.source === "directory" ? "AD group" : "group"})</span>
          </label>
        ))}
        {agents.map((u) => (
          <label key={`u${u.id}`}>
            <input type="checkbox" checked={value.users.has(u.id)} onChange={() => onChange({ ...value, users: toggle(value.users, u.id) })} />
            {u.display_name}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function QueueEditor({ queue, queues, me, agents, groups }: { queue: Queue; queues: Queue[]; me: User; agents: User[]; groups: Group[] }) {
  const queryClient = useQueryClient();
  // Admins, or those given Queues under Settings > Permissions.
  const isAdmin = can(me, "queues_manage");
  const isManager =
    queue.manager_users.some((u) => u.id === me.id) ||
    queue.manager_groups.some((g) => groups.find((x) => x.id === g.id)?.members.some((m) => m.id === me.id));
  const canEdit = isAdmin || isManager;

  const [form, setForm] = useState(() => formFrom(queue));
  const [options, setOptions] = useState(() => optionsFrom(queue));
  const [owners, setOwners] = useState(() => pick(queue.owner_users, queue.owner_groups));
  const [managers, setManagers] = useState(() => pick(queue.manager_users, queue.manager_groups));
  const [viewers, setViewers] = useState(() => pick(queue.viewer_users, queue.viewer_groups));
  const [restricted, setRestricted] = useState(queue.restricted);
  useEffect(() => {
    setForm(formFrom(queue));
    setOptions(optionsFrom(queue));
    setOwners(pick(queue.owner_users, queue.owner_groups));
    setManagers(pick(queue.manager_users, queue.manager_groups));
    setViewers(pick(queue.viewer_users, queue.viewer_groups));
    setRestricted(queue.restricted);
  }, [queue]);

  const done = () => queryClient.invalidateQueries({ queryKey: ["queues"] });
  const save = useMutation({
    mutationFn: async () => {
      const kept = options.filter((o) => o.label.trim());
      const categories = kept.map((o) => o.label.trim());
      const category_routes = Object.fromEntries(kept.filter((o) => o.route).map((o) => [o.label.trim(), Number(o.route)]));
      await api.updateQueue(queue.id, {
        name: form.name,
        description: form.description || null,
        escalation_queue_id: form.escalation_queue_id ? Number(form.escalation_queue_id) : null,
        show_in_portal: form.show_in_portal,
        portal_name: form.portal_name || null,
        portal_description: form.portal_description || null,
        portal_position: Number(form.portal_position) || 100,
        hours: form.hours.trim() || null,
        categories,
        category_routes,
        ...(isAdmin ? { is_active: form.is_active, is_default: form.is_default || undefined, restricted } : {}),
      });
      await api.setOwners(queue.id, [...owners.users], [...owners.groups]);
      if (isAdmin) await api.setManagers(queue.id, [...managers.users], [...managers.groups]);
      await api.setViewers(queue.id, [...viewers.users], [...viewers.groups]);
    },
    onSettled: done,
  });

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const check = (k: "show_in_portal" | "is_active" | "is_default") => (e: { target: { checked: boolean } }) =>
    setForm({ ...form, [k]: e.target.checked });

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <form className="stack" onSubmit={submit}>
      <div className="row-gap">
        <h2 className="spacer">{queue.name}</h2>
        {queue.is_default && <span className="chip g">Default queue</span>}
        {queue.restricted && <span className="chip a">Restricted</span>}
        {!queue.is_active && <span className="chip n">Inactive</span>}
      </div>
      {!canEdit && <p className="muted">Only admins and this queue's managers can change it.</p>}
      <fieldset disabled={!canEdit} className="stack card" style={{ margin: 0 }}>
        <div className="lbl">Settings</div>
        <label className="field">
          Name
          <input required maxLength={100} value={form.name} onChange={set("name")} />
        </label>
        <label className="field">
          Description <span className="muted small">(for agents)</span>
          <textarea rows={2} value={form.description} onChange={set("description")} />
        </label>
        <label className="field">
          Escalates to
          <select value={form.escalation_queue_id} onChange={set("escalation_queue_id")}>
            <option value="">Nowhere (escalation only raises priority)</option>
            {queues
              .filter((q) => q.id !== queue.id)
              .map((q) => (
                <option key={q.id} value={q.id}>
                  {q.name}
                </option>
              ))}
          </select>
        </label>
        {isAdmin && (
          <div className="row-gap">
            <label className="inline">
              <input type="checkbox" checked={form.is_active} onChange={check("is_active")} /> Active
            </label>
            <label className="inline">
              <input type="checkbox" checked={form.is_default} disabled={queue.is_default} onChange={check("is_default")} /> Default queue
              for unrouted tickets
            </label>
          </div>
        )}
      </fieldset>

      <fieldset disabled={!canEdit} className="stack card" style={{ margin: 0 }}>
        <div className="lbl">Self-service portal</div>
        <label className="inline">
          <input type="checkbox" checked={form.show_in_portal} onChange={check("show_in_portal")} /> Requesters can choose this queue
        </label>
        <div className="row-gap">
          <label className="field spacer">
            Name in the portal <span className="muted small">(blank: same as above)</span>
            <input maxLength={100} value={form.portal_name} onChange={set("portal_name")} />
          </label>
          <label className="field" style={{ width: 120 }}>
            Order
            <input type="number" value={form.portal_position} onChange={set("portal_position")} />
          </label>
        </div>
        <label className="field">
          Description for requesters
          <input value={form.portal_description} onChange={set("portal_description")} placeholder="e.g. Computers, phones, accounts, Wi-Fi" />
        </label>
        <label className="field">
          Opening hours <span className="muted small">(blank: the desk hours in Settings &gt; Desk hours)</span>
          <textarea rows={2} value={form.hours} onChange={set("hours")} placeholder={"Mon-Fri 08:00-17:00\nSat 09:00-12:00"} />
          <span className="muted small">The portal shows whether this queue is open, and its after-hours message when it isn&apos;t.</span>
        </label>
        <div>
          <div className="lbl">Problem options ("What is the issue with?")</div>
          <p className="muted small" style={{ margin: "0 0 8px" }}>
            Requesters pick one, or "Something else" with their own words. Optionally send an option to another queue, e.g. Wi-Fi to Network. No options:
            the question is skipped.
          </p>
          <div className="stack" style={{ gap: 6 }}>
            {options.map((o, i) => (
              <div key={i} className="option-row">
                <input aria-label={`Option ${i + 1}`} maxLength={100} value={o.label} onChange={(e) => setOptions(options.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
                <select aria-label={`Send option ${i + 1} to`} value={o.route} onChange={(e) => setOptions(options.map((x, j) => (j === i ? { ...x, route: e.target.value } : x)))}>
                  <option value="">Stays in this queue</option>
                  {queues
                    .filter((q) => q.id !== queue.id && q.is_active)
                    .map((q) => (
                      <option key={q.id} value={q.id}>
                        Send to {q.name}
                      </option>
                    ))}
                </select>
                <button type="button" className="linkish small" aria-label={`Remove option ${i + 1}`} onClick={() => setOptions(options.filter((_, j) => j !== i))}>
                  Remove
                </button>
              </div>
            ))}
            <div>
              <button type="button" className="btn2" onClick={() => setOptions([...options, { label: "", route: "" }])}>
                Add option
              </button>
            </div>
          </div>
        </div>
      </fieldset>

      <fieldset className="card stack" style={{ margin: 0 }} disabled={!canEdit}>
        <div className="lbl">Who can see these tickets</div>
        <div role="radiogroup" aria-label="Who can see these tickets" className="stack" style={{ gap: 6 }}>
          <label className="inline">
            <input type="radio" name="visibility" checked={!restricted} disabled={!isAdmin} onChange={() => setRestricted(false)} /> All agents
          </label>
          <label className="inline">
            <input type="radio" name="visibility" checked={restricted} disabled={!isAdmin} onChange={() => setRestricted(true)} /> Restricted: only this
            queue's owners, managers, the viewers below, and admins
          </label>
          {!isAdmin && <span className="muted small">Only admins can change this (or see Settings &gt; Permissions: Queues).</span>}
        </div>
        {restricted && (
          <>
            <PeoplePicker legend="Viewers: can see and work on tickets here, without being owners" agents={agents} groups={groups} value={viewers} onChange={setViewers} disabled={!canEdit} />
            <p className="muted small" style={{ margin: 0 }}>
              Everyone else won't see this queue's tickets anywhere: lists, counts, search, files or history. They can still hand a ticket over to it.
            </p>
          </>
        )}
      </fieldset>

      <div className="card stack" style={{ margin: 0 }}>
        <PeoplePicker
          legend="Owners: who can be assigned this queue's tickets (none ticked: any agent)"
          agents={agents}
          groups={groups}
          value={owners}
          onChange={setOwners}
          disabled={!canEdit}
        />
        <PeoplePicker
          legend="Managers: escalation notices, can edit this queue (admins only)"
          agents={agents}
          groups={groups}
          value={managers}
          onChange={setManagers}
          disabled={!isAdmin}
        />
      </div>

      {save.error && (
        <p className="error" role="alert">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && !save.isPending && <p className="muted" role="status">Saved.</p>}
      {canEdit && (
        <div>
          <button className="btn" type="submit" disabled={save.isPending}>
            Save changes
          </button>
        </div>
      )}
    </form>
  );
}

function formFrom(q: Queue) {
  return {
    name: q.name,
    description: q.description ?? "",
    escalation_queue_id: q.escalation_queue_id ? String(q.escalation_queue_id) : "",
    is_active: q.is_active,
    is_default: q.is_default,
    show_in_portal: q.show_in_portal,
    portal_name: q.portal_name ?? "",
    portal_description: q.portal_description ?? "",
    portal_position: String(q.portal_position),
    hours: q.hours ?? "",
  };
}

function optionsFrom(q: Queue) {
  return q.categories.map((label) => ({ label, route: q.category_routes[label] ? String(q.category_routes[label]) : "" }));
}

export function QueuesAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const groups = useQuery({ queryKey: ["groups"], queryFn: api.groups });
  const [selected, setSelected] = useState<number | null>(null);
  const [newName, setNewName] = useState("");
  const create = useMutation({
    mutationFn: () => api.createQueue({ name: newName, is_default: false, restricted: false, email_addresses: [], portal_position: 100, show_in_portal: false, categories: [], category_routes: {} }),
    onSuccess: (q) => {
      setNewName("");
      setSelected(q.id);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["queues"] }),
  });

  const list = queues.data ?? [];
  const current = list.find((q) => q.id === selected) ?? list[0];
  const agents = (users.data ?? []).filter((u) => u.is_active && u.role !== "requester");

  return (
    <>
      <div className="page-head">
        <h1>Queues</h1>
      </div>
      {queues.error && <p className="error">{queues.error.message}</p>}
      <div className="admin">
        <div className="stack">
          <ul className="admin-list">
            {list.map((q) => (
              <li key={q.id}>
                <button type="button" aria-current={q.id === current?.id} onClick={() => setSelected(q.id)}>
                  {q.name}
                  {q.show_in_portal && <span className="muted small"> · portal</span>}
                  {q.restricted && <span className="muted small"> · restricted</span>}
                  {!q.is_active && <span className="muted small"> · inactive</span>}
                </button>
              </li>
            ))}
          </ul>
          {can(me, "queues_manage") && (
            <form
              className="stack"
              style={{ gap: 8 }}
              onSubmit={(e) => {
                e.preventDefault();
                create.mutate();
              }}
            >
              <label className="field">
                New queue
                <input required maxLength={100} value={newName} onChange={(e) => setNewName(e.target.value)} />
              </label>
              {create.error && <p className="error">{create.error.message}</p>}
              <button className="btn2" type="submit" disabled={create.isPending}>
                Add queue
              </button>
            </form>
          )}
        </div>
        {current && <QueueEditor key={current.id} queue={current} queues={list} me={me} agents={agents} groups={groups.data ?? []} />}
      </div>
    </>
  );
}
