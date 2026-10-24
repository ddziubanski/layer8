// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from "react";

/** Typed text, settled for a moment (so a search isn't sent on every key). */
export function useSettled(value: string, ms = 250): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}
