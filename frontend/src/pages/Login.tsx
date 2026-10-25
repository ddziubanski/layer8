// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { api, type Me } from "../api/client";
import { Brand, backgroundStyle, useBranding } from "../branding";
import { SourceNotice } from "../components/SourceNotice";
import { ME_KEY } from "../queryClient";

// The server's answer when the password was right and the authenticator code is next.
const TWO_FACTOR_NEEDED = "Enter the code from your authenticator app";

// Per-browser memory, so Windows sign-in is tried by itself only when it can help.
// Storage can be unavailable (private windows, blocked site data): then it's always tried.
const SKIP_SILENT = "helpdesk.windowsSignIn.skip"; // this tab: it failed, or they signed out
const TECHNICIAN = "helpdesk.technician"; // this browser: a technician signs in with a password here

function read(storage: () => Storage, key: string): boolean {
  try {
    return storage().getItem(key) === "1";
  } catch {
    return false;
  }
}

function remember(storage: () => Storage, key: string) {
  try {
    storage().setItem(key, "1");
  } catch {
    // Not remembered; harmless.
  }
}

/** After signing out, show the sign-in page instead of signing straight back in. */
export function skipSilentWindowsSignIn() {
  remember(() => sessionStorage, SKIP_SILENT);
}

/** console: a Windows (portal-only) sign-in asking for a password to reach the console. */
export function Login({ console: forConsole = false }: { console?: boolean }) {
  const queryClient = useQueryClient();
  const brand = useBranding();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  // A refused Microsoft sign-in comes back here with its reason.
  const [entraError] = useState(() => new URLSearchParams(window.location.search).get("signin_error"));
  const options = useQuery({ queryKey: ["sign-in-options"], queryFn: api.signInOptions, staleTime: Infinity, enabled: !forConsole });
  const windows = !forConsole && options.data?.windows_sign_in === true;

  function signedIn(user: Me) {
    if (forConsole) window.history.replaceState(null, "", "/");
    queryClient.setQueryData(ME_KEY, user);
  }

  const login = useMutation({
    mutationFn: () => api.login(email, password, needCode ? code : undefined),
    onError: (e) => {
      if (e.message === TWO_FACTOR_NEEDED) setNeedCode(true);
    },
    onSuccess: (user) => {
      if (user.role !== "requester") remember(() => localStorage, TECHNICIAN);
      signedIn(user);
    },
  });
  const windowsSignIn = useMutation({
    mutationFn: (_how: { silent: boolean }) => api.windowsSignIn(),
    onSuccess: signedIn,
    onError: () => skipSilentWindowsSignIn(),
  });

  // Try Windows sign-in once by itself, unless this browser belongs to a technician (they'd
  // land in the portal) or it already failed or they signed out in this tab.
  const tried = useRef(false);
  const silent = windows && options.data?.windows_sign_in_silent && !read(() => localStorage, TECHNICIAN) && !read(() => sessionStorage, SKIP_SILENT);
  useEffect(() => {
    if (silent && !tried.current) {
      tried.current = true;
      windowsSignIn.mutate({ silent: true });
    }
  }, [silent, windowsSignIn]);

  function submit(e: FormEvent) {
    e.preventDefault();
    login.mutate();
  }

  const silentAttempt = windowsSignIn.variables?.silent === true;
  if (silentAttempt && windowsSignIn.isPending) {
    return (
      <div className="login" style={backgroundStyle(brand, "sign-in")}>
        <div className="card login-card">
          <h1>
            <Brand b={brand} className="" />
          </h1>
          <p className="muted" role="status">
            Signing you in with your Windows account…
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="login" style={backgroundStyle(brand, "sign-in")}>
      <form className="card login-card" onSubmit={submit}>
        <p className="workspace-area">SERVICE DESK ACCESS</p>
        <h1>{forConsole ? "Sign in to the console" : <Brand b={brand} className="" />}</h1>
        {!forConsole && <p className="muted">Sign in once. Your access opens the helpdesk or self-service portal automatically.</p>}
        {forConsole && <p className="muted">The agent console needs your password, not just your Windows sign-in.</p>}
        {windows && (
          <>
            <button
              className="btn"
              type="button"
              disabled={windowsSignIn.isPending}
              onClick={() => windowsSignIn.mutate({ silent: false })}
            >
              Sign in with your Windows account
            </button>
            {windowsSignIn.error && !silentAttempt && (
              <p className="error" role="alert">
                {windowsSignIn.error.message}
              </p>
            )}
            <div className="or" aria-hidden="true">
              or
            </div>
          </>
        )}
        {!forConsole && options.data?.entra_sign_in && (
          <>
            <a className={windows ? "btn2" : "btn"} href="/api/auth/entra/start" style={{ textAlign: "center", textDecoration: "none" }}>
              Sign in with Microsoft
            </a>
            <div className="or" aria-hidden="true">
              or
            </div>
          </>
        )}
        {entraError && <p className="error" role="alert">{entraError}</p>}
        <label className="field">
          Email or username
          <input type="text" autoComplete="username" autoCapitalize="none" spellCheck={false} required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {needCode && (
          <label className="field">
            Code from your authenticator app (or a recovery code)
            <input inputMode="numeric" autoComplete="one-time-code" autoFocus required value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
        )}
        {login.error && !(login.error.message === TWO_FACTOR_NEEDED && !code) && (
          <p className="error" role="alert">
            {login.error.message}
          </p>
        )}
        <button className={windows ? "btn2" : "btn"} type="submit" disabled={login.isPending}>
          Sign in
        </button>
        {forConsole && (
          <a className="muted" href="/">
            Back to the portal
          </a>
        )}
        {brand.sign_in_message && !forConsole && <p className="muted small sign-in-message">{brand.sign_in_message}</p>}
      </form>
      <SourceNotice className="source-notice login-source" />
    </div>
  );
}

/** After signing in with a password someone else knows (the first-run admin/admin): change it first. */
export function ChangePasswordFirst({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const brand = useBranding();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const change = useMutation({
    mutationFn: () => {
      if (next !== repeat) throw new Error("The new passwords don't match");
      return api.changePassword(current, next);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ME_KEY }),
  });
  return (
    <div className="login" style={backgroundStyle(brand, "sign-in")}>
      <form
        className="card login-card"
        onSubmit={(e) => {
          e.preventDefault();
          change.mutate();
        }}
      >
        <p className="workspace-area">ACCOUNT SETUP</p>
        <h1>Choose a new password</h1>
        <p className="muted" style={{ margin: 0 }}>
          You signed in as {me.display_name} with the first-run password. Choose your own before going on: at least 12 characters.
        </p>
        <label className="field">
          Current password
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="field">
          New password
          <input type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label className="field">
          New password again
          <input type="password" autoComplete="new-password" required minLength={12} value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </label>
        {change.error && (
          <p className="error" role="alert">
            {change.error.message}
          </p>
        )}
        <button className="btn" type="submit" disabled={change.isPending}>
          Save password
        </button>
      </form>
    </div>
  );
}
