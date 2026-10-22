import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ApiKeyMade, type User } from "../../api/client";
import { when } from "../../format";

/** Settings > API keys: keys other applications use for the integration API (look up tickets, add notes). */
export function ApiKeysAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const keys = useQuery({
    queryKey: ["api-keys"],
    queryFn: api.apiKeys.list,
    enabled: me.role === "admin",
  });
  const [name, setName] = useState("");
  const [made, setMade] = useState<ApiKeyMade | null>(null);
  const [revoking, setRevoking] = useState<number | null>(null);
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["api-keys"] });
  const create = useMutation({
    mutationFn: () => api.apiKeys.create(name.trim()),
    onSuccess: (k) => (setMade(k), setName("")),
    onSettled: refresh,
  });
  const revoke = useMutation({
    mutationFn: api.apiKeys.revoke,
    onSuccess: () => setRevoking(null),
    onSettled: refresh,
  });
  if (me.role !== "admin") return <p>API keys are managed by admins.</p>;

  return (
    <div className="stack">
      <div>
        <h1 style={{ margin: 0 }}>API keys</h1>
        <p className="muted" style={{ margin: "4px 0 0", maxWidth: "75ch" }}>
          For other applications, such as a server-room sign-in app: they can
          look up and search tickets and add internal notes (
          <code>/api/integrations</code>), nothing else. Each key acts as its
          own technician account, which can&apos;t sign in or be assigned work
          and sees what a technician sees (not restricted queues). Notes it adds
          carry its name.
        </p>
      </div>
      {made && (
        <section
          className="card note stack"
          style={{ margin: 0 }}
          aria-label="New API key"
        >
          <strong>
            Key for {made.name}: copy it now, it isn&apos;t shown again.
          </strong>
          <pre className="log-details code" style={{ margin: 0 }}>
            {made.token}
          </pre>
          <span className="small">
            The application sends it as{" "}
            <code>Authorization: Bearer {made.prefix}…</code> to{" "}
            {window.location.origin}/api/integrations/…
          </span>
          <div>
            <button
              className="btn2"
              type="button"
              onClick={() => setMade(null)}
            >
              Done
            </button>
          </div>
        </section>
      )}
      <form
        className="row-gap"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <input
          aria-label="Key name"
          placeholder="What uses it, e.g. MER server room"
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          className="btn"
          type="submit"
          disabled={!name.trim() || create.isPending}
        >
          Make a key
        </button>
      </form>
      {create.error && <p className="error">{create.error.message}</p>}
      <table className="grid">
        <thead>
          <tr>
            <th>Name</th>
            <th>Key</th>
            <th>Made</th>
            <th>Last used</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {keys.data?.map((k) => (
            <tr key={k.id} className={k.is_active ? undefined : "muted"}>
              <td>{k.name}</td>
              <td className="mono small">{k.prefix}…</td>
              <td className="small">
                {when(k.created_at)}
                {k.created_by && ` by ${k.created_by}`}
              </td>
              <td className="small">
                {k.last_used_at ? when(k.last_used_at) : "never"}
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                {!k.is_active ? (
                  <span className="small">Revoked</span>
                ) : revoking === k.id ? (
                  <>
                    <span className="small">Stops working at once. </span>
                    <button
                      className="btn2"
                      type="button"
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(k.id)}
                    >
                      Revoke
                    </button>{" "}
                    <button
                      className="linkish small"
                      type="button"
                      onClick={() => setRevoking(null)}
                    >
                      Keep
                    </button>
                  </>
                ) : (
                  <button
                    className="linkish small"
                    type="button"
                    onClick={() => setRevoking(k.id)}
                  >
                    Revoke…
                  </button>
                )}
              </td>
            </tr>
          ))}
          {keys.data?.length === 0 && (
            <tr>
              <td colSpan={5} className="muted small">
                No keys yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
