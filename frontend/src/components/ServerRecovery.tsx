// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Me, type UpdateStatus } from "../api/client";
import { ME_KEY } from "../queryClient";

declare const __LAYER8_VERSION__: string;
export const applicationReload = { run: () => window.location.reload() };
export const transientFailure = (error: unknown) => !!error && (!(error instanceof ApiError) || error.status >= 500);

/** Read actual API JSON with no cache; an HTML login/maintenance page never means ready. */
export async function serverReady(signal?: AbortSignal): Promise<string> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const deadline = setTimeout(abort, 8000);
  const read = async (path: string) => {
    const response = await fetch(path, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new ApiError(response.status, "Services are not ready yet");
    return response.json();
  };
  try {
    if ((await read("/api/health"))?.status !== "ok") throw new Error("API is not ready");
    const about = await read("/api/about");
    if (typeof about?.version !== "string" || !about.version.trim()) throw new Error("Installed version is not available");
    return about.version;
  } finally {
    clearTimeout(deadline);
    signal?.removeEventListener("abort", abort);
  }
}

/** A conservative guard: never force a fresh bundle over a changed, still-mounted form control. */
export function useServerRecovery(error: Error | null, me: Me | undefined, retryIdentity: () => unknown) {
  const client = useQueryClient();
  const [cycle, setCycle] = useState<number | null>(null);
  const sequence = useRef(0);
  const [freshVersion, setFreshVersion] = useState<string | null>(null);
  const dirty = useRef(new Set<Element>());
  const watched = useRef(false);
  const updates = useQuery<UpdateStatus>({queryKey:["release-updates"],queryFn:api.updates.status,enabled:false});
  const progressUrl = updates.data?.progress_url;
  const progress = useQuery<Record<string, unknown>>({queryKey:["update-progress",progressUrl],queryFn:() => api.updates.progress(progressUrl!),enabled:false});
  const admin = me?.role === "admin" && !me.portal_only;
  const phase = String((progress.data ?? updates.data?.operation)?.phase ?? "");
  const updating = admin && !!updates.data?.busy;
  useEffect(() => { if (updating) watched.current = true; }, [updating, progressUrl]);
  useEffect(() => {
    if (transientFailure(error) && cycle === null) setCycle(++sequence.current);
  }, [error, cycle]);
  useEffect(() => {
    if (admin && watched.current && ["succeeded", "rolled_back"].includes(phase)) {
      watched.current = false;
      setCycle(current => current ?? ++sequence.current);
    }
  }, [admin, phase, progressUrl]);
  useEffect(() => {
    const changed = (event: Event) => {
      for (const element of dirty.current) if (!element.isConnected) dirty.current.delete(element);
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement || (event.target instanceof HTMLElement && event.target.isContentEditable)) dirty.current.add(event.target);
    };
    document.addEventListener("input", changed, true); document.addEventListener("change", changed, true);
    return () => { document.removeEventListener("input", changed, true); document.removeEventListener("change", changed, true); };
  }, []);
  const readiness = useQuery({
    queryKey:["restart-readiness",cycle], enabled:cycle !== null, retry:false, refetchInterval:5000,
    refetchIntervalInBackground:true, refetchOnWindowFocus:true,
    queryFn: async ({signal}) => {
      const version = await serverReady(signal);
      try { await client.fetchQuery({queryKey:ME_KEY,queryFn:api.me,staleTime:0,retry:false}); }
      catch (failure) { if (!(failure instanceof ApiError && failure.status === 401)) throw failure; }
      return version;
    },
  });
  useEffect(() => {
    if (cycle === null || !readiness.isSuccess || readiness.isFetching) return;
    const version = readiness.data;
    setCycle(null);
    if (version !== __LAYER8_VERSION__) {
      setFreshVersion(version);
      if (!document.querySelector('[data-unsaved-work="true"]') && ![...dirty.current].some(element => element.isConnected)) {
        // One automatic reload per target version per tab. Storage denied means manual refresh.
        try {
          const key = "layer8.reloaded-version";
          if (sessionStorage.getItem(key) !== version) { sessionStorage.setItem(key,version); applicationReload.run(); }
        } catch { /* Keep the explicit refresh button available. */ }
      }
    } else {
      client.invalidateQueries({predicate:query => !["me","restart-readiness"].includes(String(query.queryKey[0]))});
    }
  }, [cycle, readiness.isSuccess, readiness.isFetching, readiness.data, client]);
  const reconnecting = transientFailure(error) || cycle !== null;
  const blocked = transientFailure(error);
  const banner = reconnecting || freshVersion ? <section className="connection-notice" role="status" aria-live="polite">
    <strong>{reconnecting ? updating || watched.current ? "Layer 8 is restarting — reconnecting automatically" : "Reconnecting to Layer 8" : `Layer 8 ${freshVersion} is ready`}</strong>
    <p>{reconnecting ? "Services are temporarily unavailable or still being checked. Your current page stays open; you do not need to refresh." : "Finish or copy any unsaved work before loading the updated application."}</p>
    {admin && ["recovery_required","rolled_back","failed"].includes(phase) && <p>The update reports: {phase.replaceAll("_"," ")}. Review its result before attempting another update.</p>}
    {reconnecting ? <button className="btn2" type="button" onClick={() => { retryIdentity(); if(cycle !== null) readiness.refetch(); }}>Check connection now</button> : <button className="btn" type="button" onClick={() => applicationReload.run()}>Load updated app (refresh page)</button>}
  </section> : null;
  return { banner, blocked };
}
