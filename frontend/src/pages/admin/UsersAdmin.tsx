import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { api, type User, type UserRole } from "../../api/client";
import { when } from "../../format";

const ROLES: UserRole[] = ["requester", "agent", "admin"];

function source(u: User): string {
  if (u.auth_source === "local") return "Set here";
  if (u.role_source === "default") return "No mapped group";
  return `via ${u.role_source.replace("group:", "")}`;
}

function LocalControls({ user }: { user: User }) {
  const queryClient = useQueryClient();
  const [pw, setPw] = useState<string | null>(null);
  const done = () => queryClient.invalidateQueries({ queryKey: ["users"] });
  const role = useMutation({ mutationFn: (r: UserRole) => api.updateUser(user.id, { role: r }), onSettled: done });
  const active = useMutation({ mutationFn: (v: boolean) => api.updateUser(user.id, { is_active: v }), onSettled: done });
  const password = useMutation({ mutationFn: () => api.setPassword(user.id, pw ?? ""), onSuccess: () => setPw(null), onSettled: done });
  const err = role.error ?? active.error ?? password.error;
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row-gap">
        <select aria-label={`Role for ${user.display_name}`} value={user.role} onChange={(e) => role.mutate(e.target.value as UserRole)} style={{ width: 140 }}>
          {ROLES.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <button className="linkish small" type="button" onClick={() => active.mutate(!user.is_active)}>
          {user.is_active ? "Deactivate" : "Reactivate"}
        </button>
        {pw === null ? (
          <button className="linkish small" type="button" onClick={() => setPw("")}>
            {user.has_password ? "Reset password" : "Set password"}
          </button>
        ) : (
          <form
            className="row-gap"
            onSubmit={(e) => {
              e.preventDefault();
              password.mutate();
            }}
          >
            <input type="password" aria-label={`New password for ${user.display_name}`} autoComplete="new-password" placeholder="12+ characters" value={pw} onChange={(e) => setPw(e.target.value)} style={{ width: 200 }} />
            <button className="btn2" type="submit" disabled={pw.length < 12 || password.isPending}>
              Save
            </button>
            <button className="linkish small" type="button" onClick={() => setPw(null)}>
              Cancel
            </button>
          </form>
        )}
      </div>
      {err && <p className="error small">{err.message}</p>}
    </div>
  );
}

type Mode = "directory" | "directory_with_fallback" | "local";
const MODE_LABEL: Record<Mode, string> = {
  directory: "AD only",
  directory_with_fallback: "AD + local fallback",
  local: "Local account",
};

/** For AD technicians: how they sign in, including a local password for when AD is down. */
function DirectoryStaffControls({ user }: { user: User }) {
  const queryClient = useQueryClient();
  const current: Mode = user.local_fallback ? "directory_with_fallback" : "directory";
  const [mode, setMode] = useState<Mode>(current);
  const [pw, setPw] = useState("");
  const needsPassword = (mode === "directory_with_fallback" && !user.local_fallback) || mode === "local";
  const save = useMutation({
    mutationFn: () => api.setSignIn(user.id, mode, pw || undefined),
    onSuccess: () => setPw(""),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
  const changed = mode !== current || pw.length > 0;
  return (
    <form
      className="stack"
      style={{ gap: 6 }}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="row-gap">
        <select aria-label={`Sign-in for ${user.display_name}`} value={mode} onChange={(e) => setMode(e.target.value as Mode)} style={{ width: 200 }}>
          {(Object.keys(MODE_LABEL) as Mode[]).map((m) => (
            <option key={m} value={m}>
              {MODE_LABEL[m]}
            </option>
          ))}
        </select>
        {mode !== "directory" && (
          <input
            type="password"
            autoComplete="new-password"
            aria-label={mode === "local" ? `Local password for ${user.display_name}` : `Fallback password for ${user.display_name}`}
            placeholder={needsPassword ? "Password (12+ characters)" : "New password (optional)"}
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            style={{ width: 210 }}
          />
        )}
        <button className="btn2" type="submit" disabled={!changed || (needsPassword && pw.length < 12) || save.isPending}>
          Save
        </button>
      </div>
      <span className="muted small">
        {mode === "directory_with_fallback"
          ? "Signs in with AD. The local password only works while AD can't be reached."
          : mode === "local"
            ? "Detached from AD: the role is then set here, and AD changes no longer apply."
            : "Signs in with AD only."}
      </span>
      {save.error && <p className="error small">{save.error.message}</p>}
    </form>
  );
}

export function UsersAdmin({ me }: { me: User }) {
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"all" | "staff" | "directory" | "local">("staff");
  const rows = useMemo(() => {
    const v = q.trim().toLowerCase();
    return (users.data ?? []).filter((u) => {
      if (u.email === "automation@helpdesk.invalid") return false;
      if (only === "staff" && u.role === "requester") return false;
      if (only === "directory" && u.auth_source !== "directory") return false;
      if (only === "local" && u.auth_source !== "local") return false;
      return !v || `${u.display_name} ${u.email} ${u.upn ?? ""} ${u.department ?? ""}`.toLowerCase().includes(v);
    });
  }, [users.data, q, only]);

  const isAdmin = me.role === "admin";
  return (
    <>
      <div className="page-head">
        <h1>Users</h1>
      </div>
      <div className="row-gap" style={{ marginBottom: 12 }}>
        <input aria-label="Search users" placeholder="Search name, email, department" className="spacer" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 380 }} />
        <div className="views" role="group" aria-label="Show" style={{ margin: 0 }}>
          {(["staff", "all", "directory", "local"] as const).map((k) => (
            <button key={k} type="button" aria-pressed={only === k} onClick={() => setOnly(k)}>
              {{ staff: "Agents and admins", all: "Everyone", directory: "From AD", local: "Local" }[k]}
            </button>
          ))}
        </div>
      </div>
      <p className="muted small">
        Directory users' roles come from AD groups (see <Link to="/settings/directory">Directory</Link>); disable them in AD. Local accounts are managed here.
      </p>
      {users.error && <p className="error">{users.error.message}</p>}
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Name</th>
              <th>Account</th>
              <th>Role</th>
              <th>Role from</th>
              <th>Department</th>
              <th>Last synced</th>
              {isAdmin && <th>Manage</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "inactive"}>
                <td>
                  {u.display_name}
                  <div className="muted small">{u.email}</div>
                </td>
                <td>
                  <span className={`chip ${u.auth_source === "directory" ? "g" : "n"}`}>{u.auth_source === "directory" ? "AD" : "Local"}</span>{" "}
                  {u.local_fallback && <span className="chip a">Local fallback</span>} {!u.is_active && <span className="chip r">Inactive</span>}
                </td>
                <td>{u.role}</td>
                <td className="small">{source(u)}</td>
                <td className="small">{u.department ?? ""}</td>
                <td className="small muted">{u.last_synced_at ? when(u.last_synced_at) : ""}</td>
                {isAdmin && (
                  <td>
                    {u.auth_source === "local" ? (
                      <LocalControls user={u} />
                    ) : u.role !== "requester" ? (
                      <DirectoryStaffControls user={u} />
                    ) : (
                      <span className="muted small">From AD</span>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {users.data && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="empty">
                  No users match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
