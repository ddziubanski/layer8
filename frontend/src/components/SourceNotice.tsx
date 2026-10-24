import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";

/** "Layer 8 1.0.0 · Free software (AGPL-3.0) · Source code": the AGPL asks every network user to be
 * offered the source of what they're using. Shown in the console, the portal and on sign-in. */
export function SourceNotice({ className = "source-notice" }: { className?: string }) {
  const about = useQuery({ queryKey: ["about"], queryFn: api.about, staleTime: Infinity, retry: false });
  if (!about.data) return null;
  const a = about.data;
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
