// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type PermissionRule, type PermissionRuleIn } from "../../api/client";

const WHO: [PermissionRuleIn["who"], string][] = [
  ["agents", "All technicians"],
  ["admins", "Admins only"],
  ["groups", "Admins and these groups"],
];

/** Settings > Permissions: who may take each action, beyond technician and admin. */
export function PermissionsAdmin() {
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ["permissions"], queryFn: api.permissions.list });
  const groups = useQuery({ queryKey: ["groups"], queryFn: api.groups });
  const [draft, setDraft] = useState<Record<string, PermissionRuleIn>>({});
  const save = useMutation({
    mutationFn: () => api.permissions.save(draft),
    onSuccess: (data) => {
      queryClient.setQueryData(["permissions"], data);
      queryClient.invalidateQueries({ queryKey: ["me"] });
      setDraft({});
    },
  });
  const active = (groups.data ?? []).filter((g) => g.is_active);
  const value = (r: PermissionRule): PermissionRuleIn => draft[r.key] ?? { who: r.who, group_ids: r.group_ids };
  const set = (r: PermissionRule, v: PermissionRuleIn) => (setDraft({ ...draft, [r.key]: v }), save.reset());
  const areas = [...new Set((rules.data ?? []).map((r) => r.area))];
  const changed = Object.keys(draft).length;

  return (
    <div className="stack">
      <div>
        <h1 style={{ margin: 0 }}>Permissions</h1>
        <p className="muted" style={{ margin: "4px 0 0", maxWidth: "75ch" }}>
          Who may take each action, beyond the technician and admin roles (those come from AD groups under Directory). Admins always may; requesters never. Groups can
          be local or synced from AD (Settings &gt; Groups).
        </p>
      </div>
      {areas.map((area) => (
        <section key={area} className="card stack" style={{ margin: 0, gap: 4 }} aria-label={area}>
          <div className="lbl" style={{ margin: 0 }}>
            {area}
          </div>
          {(rules.data ?? [])
            .filter((r) => r.area === area)
            .map((r) => {
              const v = value(r);
              return (
                <div key={r.key} className="schedule-row" role="group" aria-label={r.label}>
                  <div className="spacer" style={{ minWidth: 0 }}>
                    <strong>{r.label}</strong>
                    {r.help && <div className="muted small">{r.help}</div>}
                    {v.who === "groups" && (
                      <div className="filter-choices" style={{ marginTop: 6 }} aria-label="Groups">
                        {active.length === 0 && <span className="muted small">No groups yet: add one under Settings &gt; Groups.</span>}
                        {active.map((g) => (
                          <label key={g.id} className="inline small">
                            <input
                              type="checkbox"
                              checked={(v.group_ids ?? []).includes(g.id)}
                              onChange={(e) => set(r, { who: "groups", group_ids: e.target.checked ? [...(v.group_ids ?? []), g.id] : (v.group_ids ?? []).filter((x) => x !== g.id) })}
                            />
                            {g.name}
                            {g.source === "directory" && <span className="muted"> (AD)</span>}
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                  <select
                    style={{ width: 240, flex: "none" }}
                    aria-label={`Who may: ${r.label}`}
                    value={v.who}
                    onChange={(e) => set(r, { who: e.target.value as PermissionRuleIn["who"], group_ids: e.target.value === "groups" ? (v.group_ids ?? []) : [] })}
                  >
                    {WHO.map(([w, label]) => (
                      <option key={w} value={w}>
                        {label}
                        {w === r.default ? " (usual)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
              );
            })}
        </section>
      ))}
      {save.error && (
        <p className="error" role="alert">
          {save.error.message}
        </p>
      )}
      <div className="row-gap">
        <button className="btn" type="button" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
          Save {changed ? `${changed} change${changed > 1 ? "s" : ""}` : ""}
        </button>
        {changed > 0 && (
          <button className="btn2" type="button" onClick={() => setDraft({})}>
            Undo
          </button>
        )}
        {save.isSuccess && (
          <span className="small" role="status">
            Saved.
          </span>
        )}
      </div>
    </div>
  );
}
