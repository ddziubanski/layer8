import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type User } from "../../api/client";
import { SettingsForm } from "./SettingsForm";

/** Email intake and notifications through Microsoft Graph (Exchange Online). */
export function EmailAdmin({ me }: { me: User }) {
  const test = useMutation({ mutationFn: api.settings.testEmail });
  const check = useMutation({ mutationFn: api.settings.checkMailboxNow });
  if (me.role !== "admin") return <p>Email is managed by admins.</p>;
  return (
    <>
      <div className="page-head">
        <h1>Email</h1>
      </div>
      <div className="stack">
        <section className="card stack" style={{ margin: 0 }}>
          <div className="lbl">Connection</div>
          <p className="muted" style={{ margin: 0 }}>
            Tickets emailed to the service desk mailbox are read through Microsoft Graph, and replies and notices are sent from it. The app
            registration needs Mail.ReadWrite and Mail.Send, limited to that mailbox (see <code>docs/email-setup.md</code>).
          </p>
          <div className="row-gap">
            <button className="btn2" type="button" disabled={test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? "Testing…" : "Test connection"}
            </button>
            <span className="muted small">Signs in and reads the Inbox. Nothing is sent.</span>
          </div>
          {test.data && (
            <p className="small" role="status">
              Connected to {test.data.mailbox}: {test.data.inbox_items} message(s) in the Inbox, {test.data.unread} unread.
            </p>
          )}
          {test.error && (
            <p className="error" role="alert">
              {test.error.message}
            </p>
          )}
        </section>

        <section className="card stack" style={{ margin: 0 }}>
          <div className="lbl">Check mailbox now</div>
          <div className="row-gap">
            <button className="btn2" type="button" disabled={check.isPending} onClick={() => check.mutate()}>
              {check.isPending ? "Checking…" : "Check mailbox now"}
            </button>
            <span className="muted small">Reads the Inbox into tickets and sends queued email now, instead of waiting for the next check.</span>
          </div>
          {check.data && (
            <div role="status" className="stack" style={{ gap: 6 }}>
              <p className="small" style={{ margin: 0 }}>
                {check.data.processed} made into tickets or replies, {check.data.rejected} rejected, {check.data.failed} failed; {check.data.sent} email(s) sent.
              </p>
              {check.data.items.length > 0 && (
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                  {check.data.items.map((item, i) => (
                    <li key={i}>
                      {item.status === "processed" && item.ticket_id ? (
                        <>
                          Ticket <Link to={`/tickets/${item.ticket_id}`}>#{item.ticket_id}</Link>
                        </>
                      ) : (
                        <span className={item.status === "failed" ? "error" : "muted"}>
                          {item.status === "failed" ? "Failed" : "Rejected"}: {item.reason}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {check.error && (
            <p className="error" role="alert">
              {check.error.message}
            </p>
          )}
        </section>
        <SettingsForm me={me} section="email" title="Mailbox settings" intro="Save, then use Test connection above." embedded onSaved={() => test.reset()} />
      </div>
    </>
  );
}
