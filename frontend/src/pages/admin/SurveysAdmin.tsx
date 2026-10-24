// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, type User } from "../../api/client";
import { when } from "../../format";
import { SettingsForm } from "./SettingsForm";

const LABEL: Record<number, string> = { 1: "Very unhappy", 2: "Unhappy", 3: "OK", 4: "Happy", 5: "Very happy" };

/** Satisfaction surveys: how many solved tickets ask, and what people said. */
export function SurveysAdmin({ me }: { me: User }) {
  const [days, setDays] = useState(90);
  const results = useQuery({ queryKey: ["surveys", days], queryFn: () => api.surveys.results(days) });
  const r = results.data;
  const top = r ? Math.max(1, ...Object.values(r.by_rating)) : 1;

  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>Surveys</h1>
      <SettingsForm
        me={me}
        section="surveys"
        title="Satisfaction surveys"
        intro="When a ticket is first resolved or closed, this share of them (picked at random) emails the requester one question with 1 to 5 links; one click answers. The link needs no sign-in. Requesters can also answer on the ticket in the portal."
        embedded
      />
      <section className="card stack" aria-label="Survey results" style={{ margin: 0 }}>
        <div className="row-gap">
          <div className="lbl spacer" style={{ margin: 0 }}>
            Results
          </div>
          <label className="inline small">
            Last{" "}
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[30, 90, 365].map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </label>
        </div>
        {r && (
          <>
            <p style={{ margin: 0 }}>
              <strong>{r.sent}</strong> sent · <strong>{r.answered}</strong> answered{r.sent > 0 && ` (${Math.round((100 * r.answered) / r.sent)}%)`}
              {r.average !== null && (
                <>
                  {" "}
                  · average <strong>{r.average.toFixed(1)}</strong> / 5
                </>
              )}
            </p>
            <div className="stack" style={{ gap: 4 }} aria-label="Answers by rating">
              {[5, 4, 3, 2, 1].map((n) => (
                <div key={n} className="row-gap small" style={{ gap: 8 }}>
                  <span style={{ width: 120 }}>
                    {n} · {LABEL[n]}
                  </span>
                  <span className="bar" style={{ display: "inline-block", height: 10, borderRadius: 4, background: "var(--brand)", width: `${(200 * (r.by_rating[n] ?? 0)) / top}px` }} />
                  <span>{r.by_rating[n] ?? 0}</span>
                </div>
              ))}
            </div>
            {r.recent.some((s) => s.rating !== null) && (
              <table className="grid">
                <thead>
                  <tr>
                    <th>Ticket</th>
                    <th>Rating</th>
                    <th>Comment</th>
                    <th>Handled by</th>
                    <th>Answered</th>
                  </tr>
                </thead>
                <tbody>
                  {r.recent
                    .filter((s) => s.rating !== null)
                    .map((s) => (
                      <tr key={s.ticket_id}>
                        <td>
                          <Link to={`/tickets/${s.ticket_id}`}>
                            {s.ticket_label} {s.subject}
                          </Link>
                          <div className="muted small">{s.requester}</div>
                        </td>
                        <td>
                          <span className={`chip ${s.rating! <= 2 ? "r" : s.rating === 3 ? "a" : "g"}`}>{s.rating}</span>
                        </td>
                        <td className="small">{s.comment}</td>
                        <td className="small">{s.assignee ?? "–"}</td>
                        <td className="small">{s.answered_at && when(s.answered_at)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
            {r.sent === 0 && <p className="muted small" style={{ margin: 0 }}>No surveys sent in this period.</p>}
          </>
        )}
      </section>
    </div>
  );
}
