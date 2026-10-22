import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { api, type DirectoryGroupHit, type RoleMapping, type RoleMappingIn, type SyncRun, type User } from "../../api/client";
import { when } from "../../format";
import { SettingsForm } from "./SettingsForm";

type Draft = { group_id: number; name: string; role: "admin" | "agent"; members: number; error: string | null };

/** Type at least two letters; pick an AD group; it's imported (members synced) on the spot. */
function GroupPicker({ label, onPick, exclude }: { label: string; onPick: (g: { id: number; name: string; members: number }) => void; exclude: Set<number> }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);
  const hits = useQuery({ queryKey: ["ad-groups", debounced], queryFn: () => api.directory.searchGroups(debounced), enabled: debounced.length >= 2 });
  const importing = useMutation({
    mutationFn: (g: DirectoryGroupHit) => api.directory.importGroup(g.guid),
    onSuccess: (g) => {
      onPick({ id: g.id, name: g.name, members: g.members.length });
      setQ("");
    },
  });
  return (
    <div className="picker">
      <input aria-label={label} placeholder={label} value={q} onChange={(e) => setQ(e.target.value)} />
      {hits.error && <p className="error small">{hits.error.message}</p>}
      {importing.error && <p className="error small">{importing.error.message}</p>}
      {debounced.length >= 2 && hits.data && (
        <ul className="picker-list" role="listbox" aria-label={`${label} results`}>
          {hits.data.length === 0 && <li className="muted small">No groups match.</li>}
          {hits.data.map((g) => {
            const taken = g.group_id !== null && exclude.has(g.group_id);
            return (
              <li key={g.guid}>
                <button type="button" role="option" aria-selected={false} disabled={taken || importing.isPending} onClick={() => importing.mutate(g)}>
                  <strong>{g.name}</strong> {taken && <span className="muted small">(already added)</span>}
                  <span className="muted small doc-dn">{g.dn}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function fromMappings(rows: RoleMapping[]): Draft[] {
  return rows.map((m) => ({ group_id: m.group.id, name: m.group.name, role: m.role as Draft["role"], members: m.member_count, error: m.sync_error }));
}

function Permissions() {
  const queryClient = useQueryClient();
  const saved = useQuery({ queryKey: ["role-mappings"], queryFn: api.directory.mappings });
  const [draft, setDraft] = useState<Draft[] | null>(null);
  useEffect(() => {
    if (saved.data && draft === null) setDraft(fromMappings(saved.data));
  }, [saved.data, draft]);

  const body: RoleMappingIn[] = (draft ?? []).map((d) => ({ group_id: d.group_id, role: d.role }));
  const dirty = saved.data !== undefined && draft !== null && JSON.stringify(body) !== JSON.stringify(fromMappings(saved.data).map((d) => ({ group_id: d.group_id, role: d.role })));
  const preview = useQuery({ queryKey: ["role-preview", body], queryFn: () => api.directory.preview(body), enabled: dirty });
  const save = useMutation({
    mutationFn: () => api.directory.saveMappings(body),
    onSuccess: (rows) => {
      setDraft(fromMappings(rows));
      queryClient.invalidateQueries({ queryKey: ["role-mappings"] });
      queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });

  if (!draft) return null;
  const column = (role: Draft["role"], title: string, blurb: string) => {
    const rows = draft.filter((d) => d.role === role);
    return (
      <div className="card stack" style={{ margin: 0 }}>
        <div>
          <div className="lbl">{title}</div>
          <p className="muted small" style={{ margin: 0 }}>
            {blurb}
          </p>
        </div>
        <ul className="docs">
          {rows.length === 0 && <li className="muted small">No groups yet.</li>}
          {rows.map((d) => (
            <li key={d.group_id}>
              <span className="doc-icon" aria-hidden="true">
                AD
              </span>
              <span className="doc-main">
                <span className="doc-name">{d.name}</span>
                <span className={d.error ? "error small" : "muted small"}>{d.error ?? `${d.members} member${d.members === 1 ? "" : "s"} (including nested groups)`}</span>
              </span>
              <button type="button" className="linkish" aria-label={`Remove ${d.name} from ${title}`} onClick={() => setDraft(draft.filter((x) => x.group_id !== d.group_id))}>
                Remove
              </button>
            </li>
          ))}
        </ul>
        <GroupPicker
          label={`Add an AD group to ${title.toLowerCase()}`}
          exclude={new Set(draft.map((d) => d.group_id))}
          onPick={(g) => setDraft([...draft, { group_id: g.id, name: g.name, role, members: g.members, error: null }])}
        />
      </div>
    );
  };

  return (
    <section className="stack">
      <div>
        <h2>Permissions from AD groups</h2>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          Directory users get the highest role of any group they're in, including through nested groups. Everyone else is a requester. Local accounts keep the
          role set on the Users page.
        </p>
      </div>
      <div className="two-col">
        {column("admin", "Admins", "Full control: settings, rules, directory, every queue.")}
        {column("agent", "Agents", "Work tickets in the agent console. Queue owners further limit who can be assigned what.")}
      </div>
      {dirty && (
        <div className={`card${preview.data?.error ? " note" : ""}`} role="status" aria-live="polite">
          <div className="lbl">If you save</div>
          {preview.isPending && <p className="muted small">Checking…</p>}
          {preview.data?.error && <p className="error">{preview.data.error}</p>}
          {preview.data && !preview.data.error && (
            <>
              <p style={{ margin: "0 0 6px" }}>
                {preview.data.admins} admin(s) and {preview.data.agents} agent(s) afterwards. {preview.data.changes.length === 0 && "No one's role changes."}
              </p>
              {preview.data.changes.length > 0 && (
                <ul className="history">
                  {preview.data.changes.map((c, i) => (
                    <li key={i}>
                      <span>{String(c.user)}</span>
                      <span>{String(c.text)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
      {save.error && <p className="error">{save.error.message}</p>}
      <div className="row-gap">
        <button className="btn" type="button" disabled={!dirty || save.isPending || !!preview.data?.error} onClick={() => save.mutate()}>
          Save permissions
        </button>
        {dirty && (
          <button className="btn2" type="button" onClick={() => saved.data && setDraft(fromMappings(saved.data))}>
            Discard changes
          </button>
        )}
      </div>
    </section>
  );
}

function CheckPerson() {
  const [login, setLogin] = useState("");
  const look = useMutation({ mutationFn: () => api.directory.lookup(login) });
  function submit(e: FormEvent) {
    e.preventDefault();
    look.mutate();
  }
  const r = look.data;
  return (
    <section className="card stack" style={{ margin: 0 }}>
      <div className="lbl">Check a person</div>
      <form className="row-gap" onSubmit={submit}>
        <input aria-label="Email, UPN or username" placeholder="jpark, CORP\jpark or jpark@corp.local" className="spacer" value={login} onChange={(e) => setLogin(e.target.value)} />
        <button className="btn2" type="submit" disabled={!login.trim() || look.isPending}>
          Look up
        </button>
      </form>
      {look.error && <p className="error">{look.error.message}</p>}
      {r && !r.found && <p className="muted">Not found in the directory (or more than one match).</p>}
      {r?.found && (
        <dl className="facts">
          <dt>Name</dt>
          <dd>
            {r.display_name} {r.enabled === false && <span className="chip r">Disabled in AD</span>}
          </dd>
          <dt>Email</dt>
          <dd>
            {r.email}
            {r.aliases.length > 0 && <span className="muted small"> · also {r.aliases.join(", ")}</span>}
          </dd>
          <dt>Sign-in names</dt>
          <dd>{[r.upn, r.sam].filter(Boolean).join(", ")}</dd>
          <dt>Imported groups</dt>
          <dd>{r.groups.length ? r.groups.join(", ") : <span className="muted">none</span>}</dd>
          <dt>Role here</dt>
          <dd>
            <strong>{r.role}</strong> <span className="muted small">{r.role_source === "default" ? "(no mapped group)" : `(via ${r.role_source?.replace("group:", "")})`}</span>
          </dd>
        </dl>
      )}
    </section>
  );
}

function RunSummary({ run }: { run: SyncRun }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <span className="muted">{when(run.started_at)}</span>
      <span>
        {run.dry_run && <span className="chip n">dry run</span>}{" "}
        {run.error ? (
          <span className="error">{run.error}</span>
        ) : (
          `${run.users_seen} users: ${run.created} new, ${run.linked} linked, ${run.updated} updated, ${run.deactivated} deactivated, ${run.role_changes} role changes; ${run.devices_seen ?? 0} computers`
        )}{" "}
        {run.details.length > 0 && (
          <button type="button" className="linkish small" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Hide details" : `Details (${run.details.length})`}
          </button>
        )}
        {open && (
          <ul className="history" style={{ marginTop: 6 }}>
            {run.details.map((d, i) => (
              <li key={i}>
                <span className="muted small">{String(d.kind)}</span>
                <span className="small">
                  {String(d.user)}: {String(d.text)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </span>
    </li>
  );
}

function Connection() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["directory-status"], queryFn: api.directory.status });
  const runs = useQuery({ queryKey: ["directory-runs"], queryFn: api.directory.runs });
  const test = useMutation({ mutationFn: api.directory.test });
  const sync = useMutation({
    mutationFn: (dry: boolean) => api.directory.sync(dry),
    onSettled: () => {
      for (const k of ["directory-status", "directory-runs", "role-mappings", "users"]) queryClient.invalidateQueries({ queryKey: [k] });
    },
  });
  const s = status.data;
  if (!s) return null;
  return (
    <section className="card stack" style={{ margin: 0 }}>
      <div className="lbl">Connection and sync</div>
      {!s.configured ? (
        <p className="muted" style={{ margin: 0 }}>
          No directory configured: everyone uses local accounts. Fill in the connection settings below to sync people and groups from AD.
        </p>
      ) : (
        <>
          <dl className="facts">
            <dt>Servers</dt>
            <dd>{s.servers.join(", ")}</dd>
            <dt>Base DN</dt>
            <dd>{s.base_dn}</dd>
            <dt>Type</dt>
            <dd>{s.kind === "ad" ? "Active Directory" : "LDAP"}</dd>
            <dt>Sync</dt>
            <dd>
              every {s.sync_minutes} min · {s.directory_users} active directory users · {s.tracked_groups} imported groups
              {s.last_run && ` · last ${when(s.last_run.started_at)}${s.last_run.error ? " (failed)" : ""}`}
            </dd>
          </dl>
          <div className="row-gap">
            <button className="btn2" type="button" disabled={test.isPending} onClick={() => test.mutate()}>
              Test connection
            </button>
            <button className="btn2" type="button" disabled={sync.isPending} onClick={() => sync.mutate(true)}>
              Dry run
            </button>
            <button className="btn" type="button" disabled={sync.isPending} onClick={() => sync.mutate(false)}>
              Sync now
            </button>
          </div>
          {test.data && (
            <p className="small" role="status">
              Connected to {test.data.server} as {test.data.bound_as}.
            </p>
          )}
          {(test.error || sync.error) && <p className="error">{(test.error ?? sync.error)!.message}</p>}
          {runs.data && runs.data.length > 0 && (
            <ul className="history">
              {runs.data.map((r) => (
                <RunSummary key={r.id} run={r} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

export function DirectoryAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["directory-status"], queryFn: api.directory.status, enabled: me.role === "admin" });
  if (me.role !== "admin") return <p>The directory is managed by admins.</p>;
  return (
    <>
      <div className="page-head">
        <h1>Directory</h1>
      </div>
      <div className="stack">
        {status.data?.configured && (
          <>
            <Permissions />
            <CheckPerson />
          </>
        )}
        <Connection />
        <SettingsForm
          me={me}
          section="directory"
          title="Connection settings"
          intro="Save, then use Test connection above."
          embedded
          onSaved={() => queryClient.invalidateQueries({ queryKey: ["directory-status"] })}
        />
      </div>
    </>
  );
}
