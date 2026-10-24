// SPDX-License-Identifier: AGPL-3.0-or-later
import type { User } from "../../api/client";
import { SettingsForm } from "./SettingsForm";

// The same sample the API's preview uses (POST /api/settings/requester-emails/preview).
const SAMPLE: Record<string, string> = {
  first_name: "Ana", name: "Ana Silva", ticket: "#1042", subject: "Laptop won't join Staff Wi-Fi",
  technician: "J. Park", queue: "Service desk", portal: "https://helpdesk.example.com/tickets/1042",
};

export function fillSample(text: string): string {
  return text.replace(/\{(first_name|name|ticket|subject|technician|queue|portal)\}/g, (_, key: string) => SAMPLE[key]);
}

const TEMPLATES: [string, string, string | null][] = [
  ["email_ack_template", "We received your request", null],
  ["email_resolved_template", "Resolved", "email_resolved_enabled"],
  ["email_assigned_template", "Assigned", "email_assigned_enabled"],
];

/** Settings > Requester emails: the wording of emails to requesters, with a preview as you type. */
export function RequesterEmailsAdmin({ me }: { me: User }) {
  return (
    <SettingsForm
      me={me}
      section="requester_emails"
      title="Requester emails"
      intro="What requesters get by email. Technicians' replies are sent as they write them; these are the automatic ones. The ticket tag goes in the subject, so replies land on the ticket."
      preview={(values) => (
        <div className="stack" aria-label="Preview" style={{ gap: 8 }}>
          <div className="lbl" style={{ margin: 0 }}>Preview, with a sample ticket</div>
          {TEMPLATES.map(([field, label, toggle]) => (
            <div key={field} className="card" style={{ padding: 10 }}>
              <strong className="small">{label}{toggle && !values[toggle] ? " (off)" : ""}</strong>
              <pre className="email-preview">{fillSample(String(values[field] ?? ""))}</pre>
            </div>
          ))}
        </div>
      )}
    />
  );
}
