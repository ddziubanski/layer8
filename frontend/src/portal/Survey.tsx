import { WorkspaceTitle } from "../components/WorkspaceTitle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError, api, type Survey, type SurveyAnswer } from "../api/client";
import { Brand, useBranding } from "../branding";

/** 1 to 5 (one click answers), then an optional comment. */
export function SurveyForm({ survey, onAnswer, pending, error }: { survey: Survey; onAnswer: (a: SurveyAnswer) => void; pending: boolean; error: Error | null }) {
  const [comment, setComment] = useState(survey.comment);
  const [sent, setSent] = useState(false);
  const ratings = Object.entries(survey.ratings)
    .map(([n, label]) => [Number(n), label] as const)
    .sort((a, b) => b[0] - a[0]);
  if (survey.expired && survey.rating === null) return <p className="muted">This survey has closed. Thank you anyway!</p>;
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p style={{ margin: 0 }}>
        <strong>{survey.question}</strong>
      </p>
      <div className="row-gap" role="radiogroup" aria-label="Your rating">
        {ratings.map(([n, label]) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={survey.rating === n}
            className={survey.rating === n ? "btn" : "btn2"}
            disabled={pending || survey.expired}
            onClick={() => onAnswer({ rating: n, comment })}
          >
            {n} · {label}
          </button>
        ))}
      </div>
      {survey.rating !== null && (
        <form
          className="stack"
          style={{ gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            onAnswer({ rating: survey.rating!, comment });
            setSent(true);
          }}
        >
          <p className="small" role="status" style={{ margin: 0 }}>
            Thank you! {survey.rating <= 2 ? "Sorry it wasn't better: tell us what went wrong and we'll look at it." : "Anything you'd like to add?"}
          </p>
          <label className="field">
            Comment <span className="muted small">(optional)</span>
            <textarea rows={3} maxLength={4000} value={comment} disabled={survey.expired} onChange={(e) => (setComment(e.target.value), setSent(false))} />
          </label>
          <div className="row-gap">
            <button className="btn2" type="submit" disabled={pending || survey.expired || comment === survey.comment}>
              Send comment
            </button>
            {sent && comment === survey.comment && <span className="small">Sent.</span>}
          </div>
        </form>
      )}
      {error && (
        <p className="error" role="alert" style={{ margin: 0 }}>
          {error.message}
        </p>
      )}
    </div>
  );
}

/** The page the emailed links open: no sign-in, the link is the key. ?r=N answers straight away. */
export function SurveyPage() {
  const brand = useBranding();
  const token = decodeURIComponent(window.location.pathname.replace(/^.*\/survey\//, "").replace(/\/$/, ""));
  const given = Number(new URLSearchParams(window.location.search).get("r"));
  const queryClient = useQueryClient();
  const key = ["survey", token];
  const survey = useQuery({ queryKey: key, queryFn: () => api.surveys.byLink(token), retry: false });
  const answer = useMutation({ mutationFn: (a: SurveyAnswer) => api.surveys.answerByLink(token, a), onSuccess: (s) => queryClient.setQueryData(key, s) });
  // One click in the email is enough.
  useEffect(() => {
    if (survey.data && given >= 1 && given <= 5 && survey.data.rating === null && !survey.data.expired && answer.isIdle) answer.mutate({ rating: given, comment: "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [survey.data]);

  return (
    <div className="portal">
      <header className="topbar">
        <Brand b={brand} />
        <span className="portal-topbar-context">Service feedback</span>
      </header>
      <main className="portal-main stack" style={{ maxWidth: 720 }}>
        <WorkspaceTitle title="How did we do?" area="SERVICE FEEDBACK" description="Tell the service team about your experience with this request." />
        {survey.error && <p className="error">{survey.error instanceof ApiError && survey.error.status === 404 ? "That survey link isn't valid." : survey.error.message}</p>}
        {survey.data && (
          <div className="card stack" style={{ margin: 0 }} aria-label="Survey" role="region">
            <span className="muted small">
              {survey.data.ticket_label} {survey.data.subject}
            </span>
            <SurveyForm survey={survey.data} onAnswer={(a) => answer.mutate(a)} pending={answer.isPending} error={answer.error} />
          </div>
        )}
      </main>
    </div>
  );
}

/** On a ticket in the portal, when it was surveyed. */
export function TicketSurvey({ ticketId }: { ticketId: number }) {
  const queryClient = useQueryClient();
  const key = ["portal-survey", ticketId];
  const survey = useQuery({ queryKey: key, queryFn: () => api.surveys.forMyTicket(ticketId), retry: false });
  const answer = useMutation({ mutationFn: (a: SurveyAnswer) => api.surveys.answerMyTicket(ticketId, a), onSuccess: (s) => queryClient.setQueryData(key, s) });
  if (!survey.data || (survey.data.expired && survey.data.rating === null)) return null;
  return (
    <div className="card stack" style={{ margin: 0 }} role="region" aria-label="How did we do?">
      <SurveyForm survey={survey.data} onAnswer={(a) => answer.mutate(a)} pending={answer.isPending} error={answer.error} />
    </div>
  );
}
