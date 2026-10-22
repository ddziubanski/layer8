import { createContext, useContext } from "react";

/** Where the portal lives: "" for requesters, "/portal" when agents and admins use it. */
export const PortalBase = createContext("");

export function usePortalPath(): (path: string) => string {
  const base = useContext(PortalBase);
  return (path) => (path === "/" ? base || "/" : `${base}${path}`);
}
