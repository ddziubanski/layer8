// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, type LibraryReference } from "../../api/client";

/** Explicit, user-scoped search results live only in this mounted ticket tool. */
export function SharePointLibrary({ ticketId }: { ticketId: number }) {
  const client = useQueryClient();
  const status = useQuery({ queryKey: ["library-status"], queryFn: api.library.status, refetchInterval: 30000, gcTime: 0 });
  const [query, setQuery] = useState("");
  const reviewPanel = useRef<HTMLElement>(null);
  const [review, setReview] = useState<LibraryReference | null>(null);
  const search = useMutation({ mutationFn: (text: string) => api.library.search(ticketId, text), onSuccess: () => setReview(null) });
  const link = useMutation({ mutationFn: (reference: LibraryReference) => api.library.link(ticketId, reference.drive_id, reference.item_id),
    onSuccess: () => { setReview(null); void client.invalidateQueries({ queryKey: ["ticket", ticketId] }); } });
  useEffect(() => { search.reset(); link.reset(); setReview(null); }, [status.data?.site_url, status.data?.connected, status.data?.enabled]);
  useEffect(() => { if (review) { reviewPanel.current?.focus(); reviewPanel.current?.scrollIntoView?.({ block: "nearest" }); } }, [review]);
  const ready = status.data?.enabled && status.data.connected;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    link.reset(); setReview(null); search.reset(); search.mutate(query.trim());
  };
  if (status.data && !status.data.enabled) return null;
  return <section className="card stack" aria-label="SharePoint Library" style={{ gap: 10 }}>
    <h3 style={{ margin: 0 }}>SharePoint runbooks and FAQs</h3>
    {status.error && <p className="error" role="alert">Could not read Library setup. Your local knowledge base is still available.</p>}
    {status.data?.enabled && !status.data.connected && <p className="muted">Connect or reconnect your Microsoft account in <Link to="/preferences">My preferences</Link> after an administrator enables Library access. Your SharePoint permissions still apply.</p>}
    {ready && <>
      <p className="muted small">Search IT runbooks and FAQ pages on <a href={status.data!.site_url!} target="_blank" rel="noreferrer">your approved SharePoint site</a>. Only the words you submit below are sent to Microsoft. No Copilot licence is needed for this search.</p>
      <form onSubmit={submit} className="stack">
        <label className="field">Find a runbook or FAQ
          <input value={query} maxLength={200} disabled={link.isPending} onChange={event => setQuery(event.target.value)} placeholder="VPN setup, new laptop, password reset…" />
        </label>
        <button className="btn2" type="submit" disabled={!query.trim() || search.isPending || link.isPending}>{search.isPending ? "Searching SharePoint…" : "Search SharePoint"}</button>
      </form>
      {search.error && <p className="error" role="alert">{search.error.message}</p>}
      {search.isSuccess && search.data.length === 0 && <p className="muted" role="status">No accessible pages or documents matched. Try different words.</p>}
      <div className="stack" aria-label="Library results">
        {search.data?.map(item => <article className="library-result stack" key={`${item.drive_id}:${item.item_id}`}>
          <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a>
          {item.summary && <p className="small" style={{ margin: 0 }}>{item.summary}</p>}
          <button type="button" className="linkish small" disabled={link.isPending} onClick={() => { link.reset(); setReview(item); }}>Link reference to ticket</button>
        </article>)}
      </div>
      {review && <section ref={reviewPanel} tabIndex={-1} className="notice stack" aria-label="Review Library reference">
        <strong>{review.title}</strong><a href={review.url} target="_blank" rel="noreferrer">{review.url}</a>
        <p className="small">This title and link will be saved as an internal note visible to technicians with access to this ticket. The requester receives no reply or email. SharePoint checks access when someone opens the link.</p>
        <div className="row-gap"><button className="btn" type="button" disabled={link.isPending} onClick={() => link.mutate(review)}>Confirm internal reference</button><button className="btn2" type="button" disabled={link.isPending} onClick={() => setReview(null)}>Cancel</button></div>
      </section>}
      {link.error && <p className="error" role="alert">{link.error.message}</p>}
      {link.isSuccess && <p role="status">Library reference saved as an internal note.</p>}
    </>}
  </section>;
}
