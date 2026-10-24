// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef } from "react";
import { useLocation } from "react-router";

/** Move keyboard/screen-reader focus after page navigation, never after filter changes. */
export function RouteFocus({ mainId }: { mainId: string }) {
  const { pathname, hash } = useLocation();
  const previous = useRef(pathname);
  useEffect(() => {
    if (previous.current === pathname) return;
    previous.current = pathname;
    if (hash) return;
    const main = document.getElementById(mainId);
    if (!main) return;
    main.focus({ preventScroll: true });
    const focusHeading = () => {
      const heading = main.querySelector<HTMLElement>("h1");
      if (!heading) return false;
      // Lazy routes may finish after the person already started interacting.
      if (document.activeElement === main) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
      return true;
    };
    if (focusHeading()) return;
    const observer = new MutationObserver(() => { if (focusHeading()) observer.disconnect(); });
    observer.observe(main, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [pathname, hash, mainId]);
  return null;
}
