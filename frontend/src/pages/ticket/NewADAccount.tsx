import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ADGroup, type NewADAccount, type NewADAccountMade, type Ticket, type User } from "../../api/client";
import { GroupLists } from "../../components/GroupLists";
import { CopyGroups } from "../../components/CopyGroups";
import { GroupPicker } from "../../components/GroupPicker";
import { UserPicker } from "../../components/UserPicker";

type Person = Pick<User, "id" | "display_name" | "email">;
const EMPTY: NewADAccount = {
  given_name: "", surname: "", display_name: "", sam: "", mail: "", ou: "", phone: "", extension: "", mobile: "",
  department: "", title: "", office: "", company: "", description: "", manager_id: null, groups: [], template_id: null, start_date: null, email_manager: false,
};
const TEXT = ["phone", "extension", "mobile", "department", "title", "office", "company", "description"] as const;

/** Onboarding: a new AD account from the ticket. Checked first (name from the format, clashes in AD),
 * then created on a PowerShell runner; the first password is shown here once and kept nowhere. */
export function NewADAccountForm({
  ticket,
  defaultManager,
  onDone,
}: {
  ticket: Ticket;
  defaultManager: Person | null;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [f, setF] = useState<NewADAccount>(EMPTY);
  const [manager, setManager] = useState<Person | null>(defaultManager);
  const [groups, setGroups] = useState<ADGroup[]>([]);
  const [made, setMade] = useState<NewADAccountMade | null>(null);
  const [copied, setCopied] = useState(false);
  const templates = useQuery({ queryKey: ["onboarding-templates"], queryFn: api.ad.templates });
  const ous = useQuery({ queryKey: ["ad-ous"], queryFn: () => api.ad.ous() });
  const body: NewADAccount = { ...f, manager_id: manager?.id ?? null, groups: groups.map((g) => g.dn) };
  const preview = useMutation({ mutationFn: () => api.ad.previewNew(ticket.id, body) });
  const create = useMutation({
    mutationFn: () => api.ad.createNew(ticket.id, body),
    onSuccess: (r) => {
      setMade(r);
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const set = (patch: Partial<NewADAccount>) => (preview.reset(), setF({ ...f, ...patch }));
  const ticked = (dn: string) => groups.some((g) => g.dn.toLowerCase() === dn.toLowerCase());
  const toggle = (g: ADGroup, on: boolean) => (preview.reset(), setGroups(on ? [...groups, g] : groups.filter((x) => x.dn.toLowerCase() !== g.dn.toLowerCase())));
  const p = preview.data;

  if (made) {
    return (
      <div className="card note stack" style={{ margin: 0, gap: 8 }} role="region" aria-label="Account requested">
        <strong>
          {made.sam} ({made.upn}) is queued on the runner.
        </strong>
        <span className="small">First password, shown once. Layer 8 doesn't keep a copy, so give it to the person (or their manager) now:</span>
        <div className="row-gap">
          <span className="password-once" aria-label="First password">
            {made.password}
          </span>
          <button
            className="btn2"
            type="button"
            onClick={() =>
              navigator.clipboard.writeText(made.password).then(
                () => setCopied(true),
                () => setCopied(false),
              )
            }
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <span className="muted small">Whether it worked shows under Scripts below, and the details are noted on this ticket.</span>
        <div>
          <button className="btn" type="button" onClick={onDone}>
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <form
      className="stack"
      style={{ gap: 8 }}
      aria-label="New AD account"
      onSubmit={(e) => {
        e.preventDefault();
        preview.mutate();
      }}
    >
      <div className="form-grid">
        <label className="field">
          First name
          <input required value={f.given_name} onChange={(e) => set({ given_name: e.target.value })} />
        </label>
        <label className="field">
          Last name
          <input required value={f.surname} onChange={(e) => set({ surname: e.target.value })} />
        </label>
        <label className="field">
          Display name
          <input value={f.display_name ?? ""} onChange={(e) => set({ display_name: e.target.value })} placeholder={f.given_name || f.surname ? `${f.given_name} ${f.surname}`.trim() : "First and last name"} />
        </label>
        <label className="field">
          Account name
          <input value={f.sam ?? ""} maxLength={20} onChange={(e) => set({ sam: e.target.value.toLowerCase() })} placeholder={p?.sam ?? "From the name format"} />
        </label>
        <label className="field">
          Email address
          <input type="email" value={f.mail ?? ""} onChange={(e) => set({ mail: e.target.value })} placeholder={p?.upn ?? "e.g. jsmith@example.com"} />
          {p && !f.mail && p.upn.includes("@") && (
            <button className="linkish small" type="button" style={{ justifySelf: "start" }} onClick={() => set({ mail: p.upn })}>
              Use {p.upn}
            </button>
          )}
        </label>
        <label className="field">
          Phone number
          <input type="tel" value={f.phone ?? ""} onChange={(e) => set({ phone: e.target.value })} />
        </label>
        <label className="field">
          Extension
          <input value={f.extension ?? ""} onChange={(e) => set({ extension: e.target.value })} />
        </label>
        <label className="field">
          Mobile
          <input type="tel" value={f.mobile ?? ""} onChange={(e) => set({ mobile: e.target.value })} />
        </label>
        <UserPicker label="Manager" value={manager} onChange={(u) => (preview.reset(), setManager(u))} />
        <label className="field">
          Start date <span className="muted small">(later: created disabled, enabled that day)</span>
          <input type="date" value={f.start_date ?? ""} onChange={(e) => set({ start_date: e.target.value || null })} />
        </label>
        <label className="inline" style={{ alignSelf: "end" }}>
          <input type="checkbox" checked={!!f.email_manager} onChange={(e) => set({ email_manager: e.target.checked })} /> Email the manager when it's created (not the password)
        </label>
        <label className="field">
          Department
          <input value={f.department ?? ""} onChange={(e) => set({ department: e.target.value })} placeholder="From the template, if any" />
        </label>
        <label className="field">
          Job title
          <input value={f.title ?? ""} onChange={(e) => set({ title: e.target.value })} placeholder="From the template, if any" />
        </label>
        <label className="field">
          Office
          <input value={f.office ?? ""} onChange={(e) => set({ office: e.target.value })} />
        </label>
        <label className="field">
          Company
          <input value={f.company ?? ""} onChange={(e) => set({ company: e.target.value })} />
        </label>
        <label className="field">
          Description
          <input value={f.description ?? ""} onChange={(e) => set({ description: e.target.value })} />
        </label>
        {(templates.data?.length ?? 0) > 0 && (
          <label className="field">
            Onboarding template
            <select value={f.template_id ?? ""} onChange={(e) => set({ template_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">None</option>
              {templates.data!.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          OU
          <select value={f.ou ?? ""} onChange={(e) => set({ ou: e.target.value })}>
            <option value="">The template's, else the default</option>
            {ous.data?.map((o) => (
              <option key={o.dn} value={o.dn}>
                {o.dn}
              </option>
            ))}
          </select>
        </label>
      </div>
      <GroupLists isTicked={ticked} onToggle={toggle} />
      <div className="row-gap" role="list" aria-label="Groups to add">
        {groups.map((g) => (
          <span key={g.dn} role="listitem" className="chip n" title={g.dn}>
            {g.name}{" "}
            <button className="linkish small" type="button" aria-label={`Don't add ${g.name}`} onClick={() => toggle(g, false)}>
              ×
            </button>
          </span>
        ))}
      </div>
      <GroupPicker label="Add a group (e.g. the 365 licence group)" exclude={groups.map((g) => g.dn)} onPick={(g) => toggle(g, true)} />
      <CopyGroups
        onCopy={(gs) => {
          preview.reset();
          const have = new Set(groups.map((g) => g.dn.toLowerCase()));
          setGroups([...groups, ...gs.filter((g) => !have.has(g.dn.toLowerCase()))]);
        }}
      />

      {p && (
        <div className="card note stack" style={{ margin: 0, gap: 4 }} role="region" aria-label="Before it's created">
          <strong>
            {p.display_name}: {p.sam} · {p.upn}
          </strong>
          <div className="small">
            In {p.ou}
            {p.mail && <> · email {p.mail}</>}
            {p.manager && <> · reports to {p.manager}</>}
          </div>
          {TEXT.some((k) => p[k]) && (
            <div className="small muted">
              {TEXT.filter((k) => p[k])
                .map((k) => `${k === "extension" ? "ext." : k} ${p[k]}`)
                .join(" · ")}
            </div>
          )}
          {p.groups.map((g) => (
            <div key={g.dn} className="small">
              + {g.name}
              {g.warning && <div className="error small">⚠ {g.warning}</div>}
            </div>
          ))}
          <div className="small muted">
            {p.enabled ? "Enabled straight away" : p.start_date ? `Created disabled, enabled on ${new Date(p.start_date + "T00:00").toLocaleDateString()}` : "Created disabled"}
            {p.email_manager && p.manager && `; ${p.manager} is emailed once it's created`}
            {p.change_password && "; the password must be changed at first sign-in"}.
          </div>
          {p.problems.map((x) => (
            <div key={x} className="error small">
              {x}
            </div>
          ))}
          <div className="row-gap">
            <button className="btn" type="button" disabled={create.isPending || p.problems.length > 0} onClick={() => create.mutate()}>
              Create account
            </button>
            <button className="btn2" type="button" onClick={() => preview.reset()}>
              Change
            </button>
          </div>
        </div>
      )}
      {(preview.error ?? create.error) && <p className="error small">{(preview.error ?? create.error)!.message}</p>}
      {!p && (
        <div className="row-gap">
          <button className="btn2" type="submit" disabled={preview.isPending}>
            Check…
          </button>
          <button className="linkish small" type="button" onClick={onDone}>
            Cancel
          </button>
        </div>
      )}
    </form>
  );
}
