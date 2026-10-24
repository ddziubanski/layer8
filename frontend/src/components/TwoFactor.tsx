// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useEffect, useState } from "react";

import { api, type Me } from "../api/client";
import { backgroundStyle, useBranding } from "../branding";
import { ME_KEY } from "../queryClient";

/** Turning on two-factor sign-in: scan the QR code (or type the key), confirm a code, keep the recovery codes. */
export function TwoFactorSetup({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const setup = useMutation({ mutationFn: api.twoFactor.setup });
  const [qr, setQr] = useState("");
  const [code, setCode] = useState("");
  const confirm = useMutation({ mutationFn: () => api.twoFactor.confirm(code) });
  useEffect(() => {
    if (setup.data) void QRCode.toDataURL(setup.data.uri, { margin: 1, width: 200 }).then(setQr);
  }, [setup.data]);

  if (confirm.data) {
    return (
      <div className="stack" style={{ gap: 8 }}>
        <strong>Two-factor sign-in is on.</strong>
        <p style={{ margin: 0 }}>Keep these recovery codes somewhere safe, apart from your phone. Each one signs you in once if you lose the app. They won&apos;t be shown again.</p>
        <ul className="recovery-codes mono" aria-label="Recovery codes">
          {confirm.data.recovery_codes.map((c) => <li key={c}>{c}</li>)}
        </ul>
        <div>
          <button className="btn" type="button" onClick={() => { void queryClient.invalidateQueries({ queryKey: ME_KEY }); onDone(); }}>
            I&apos;ve saved them
          </button>
        </div>
      </div>
    );
  }
  if (!setup.data) {
    return (
      <div className="stack" style={{ gap: 8 }}>
        <p style={{ margin: 0 }}>Use an authenticator app on your phone, such as Microsoft Authenticator or Google Authenticator. After your password, sign-in asks for the code it shows.</p>
        <div>
          <button className="btn" type="button" disabled={setup.isPending} onClick={() => setup.mutate()}>Set up two-factor sign-in</button>
        </div>
        {setup.error && <p className="error" role="alert">{setup.error.message}</p>}
      </div>
    );
  }
  return (
    <form className="stack" style={{ gap: 8 }} onSubmit={(e) => (e.preventDefault(), confirm.mutate())}>
      <p style={{ margin: 0 }}>Scan this with your authenticator app, then type the code it shows.</p>
      {qr && <img src={qr} width={200} height={200} alt="QR code for your authenticator app" />}
      <p className="small muted" style={{ margin: 0 }}>
        Can&apos;t scan? Enter this key: <span className="mono">{setup.data.secret.replace(/(.{4})/g, "$1 ").trim()}</span>
      </p>
      <label className="field" style={{ maxWidth: 220 }}>
        Code from the app
        <input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required />
      </label>
      {confirm.error && <p className="error" role="alert">{confirm.error.message}</p>}
      <div>
        <button className="btn" type="submit" disabled={confirm.isPending || code.trim().length < 6}>Turn it on</button>
      </div>
    </form>
  );
}

/** My preferences: two-factor sign-in on or off. */
export function TwoFactorSection() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["two-factor"], queryFn: api.twoFactor.status });
  const [setting, setSetting] = useState(false);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const off = useMutation({
    mutationFn: () => api.twoFactor.off(password, code),
    onSuccess: () => { setPassword(""); setCode(""); void queryClient.invalidateQueries({ queryKey: ["two-factor"] }); },
  });
  const s = status.data;
  return (
    <section className="card stack" aria-label="Two-factor sign-in">
      <h2 style={{ margin: 0 }}>Two-factor sign-in</h2>
      {s?.enabled && (
        <>
          <p style={{ margin: 0 }}>On: sign-in asks for a code from your authenticator app. {s.recovery_codes_left} recovery code{s.recovery_codes_left === 1 ? "" : "s"} left.</p>
          <form className="row-gap" onSubmit={(e) => (e.preventDefault(), off.mutate())}>
            <label className="field">
              Password
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            <label className="field" style={{ width: 160 }}>
              Code
              <input autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required />
            </label>
            <button className="btn2" type="submit" disabled={off.isPending}>Turn off</button>
          </form>
          {off.error && <p className="error" role="alert">{off.error.message}</p>}
        </>
      )}
      {s && !s.enabled && (setting ? (
        <TwoFactorSetup onDone={() => { setSetting(false); void queryClient.invalidateQueries({ queryKey: ["two-factor"] }); }} />
      ) : (
        <>
          <p style={{ margin: 0 }}>Off. Turn it on so a stolen password isn&apos;t enough to sign in as you.</p>
          <div><button className="btn2" type="button" onClick={() => setSetting(true)}>Turn on</button></div>
        </>
      ))}
    </section>
  );
}

/** Settings > Sign-in requires it of admins with a local password: set it up before anything else. */
export function TwoFactorFirst({ me }: { me: Me }) {
  const brand = useBranding();
  const queryClient = useQueryClient();
  return (
    <div className="login" style={backgroundStyle(brand, "sign-in")}>
      <div className="card login-card stack">
        <p className="workspace-area">ACCOUNT SETUP</p>
        <h1>Set up two-factor sign-in</h1>
        <p className="muted" style={{ margin: 0 }}>{me.display_name}, this service desk requires two-factor sign-in for admins who sign in with a password.</p>
        <TwoFactorSetup onDone={() => void queryClient.invalidateQueries({ queryKey: ME_KEY })} />
      </div>
    </div>
  );
}
