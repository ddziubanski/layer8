// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";

/** "Layer 8 1.0.0 · Free software (AGPL-3.0) · Source code": the AGPL asks every network user to be
 * offered the source of what they're using. Shown in the console, the portal and on sign-in. */
export function SourceNotice({ className = "source-notice", compact = false, stacked = false }: { className?: string; compact?: boolean; stacked?: boolean }) {
  const about = useQuery({ queryKey: ["about"], queryFn: api.about, staleTime: Infinity, retry: false });
  if (!about.data) return null;
  const a = about.data;
  if (stacked) {
    // The collapsed console rail: one short item per line, both links kept.
    const source = a.source_download ?? a.source_repository;
    return (
      <p className={`${className} source-stacked`}>
        <span>{a.version}</span>
        <a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noreferrer">AGPL</a>
        {source && <a href={source} {...(a.source_download ? {} : { target: "_blank", rel: "noreferrer" })}>Source</a>}
      </p>
    );
  }
  if (compact) {
    // One short line for the console sidebar.
    const source = a.source_download ?? a.source_repository;
    return (
      <p className={className}>
        {a.product} {a.version} ·{" "}
        <a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noreferrer">
          AGPL-3.0
        </a>
        {source && (
          <>
            {" "}
            · <a href={source} {...(a.source_download ? {} : { target: "_blank", rel: "noreferrer" })}>Source</a>
          </>
        )}
      </p>
    );
  }
  return (
    <p className={className}>
      {a.product} {a.version} · Free software under{" "}
      <a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noreferrer">
        AGPL-3.0
      </a>
      {a.source_download && (
        <>
          {" "}
          · <a href={a.source_download}>Source code</a>
        </>
      )}
      {a.source_repository && (
        <>
          {" "}
          ·{" "}
          <a href={a.source_repository} target="_blank" rel="noreferrer">
            {a.source_download ? "Repository" : "Source code"}
          </a>
        </>
      )}
    </p>
  );
}
