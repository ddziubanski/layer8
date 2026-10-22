import { useRef, useState, type DragEvent } from "react";

/** Drag-and-drop files onto an element. Spread `props` on it; `dragging` styles it while
 * files are over it. Only file drags count (dragging text around the page doesn't). */
export function useDropFiles(onFiles: (files: File[]) => void, { disabled = false } = {}) {
  const [dragging, setDragging] = useState(false);
  // dragenter/dragleave fire for every child; count them so the highlight doesn't flicker.
  const depth = useRef(0);
  const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
  const props = {
    onDragEnter: (e: DragEvent) => {
      if (disabled || !hasFiles(e)) return;
      e.preventDefault();
      depth.current += 1;
      setDragging(true);
    },
    onDragOver: (e: DragEvent) => {
      if (disabled || !hasFiles(e)) return;
      // Required for the drop event to fire; shows a "copy" cursor.
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: (e: DragEvent) => {
      if (disabled || !hasFiles(e)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    onDrop: (e: DragEvent) => {
      if (disabled || !hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      setDragging(false);
      const files = Array.from(e.dataTransfer.files ?? []);
      if (files.length) onFiles(files);
    },
  };
  return { dragging, props };
}
