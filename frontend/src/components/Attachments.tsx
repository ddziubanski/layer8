import { useEffect, useRef, useState, type ReactNode } from "react";

import { api, type Attachment, type Comment } from "../api/client";
import { fileSize, when } from "../format";
import { useDropFiles } from "./useDropFiles";

const ICON: Record<string, string> = { image: "IMG", pdf: "PDF", text: "TXT" };

/** Short "Attached: a.png, b.txt" line under a message; the files live in the Attachments section. */
export function AttachedLine({ files }: { files: Attachment[] }) {
  if (files.length === 0) return null;
  return (
    <p className="muted small" style={{ margin: "8px 0 0" }}>
      Attached: {files.map((f) => f.filename).join(", ")}
    </p>
  );
}

function Preview({ files, index, onIndex, onClose }: { files: Attachment[]; index: number; onIndex: (i: number) => void; onClose: () => void }) {
  const file = files[index];
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight" && index < files.length - 1) onIndex(index + 1);
      if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, files.length, onIndex, onClose]);

  const src = api.viewUrl(file.id);
  return (
    <div className="overlay" onClick={onClose}>
      <div className="viewer" role="dialog" aria-modal="true" aria-label={`Preview of ${file.filename}`} onClick={(e) => e.stopPropagation()}>
        <div className="row-gap viewer-bar">
          <strong className="spacer">{file.filename}</strong>
          <span className="muted small">{fileSize(file.size_bytes)}</span>
          <button className="btn2" type="button" disabled={index === 0} onClick={() => onIndex(index - 1)} aria-label="Previous file">
            ‹
          </button>
          <button className="btn2" type="button" disabled={index === files.length - 1} onClick={() => onIndex(index + 1)} aria-label="Next file">
            ›
          </button>
          <a className="btn2" href={api.downloadUrl(file.id)}>
            Download
          </a>
          <button className="btn" type="button" onClick={onClose} autoFocus>
            Close
          </button>
        </div>
        <div className="viewer-body">
          {file.preview === "image" ? (
            <img src={src} alt={file.filename} />
          ) : (
            <iframe src={src} title={file.filename} />
          )}
        </div>
      </div>
    </div>
  );
}

// Only where there's a real mouse: touch screens have no hover.
const canHover = () => typeof window !== "undefined" && window.matchMedia?.("(hover: hover) and (pointer: fine)").matches !== false;

/** Rest the mouse on a file for a moment to see it; leave to hide it. Click still opens the viewer. */
function HoverPreview({ file, children }: { file: Attachment; children: ReactNode }) {
  const [shown, setShown] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stop = () => {
    if (timer.current) clearTimeout(timer.current);
    setShown(false);
  };
  useEffect(() => () => stop(), []);
  if (!file.preview) return <>{children}</>;
  return (
    <span
      className="hover-preview-anchor"
      onMouseEnter={() => {
        if (!canHover()) return;
        timer.current = setTimeout(() => setShown(true), 350);
      }}
      onMouseLeave={stop}
    >
      {children}
      {shown && (
        <span className="hover-preview" role="tooltip" aria-label={`Preview of ${file.filename}`}>
          {file.preview === "image" ? <img src={api.viewUrl(file.id)} alt="" /> : <iframe src={api.viewUrl(file.id)} title={`Preview of ${file.filename}`} tabIndex={-1} />}
        </span>
      )}
    </span>
  );
}

/** Every file on the ticket, as documents: who added it, when, from which message, view or download.
 * onDropFiles: files dragged onto the section are uploaded to the ticket. */
export function AttachmentsSection({
  files,
  comments,
  describeAuthor,
  onDropFiles,
}: {
  files: Attachment[];
  comments: Comment[];
  describeAuthor: (id: number | null | undefined, fallback?: string) => string;
  onDropFiles?: (files: File[]) => void;
}) {
  const viewable = files.filter((f) => f.preview);
  const [open, setOpen] = useState<number | null>(null);
  const drop = useDropFiles((dropped) => onDropFiles?.(dropped), { disabled: !onDropFiles });
  const fromLabel = (f: Attachment) => {
    if (f.comment_id === null) return "original request";
    const c = comments.find((x) => x.id === f.comment_id);
    return c ? `${c.is_internal ? "internal note" : "reply"}, ${when(c.created_at)}` : "a reply";
  };

  return (
    <div className={`card composer${drop.dragging ? " dropping" : ""}`} {...drop.props}>
      <div className="lbl">Attachments ({files.length})</div>
      {drop.dragging && <div className="drop-hint">Drop files to add them to the ticket</div>}
      {files.length === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>
          No files yet.{onDropFiles ? " Drag files here to add them." : ""}
        </p>
      ) : (
        <ul className="docs">
          {files.map((f) => (
            <li key={f.id}>
              <span className="doc-icon" aria-hidden="true">
                {f.preview === "image" ? <img src={api.viewUrl(f.id)} alt="" loading="lazy" /> : (f.preview && ICON[f.preview]) || "FILE"}
              </span>
              <span className="doc-main">
                {f.preview ? (
                  <HoverPreview file={f}>
                    <button type="button" className="linkish doc-name" onClick={() => setOpen(viewable.indexOf(f))}>
                      {f.filename}
                    </button>
                  </HoverPreview>
                ) : (
                  <a className="doc-name" href={api.downloadUrl(f.id)}>
                    {f.filename}
                  </a>
                )}
                <span className="muted small">
                  {fileSize(f.size_bytes)} · {describeAuthor(f.uploaded_by?.id, f.uploaded_by?.display_name)} · {when(f.created_at)} · {fromLabel(f)}
                </span>
              </span>
              {f.preview && (
                <button type="button" className="btn2" onClick={() => setOpen(viewable.indexOf(f))} aria-label={`View ${f.filename}`}>
                  View
                </button>
              )}
              <a className="btn2" href={api.downloadUrl(f.id)} aria-label={`Download ${f.filename}`}>
                Download
              </a>
            </li>
          ))}
        </ul>
      )}
      {open !== null && viewable[open] && <Preview files={viewable} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />}
    </div>
  );
}
