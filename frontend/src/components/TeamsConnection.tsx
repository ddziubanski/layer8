// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";

export function TeamsConnection() {
  const client = useQueryClient();
  const connection = useQuery({ queryKey: ["teams-connection"], queryFn: api.teams.connection });
  const connect = useMutation({ mutationFn: api.teams.connect, onSuccess: ({ url }) => window.location.assign(url) });
  const disconnect = useMutation({ mutationFn: api.teams.disconnect, onSuccess: () => client.invalidateQueries({ queryKey: ["teams-connection"] }) });
  return <section className="card stack" aria-label="Microsoft Teams connection">
    <h2>Microsoft Teams</h2>
    <p className="muted">Connect your work account to send ticket messages as yourself. Library can also use this account when enabled. Your Layer 8 sign-in stays the same.</p>
    {connection.data?.connected ? <>
      <p>Connected as <strong>{connection.data.account}</strong>.</p>
      <button className="btn2" type="button" disabled={connect.isPending} onClick={() => connect.mutate()}>Reconnect Microsoft account</button>
      <button className="btn2" type="button" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>Disconnect Teams</button>
    </> : connection.data?.configured ? <button className="btn" type="button" disabled={connect.isPending} onClick={() => connect.mutate()}>Connect Microsoft Teams</button>
      : <p className="muted">An administrator can enable technician connections in Settings → Teams.</p>}
    {(connection.error || connect.error || disconnect.error) && <p role="alert" className="error">{(connection.error || connect.error || disconnect.error)?.message}</p>}
  </section>;
}

export function TeamsCallback() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [response] = useState(() => {
    // OAuth codes stay in the fragment, never in HTTP access logs or referrers.
    const params = new URLSearchParams(window.location.hash.slice(1));
    return { state: params.get("state") ?? "", code: params.get("code"), error: params.get("error") };
  });
  const complete = useMutation({ mutationFn: () => api.teams.complete(response), onSuccess: async () => {
    await client.invalidateQueries({ queryKey: ["teams-connection"] });
    navigate("/preferences", { replace: true });
  } });
  const started = useRef(false);
  useEffect(() => { if (!started.current) { started.current = true; window.history.replaceState(null, "", window.location.pathname); complete.mutate(); } }, []);
  return <section className="card stack"><h1>Connect Microsoft Teams</h1>
    {complete.error ? <><p className="error" role="alert">{complete.error.message}</p><Link to="/preferences">Return to My preferences and connect again</Link></>
      : <p role="status">Verifying your Microsoft work account…</p>}
  </section>;
}
