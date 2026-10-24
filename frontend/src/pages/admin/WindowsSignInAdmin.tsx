// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api, type User, type WindowsSignIn } from "../../api/client";
import { useDropFiles } from "../../components/useDropFiles";
import { when } from "../../format";

const KEY = ["windows-sign-in"];

/** Portal single sign-on as the logged-on Windows user (Kerberos): keytab and on/off. */
export function WindowsSignInAdmin({ me }: { me: User }) {
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: KEY, queryFn: api.settings.windowsSignIn, enabled: me.role === "admin" });
  const file = useRef<HTMLInputElement>(null);
  const done = (data: WindowsSignIn) => {
    queryClient.setQueryData(KEY, data);
    // The sign-in page reads whether it's on.
    queryClient.invalidateQueries({ queryKey: ["sign-in-options"] });
  };
  const save = useMutation({ mutationFn: api.settings.saveWindowsSignIn, onSuccess: done });
  const upload = useMutation({
    mutationFn: api.settings.uploadKeytab,
    onSuccess: done,
    onSettled: () => {
      if (file.current) file.current.value = "";
    },
  });
  const remove = useMutation({ mutationFn: api.settings.removeKeytab, onSuccess: done });
  const drop = useDropFiles((files) => upload.mutate(files[0]), { disabled: upload.isPending });

  if (me.role !== "admin") return <p>Windows sign-in is managed by admins.</p>;
  const c = config.data;
  if (!c) return config.error ? <p className="error">{config.error.message}</p> : null;
  const hasKeytab = c.keytab_principals.length > 0;
  const error = save.error ?? upload.error ?? remove.error;

  return (
    <>
      <div className="page-head">
        <h1>Windows sign-in</h1>
      </div>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          People opening the portal on a domain PC are signed in as their Windows user, with no password. It only signs in to the portal: the
          agent console always asks for a password.
        </p>

        <section className={`card stack${drop.dragging ? " dropping" : ""}`} style={{ margin: 0 }} aria-label="Service keytab" {...drop.props}>
          <div className="lbl">Service keytab</div>
          {hasKeytab ? (
            <dl className="facts">
              <dt>Service principal</dt>
              <dd>{c.keytab_principals.join(", ")}</dd>
              <dt>Realm</dt>
              <dd>{c.realm}</dd>
              <dt>Encryption</dt>
              <dd>{c.keytab_enctypes.join(", ")}</dd>
              <dt>Uploaded</dt>
              <dd>
                {c.keytab_uploaded_at ? when(c.keytab_uploaded_at) : "unknown"}
                {c.keytab_uploaded_by && ` by ${c.keytab_uploaded_by}`}
              </dd>
            </dl>
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              No keytab yet. Create a service account with the SPN <code>HTTP/&lt;portal host name&gt;</code>, set it to AES, and export its keytab with{" "}
              <code>ktpass</code> (see <code>docs/windows-sign-in.md</code>).
            </p>
          )}
          {c.problem && (
            <p className="error" role="alert">
              {c.problem}
            </p>
          )}
          <div className="row-gap">
            <label className="btn2">
              {hasKeytab ? "Replace keytab…" : "Upload keytab…"}
              <input
                ref={file}
                type="file"
                accept=".keytab"
                hidden
                aria-label={hasKeytab ? "Replace keytab" : "Upload keytab"}
                disabled={upload.isPending}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload.mutate(f);
                }}
              />
            </label>
            {hasKeytab && (
              <button
                className="btn2"
                type="button"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm("Remove the keytab? Windows sign-in turns off for everyone.")) remove.mutate();
                }}
              >
                Remove keytab
              </button>
            )}
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            Or drag the .keytab file onto this box. It's stored on the server where only the service can read it, and can't be downloaded again.
          </p>
        </section>

        <section className="card stack" style={{ margin: 0 }}>
          <div className="lbl">Sign-in page</div>
          <label className="inline">
            <input
              type="checkbox"
              checked={c.enabled}
              disabled={!hasKeytab || save.isPending}
              onChange={(e) => save.mutate({ enabled: e.target.checked, silent: c.silent })}
            />{" "}
            Offer “Sign in with your Windows account”
          </label>
          <label className="inline">
            <input
              type="checkbox"
              checked={c.silent}
              disabled={!c.enabled || save.isPending}
              onChange={(e) => save.mutate({ enabled: c.enabled, silent: e.target.checked })}
            />{" "}
            Try it straight away, without a click
          </label>
          <p className="muted small" style={{ margin: 0 }}>
            Turn on “straight away” once a GPO puts the portal’s address in the Local Intranet zone. Without it, browsers may ask for a username and
            password instead.
          </p>
        </section>
        {error && (
          <p className="error" role="alert">
            {error.message}
          </p>
        )}
      </div>
    </>
  );
}
