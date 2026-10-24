// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ADChange, type ADGroup, type Ticket } from "../../api/client";
import { GroupLists } from "../../components/GroupLists";
import { CopyGroups } from "../../components/CopyGroups";
import { GroupPicker } from "../../components/GroupPicker";
import { NewADAccountForm } from "./NewADAccount";

const EMPTY: ADChange = { add: [], remove: [], move_to_ou: null, department: null, title: null, template_id: null };

/** The requester's AD account: groups and OU, changed through a PowerShell runner (with a preview first). */
export function ADAccountCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const account = useQuery({ queryKey: ["requester-ad", ticket.id, ticket.requester.id], queryFn: () => api.ad.requester(ticket.id) });
  const templates = useQuery({ queryKey: ["onboarding-templates"], queryFn: api.ad.templates, enabled: account.data?.enabled === true && !account.data.problem });
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [change, setChange] = useState<ADChange>(EMPTY);
  const [adding, setAdding] = useState<ADGroup[]>([]);
  const ous = useQuery({ queryKey: ["ad-ous"], queryFn: () => api.ad.ous(), enabled: open });
  const body: ADChange = { ...change, add: adding.map((g) => g.dn) };
  const preview = useMutation({ mutationFn: () => api.ad.preview(ticket.id, body) });
  const apply = useMutation({
    mutationFn: () => api.ad.apply(ticket.id, body),
    onSuccess: () => {
      setChange(EMPTY);
      setAdding([]);
      preview.reset();
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      onChanged();
    },
  });
  const a = account.data;
  if (!a) return null;
  if (!a.enabled) return null; // not set up: the card stays out of the way
  const removing = new Set((change.remove ?? []).map((d) => d.toLowerCase()));
  const toggleRemove = (dn: string) => {
    preview.reset();
    setChange({ ...change, remove: removing.has(dn.toLowerCase()) ? (change.remove ?? []).filter((d) => d.toLowerCase() !== dn.toLowerCase()) : [...(change.remove ?? []), dn] });
  };
  const member = new Set(a.groups.map((g) => g.dn.toLowerCase()));
  const ticked = (dn: string) => (member.has(dn.toLowerCase()) ? !removing.has(dn.toLowerCase()) : adding.some((g) => g.dn.toLowerCase() === dn.toLowerCase()));
  const tick = (g: ADGroup, on: boolean) => {
    if (member.has(g.dn.toLowerCase())) return toggleRemove(g.dn);
    preview.reset();
    setAdding(on ? [...adding, g] : adding.filter((x) => x.dn.toLowerCase() !== g.dn.toLowerCase()));
  };
  const nothing = !adding.length && !(change.remove ?? []).length && !change.move_to_ou && !change.department && !change.title && !change.template_id;

  return (
    <section className="card stack" style={{ gap: 10 }} aria-label="AD account">
      <div className="row-gap">
        <div className="lbl spacer" style={{ margin: 0 }}>
          AD account {a.name && <span className="muted">· {a.name}</span>}
        </div>
        {!a.problem && !open && !creating && (
          <button className="btn2" type="button" onClick={() => setOpen(true)}>
            Change groups or OU…
          </button>
        )}
        {a.can_create && !open && !creating && (
          <button className="btn2" type="button" onClick={() => setCreating(true)}>
            New account…
          </button>
        )}
      </div>
      {a.problem && <p className="muted small" style={{ margin: 0 }}>{a.problem}</p>}
      {!a.problem && (
        <>
          <div className="small muted">OU: {a.ou}</div>
          <div className="row-gap" role="list" aria-label="Groups">
            {a.groups.map((g) => (
              <span key={g.dn} role="listitem" className={`chip ${removing.has(g.dn.toLowerCase()) ? "r" : "n"}`} title={g.dn}>
                {g.name}
                {open && g.changeable && (
                  <>
                    {" "}
                    <button className="linkish small" type="button" aria-label={`${removing.has(g.dn.toLowerCase()) ? "Keep" : "Remove from"} ${g.name}`} onClick={() => toggleRemove(g.dn)}>
                      {removing.has(g.dn.toLowerCase()) ? "keep" : "×"}
                    </button>
                  </>
                )}
              </span>
            ))}
            {a.groups.length === 0 && <span className="muted small">In no groups (beyond Domain Users).</span>}
          </div>
        </>
      )}
      {creating && (
        <NewADAccountForm
          ticket={ticket}
          defaultManager={a.problem ? null : ticket.requester}
          onDone={() => (setCreating(false), onChanged())}
        />
      )}
      {open && (
        <div className="stack" style={{ gap: 8 }}>
          {(templates.data?.length ?? 0) > 0 && (
            <label className="field">
              Onboarding template
              <select value={change.template_id ?? ""} onChange={(e) => (preview.reset(), setChange({ ...change, template_id: e.target.value ? Number(e.target.value) : null }))}>
                <option value="">None</option>
                {templates.data!.filter((t) => t.is_active).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {adding.length > 0 && (
            <div className="row-gap">
              {adding.map((g) => (
                <span key={g.dn} className="chip g">
                  + {g.name}{" "}
                  <button className="linkish small" type="button" aria-label={`Don't add ${g.name}`} onClick={() => (preview.reset(), setAdding(adding.filter((x) => x.dn !== g.dn)))}>
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <GroupLists isTicked={ticked} onToggle={tick} />
          <GroupPicker label="Add to a group" exclude={[...a.groups.map((g) => g.dn), ...adding.map((g) => g.dn)]} onPick={(g) => (preview.reset(), setAdding([...adding, g]))} />
          <CopyGroups
            onCopy={(gs) => {
              preview.reset();
              const have = new Set([...a.groups, ...adding].map((g) => g.dn.toLowerCase()));
              setAdding([...adding, ...gs.filter((g) => !have.has(g.dn.toLowerCase()))]);
            }}
          />
          <div className="form-grid">
            <label className="field">
              Move to OU
              <select value={change.move_to_ou ?? ""} onChange={(e) => (preview.reset(), setChange({ ...change, move_to_ou: e.target.value || null }))}>
                <option value="">Leave where it is</option>
                {ous.data?.map((o) => (
                  <option key={o.dn} value={o.dn}>
                    {o.dn}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Department
              <input value={change.department ?? ""} onChange={(e) => (preview.reset(), setChange({ ...change, department: e.target.value || null }))} placeholder="Leave as is" />
            </label>
            <label className="field">
              Job title
              <input value={change.title ?? ""} onChange={(e) => (preview.reset(), setChange({ ...change, title: e.target.value || null }))} placeholder="Leave as is" />
            </label>
          </div>
          {!preview.data && (
            <div className="row-gap">
              <button className="btn2" type="button" disabled={nothing || preview.isPending} onClick={() => preview.mutate()}>
                Check changes…
              </button>
              <button className="linkish small" type="button" onClick={() => (setOpen(false), setChange(EMPTY), setAdding([]), preview.reset())}>
                Cancel
              </button>
            </div>
          )}
          {preview.data && (
            <div className="card note stack" style={{ margin: 0, gap: 4 }} role="region" aria-label="Before it runs">
              <strong>For {a.name}:</strong>
              {preview.data.add.map((g) => (
                <div key={g.dn} className="small">
                  + add to <strong>{g.name}</strong>
                  {g.warning && <div className="error small">⚠ {g.warning}</div>}
                </div>
              ))}
              {preview.data.already.map((g) => (
                <div key={g.dn} className="small muted">
                  already in {g.name}
                </div>
              ))}
              {preview.data.remove.map((g) => (
                <div key={g.dn} className="small">
                  − remove from <strong>{g.name}</strong>
                </div>
              ))}
              {preview.data.move_to_ou && (
                <div className="small">
                  move from {preview.data.current_ou} to <strong>{preview.data.move_to_ou}</strong>
                </div>
              )}
              {preview.data.department && <div className="small">department: {preview.data.department}</div>}
              {preview.data.title && <div className="small">title: {preview.data.title}</div>}
              <span className="muted small">It runs on a PowerShell relay and is noted on this ticket; its result shows under Scripts below.</span>
              <div className="row-gap">
                <button className="btn" type="button" disabled={apply.isPending} onClick={() => apply.mutate()}>
                  Make these changes
                </button>
                <button className="btn2" type="button" onClick={() => preview.reset()}>
                  Change
                </button>
              </div>
            </div>
          )}
          {(preview.error ?? apply.error) && <p className="error small">{(preview.error ?? apply.error)!.message}</p>}
        </div>
      )}
    </section>
  );
}
