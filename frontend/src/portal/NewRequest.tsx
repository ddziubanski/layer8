// SPDX-License-Identifier: AGPL-3.0-or-later
import { WorkspaceTitle } from "../components/WorkspaceTitle";
import { TeamsAndHours } from "./QueueStatus";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { api, type PortalTicket } from "../api/client";
import { useDropFiles } from "../components/useDropFiles";
import { fileSize } from "../format";
import { usePortalPath } from "./base";
import { HelpSuggestions } from "./Help";

// Choosing this in "What is the issue with?" opens a free-text box.
const OTHER = "__other__";
const MAX_TILES = 12;
const IMPACTS = [
  { value: "single", label: "Just me" },
  { value: "many", label: "Several people or my team" },
  { value: "business", label: "The business: work has stopped" },
] as const;
const URGENCIES = [
  { value: "low", label: "It can wait" },
  { value: "medium", label: "Needed soon" },
  { value: "high", label: "Urgent: I'm blocked" },
] as const;
type ImpactV = (typeof IMPACTS)[number]["value"];
type UrgencyV = (typeof URGENCIES)[number]["value"];

function Tiles<T extends string>({ id, label, n, options, value, onChange }: { id: string; label: string; n: number; options: readonly { value: T; label: string }[]; value: T | ""; onChange: (v: T) => void }) {
  return (
    <div className="step">
      <div className="step-q">
        <span className="step-n" aria-hidden="true">
          {n}
        </span>
        <span id={id}>{label}</span>
      </div>
      <div className="option-tiles" role="radiogroup" aria-labelledby={id}>
        {options.map((o) => (
          <label key={o.value} className={`option-tile${value === o.value ? " on" : ""}`}>
            <input type="radio" name={id} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
            {o.label}
          </label>
        ))}
      </div>
    </div>
  );
}

/** Guided request: 1. which team, 2. what the issue is with (the team's options or free text), 3. details. */
export function NewRequest() {
  const p = usePortalPath();
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const queues = useQuery({ queryKey: ["portal", "queues"], queryFn: api.portal.queues });
  const queueId = params.get("queue") ? Number(params.get("queue")) : null;
  const queue = queues.data?.find((q) => q.id === queueId) ?? null;
  const noQueues = queues.data !== undefined && queues.data.length === 0;
  const desk = useQuery({ queryKey: ["portal", "hours"], queryFn: api.portal.hours, enabled: noQueues });

  const [choice, setChoice] = useState("");
  const [impact, setImpact] = useState<ImpactV | "">("");
  const [urgency, setUrgency] = useState<UrgencyV | "">("");
  const [otherText, setOtherText] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  // "Which computer?": shown when the requester has devices assigned; one is picked for them.
  const myDevices = useQuery({ queryKey: ["portal", "devices"], queryFn: api.portal.devices });
  const [computer, setComputer] = useState<string | null>(null);
  const computerId = computer ?? (myDevices.data?.length === 1 ? String(myDevices.data[0].id) : "");
  const picker = useRef<HTMLInputElement>(null);
  const drop = useDropFiles((dropped) => setFiles((f) => [...f, ...dropped]));
  const [created, setCreated] = useState<{ ticket: PortalTicket; failed: string[] } | null>(null);

  const hasOptions = !!queue && queue.categories.length > 0;
  // Tiles are quicker to scan; a long list is easier as a dropdown.
  const tiles = hasOptions && queue!.categories.length <= MAX_TILES;
  const category = choice === OTHER ? otherText.trim() : choice;
  // "Tell us more" opens once the earlier questions are answered.
  const answered = noQueues || (!!queue && (!hasOptions || !!category));
  const ready = answered && !!impact && !!urgency;

  const submit = useMutation({
    mutationFn: async () => {
      const ticket = await api.portal.create({ queue_id: queue?.id ?? null, category: category || null, subject, description, impact: impact as ImpactV, urgency: urgency as UrgencyV, computer_id: computerId ? Number(computerId) : null });
      // The ticket exists now; a failed file shouldn't lose it.
      const failed: string[] = [];
      for (const file of files) {
        try {
          await api.upload(ticket.id, file);
        } catch (e) {
          failed.push(`${file.name}: ${(e as Error).message}`);
        }
      }
      return { ticket, failed };
    },
    onSuccess: (result) => {
      setCreated(result);
      queryClient.invalidateQueries({ queryKey: ["portal", "tickets"] });
    },
  });

  if (queues.isPending) return null;
  if (queues.error) return <p className="error">{queues.error.message}</p>;

  if (created) {
    return (
      <section className="card" style={{ maxWidth: 640 }}>
        <h1>Request sent</h1>
        <p>
          Your ticket number is <strong>#{created.ticket.id}</strong>. We'll email you when there's an update; reply to that email or use this site to add more.
        </p>
        {created.failed.length > 0 && (
          <p className="error" role="alert">
            Some files weren't attached: {created.failed.join("; ")}. You can add them from the ticket.
          </p>
        )}
        <div className="row-gap">
          <Link className="btn" to={p(`/tickets/${created.ticket.id}`)}>
            View ticket
          </Link>
          <Link className="btn2" to={p("/")} onClick={() => setCreated(null)}>
            New request
          </Link>
        </div>
      </section>
    );
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit.mutate();
  }

  const impactStep = noQueues ? 1 : hasOptions ? 3 : 2;
  const detailsStep = impactStep + 2;
  return (
    <form className="stack guided" style={{ maxWidth: 720 }} onSubmit={onSubmit}>
      <div className="page-intro">
        <WorkspaceTitle title="How can we help?" area="SELF SERVICE" />
        <p className="muted" style={{ margin: 0 }}>
          A few quick questions get your request to the right people.
        </p>
      </div>

      <TeamsAndHours queues={queues.data} desk={desk.data} />

      {!noQueues && (
        <div className="step">
          <div className="step-q">
            <span className="step-n" aria-hidden="true">
              1
            </span>
            <label htmlFor="q-team">Which team can help?</label>
          </div>
          <select
            id="q-team"
            required
            value={queue?.id ?? ""}
            onChange={(e) => {
              setParams({ queue: e.target.value });
              setChoice("");
              setOtherText("");
            }}
          >
            <option value="" disabled>
              Choose…
            </option>
            {queues.data.map((q) => (
              <option key={q.id} value={q.id}>
                {q.name}
              </option>
            ))}
          </select>
          {queue?.description && <p className="muted small step-hint">{queue.description}</p>}
          {queue && !queue.hours.open_now && queue.hours.message && (
            <p className="card note small hours-message" role="status" style={{ margin: "8px 0 0" }}>
              {queue.hours.message} You can still send your request now.
            </p>
          )}
        </div>
      )}

      {hasOptions && (
        <div className="step">
          <div className="step-q">
            <span className="step-n" aria-hidden="true">
              2
            </span>
            {tiles ? <span id="q-issue-label">What is the issue with?</span> : <label htmlFor="q-issue">What is the issue with?</label>}
          </div>
          {tiles ? (
            <div className="option-tiles" role="radiogroup" aria-labelledby="q-issue-label">
              {[...queue!.categories, OTHER].map((c) => (
                <label key={c} className={`option-tile${choice === c ? " on" : ""}`}>
                  <input type="radio" name="issue" value={c} checked={choice === c} onChange={() => setChoice(c)} />
                  {c === OTHER ? "Something else…" : c}
                </label>
              ))}
            </div>
          ) : (
            <select id="q-issue" required value={choice} onChange={(e) => setChoice(e.target.value)}>
              <option value="" disabled>
                Choose…
              </option>
              {queue!.categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
              <option value={OTHER}>Something else…</option>
            </select>
          )}
          {choice === OTHER && (
            <input
              aria-label="Describe what the issue is with"
              placeholder="In a few words, e.g. label printer, door badge"
              maxLength={100}
              required
              autoFocus
              value={otherText}
              onChange={(e) => setOtherText(e.target.value)}
              style={{ marginTop: 8 }}
            />
          )}
        </div>
      )}

      {answered && <Tiles id="q-impact" label="Who is affected?" n={impactStep} options={IMPACTS} value={impact} onChange={setImpact} />}
      {answered && impact && <Tiles id="q-urgency" label="How urgent is it?" n={impactStep + 1} options={URGENCIES} value={urgency} onChange={setUrgency} />}

      {ready && (
        <div className="step stack" style={{ gap: 12 }}>
          <span className="step-q">
            <span className="step-n" aria-hidden="true">
              {detailsStep}
            </span>
            Tell us more
          </span>
          <label className="field">
            Subject
            <input required maxLength={300} placeholder={category ? `${category}: …` : "A short summary"} value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <HelpSuggestions text={subject} category={category || undefined} />
          <label className="field">
            Details
            <textarea rows={7} placeholder="What's happening, where, and since when? Include any error messages." value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          {(myDevices.data?.length ?? 0) > 0 && (
            <label className="field">
              Which computer? <span className="muted small">(helps us connect to it)</span>
              <select value={computerId} onChange={(e) => setComputer(e.target.value)}>
                {myDevices.data!.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                    {d.os ? ` (${d.os})` : ""}
                  </option>
                ))}
                <option value="">Another one, or not about a computer</option>
              </select>
            </label>
          )}
          <div className={`dropzone${drop.dragging ? " dropping" : ""}`} {...drop.props}>
            <input
              ref={picker}
              type="file"
              multiple
              hidden
              data-testid="file-input"
              onChange={(e) => {
                setFiles([...files, ...Array.from(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
            <button type="button" className="btn2" onClick={() => picker.current?.click()}>
              Attach files
            </button>
            <span className="muted small"> or drag screenshots and files here</span>
            {files.length > 0 && (
              <ul className="files" aria-label="Files to attach" style={{ listStyle: "none", padding: 0 }}>
                {files.map((f, i) => (
                  <li key={`${f.name}-${i}`} className="chip n">
                    {f.name} {fileSize(f.size)}{" "}
                    <button type="button" className="linkish" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {submit.error && (
            <p className="error" role="alert">
              {submit.error.message}
            </p>
          )}
          <div>
            <button className="btn" type="submit" disabled={submit.isPending}>
              Send request
            </button>
          </div>
        </div>
      )}
    </form>
  );
}
