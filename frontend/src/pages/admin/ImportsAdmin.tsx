// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router";
import { api, type ImportReport, type Me } from "../../api/client";

const LABELS: Record<string,string> = {tickets_created:"Tickets to create",tickets_updated:"Existing tickets to update",people_created_inactive:"Inactive historical accounts to create",people_matched:"Existing people matched",comments_created:"Comments to create",comments_updated:"Imported comments to update",attachments_created:"Files to import",attachments_existing:"Files already imported",work_created:"Internal work notes to create",work_existing:"Work already imported",time_entries_created:"Time entries to create"};

export function ImportsAdmin({me}:{me:Me}) {
  const client=useQueryClient();
  const [file,setFile]=useState<File|null>(null);
  const [queue,setQueue]=useState("");
  const [review,setReview]=useState<ImportReport|null>(null);
  const [result,setResult]=useState<ImportReport|null>(null);
  const [confirm,setConfirm]=useState(false);
  const [error,setError]=useState("");
  const picker=useRef<HTMLInputElement>(null);
  const queues=useQuery({queryKey:["queues"],queryFn:api.queues,enabled:me.role==="admin"});
  const preview=useMutation({mutationFn:()=>api.imports.kace(file!,Number(queue)),onSuccess:data=>{setReview(data);setConfirm(false);}});
  const apply=useMutation({mutationFn:()=>api.imports.kace(file!,Number(queue),review!.fingerprint),onSuccess:data=>{
    setResult(data);setReview(null);setFile(null);setConfirm(false);
    if(picker.current)picker.current.value="";
    client.invalidateQueries({predicate:q=>["tickets","views","users"].includes(String(q.queryKey[0]))});
  }});
  const busy=preview.isPending||apply.isPending;
  function changed(){setReview(null);setResult(null);setConfirm(false);setError("");preview.reset();apply.reset();}
  if(me.role!=="admin")return <p>Imports are managed by admins.</p>;
  return <div className="stack">
    <div className="page-head"><h1>Imports</h1></div>
    <section className="card stack" aria-label="KACE ticket import">
      <p className="workspace-area">DATA MIGRATION / KACE SMA</p>
      <h2>Bring your ticket history into Layer 8</h2>
      <p>Import a bundle made by the KACE export tool. Preview first, then apply the reviewed file to an existing queue. No emails, routing rules or surveys run, and old SLA timers are not restarted.</p>
      <p className="notice">KACE SMA 15.1 support is tested with synthetic fixtures. Live appliance validation is pending. Start with a small trial containing private notes and attachments.</p>
      <details><summary>Prepare a KACE export</summary><div className="stack">
        <p>On a machine that can reach KACE, run <code>tools/kace-export/kace-export.py</code> from the matching Layer 8 source. Use a read-only administrator for the required organization. Credentials and MFA are entered locally, never uploaded here.</p>
        <pre className="code">python kace-export.py --url https://kace.corp.example --organization Default --user migration --timezone America/Los_Angeles --limit 10 --out trial.zip</pre>
        <p>Export up to 1,000 tickets per batch with <code>--limit</code> and <code>--offset</code>. Use <code>--mfa</code> when required and <code>--ca-file</code> for a trusted private CA. The guide is <code>docs/kace-import.md</code> in the source.</p>
      </div></details>
      <label className="field">Target queue<select value={queue} disabled={busy} onChange={event=>{changed();setQueue(event.target.value);}}><option value="">Choose a queue</option>{queues.data?.filter(q=>q.is_active).map(q=><option key={q.id} value={q.id}>{q.name}</option>)}</select></label>
      {queues.error&&<p role="alert" className="error">{queues.error.message} <button type="button" onClick={()=>queues.refetch()}>Retry queues</button></p>}
      <p className="muted small">All tickets in this bundle go to this queue. The original KACE queue stays in imported details. For separate teams, use separate batches/target queues. Configure access under <Link to="/settings/queues">Queues</Link> before importing.</p>
      <label className="field">KACE export bundle (.zip)<input ref={picker} type="file" accept=".zip,application/zip" disabled={busy} onChange={event=>{
        changed();const next=event.target.files?.[0]??null;
        if(next&&next.size>64*1024*1024){setFile(null);setError("Bundles are limited to 64 MiB. Export a smaller batch.");}else setFile(next);
      }}/></label>
      <p className="muted small">64 MiB upload, 256 MiB expanded, 25 MiB per file. Preview saves no ticket records or attachment files. Reimport updates records from the same KACE appliance/organization; new Layer 8 comments stay intact.</p>
      <div><button className="btn2" type="button" disabled={busy||!file||!queue||!!result} onClick={()=>{preview.reset();setReview(null);setConfirm(false);preview.mutate();}}>{preview.isPending?"Checking bundle…":"Preview import"}</button></div>
      {(error||preview.error||apply.error)&&<p className="error" role="alert">{error||preview.error?.message||apply.error?.message}</p>}
      {apply.isPending&&<p role="status">Importing the reviewed bundle. Keep this page open.</p>}
    </section>
    {(review||result)&&<section className="card stack" aria-label={result?"Import result":"Import preview"}>
      <h2>{result?"Import complete":"Review import"}</h2>
      <p>{result?"Applied to":"Target queue:"} <strong>{(result??review)!.queue}</strong>{result?". No notifications were sent.":". Nothing has been imported yet."}</p>
      <dl className="import-counts">{Object.entries((result??review)!.counts).map(([key,value])=><div key={key}><dt>{result?(LABELS[key]??key.replaceAll("_"," ")).replaceAll(" to create","").replaceAll(" to update","").replaceAll(" to import",""):LABELS[key]??key.replaceAll("_"," ")}</dt><dd>{value}</dd></div>)}</dl>
      {!!(result??review)!.warnings.length&&<div className="notice"><h3>Items to review</h3><ul>{(result??review)!.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul></div>}
      {review&&<>
        <p>Applying updates previously imported fields and moves those tickets to the selected queue. Original IDs prevent duplicate tickets, comments and files. New historical accounts are inactive; existing users keep their permissions.</p>
        <label className="inline"><input type="checkbox" checked={confirm} disabled={busy} onChange={event=>setConfirm(event.target.checked)}/>I reviewed this preview and have a current backup. Apply this file to the selected queue.</label>
        <div><button className="btn" type="button" disabled={!confirm||busy} onClick={()=>apply.mutate()}>Apply reviewed import</button> <Link to="/settings/backup">Backups</Link></div>
      </>}
      {result&&<p><Link to="/">Open ticket queue</Link> · Select another file above for the next batch.</p>}
    </section>}
  </div>;
}
