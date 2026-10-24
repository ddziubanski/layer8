// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createBrowserRouter, Link, NavLink, Outlet, type RouteObject } from "react-router";

import { api, type Me } from "../api/client";
import { Brand, backgroundStyle, useBranding } from "../branding";
import { RouteFocus } from "../components/RouteFocus";
import { SourceNotice } from "../components/SourceNotice";
import { NoticeBanner, NoticeDialog } from "../components/Notices";
import { skipSilentWindowsSignIn } from "../pages/Login";
import { PortalBase, usePortalPath } from "./base";
import { HelpArticle, HelpList } from "./Help";
import { Home } from "./Home";
import { MyTickets } from "./MyTickets";
import { NewRequest } from "./NewRequest";
import { PortalTicket } from "./PortalTicket";

function PortalShell({ me }: { me: Me }) {
  const p = usePortalPath();
  const brand = useBranding();
  const staff = me.role !== "requester";
  const queryClient = useQueryClient();
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      skipSilentWindowsSignIn();
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
  return (
    <div className={`portal${brand.background_url ? " has-bg" : ""}`} style={backgroundStyle(brand, "portal")}>
      <a className="skip-link" href="#portal-main">Skip to request workspace</a>
      <header className="topbar">
        <Brand b={brand} />
        <nav aria-label="Portal">
          <NavLink to={p("/")} end>
            New request
          </NavLink>
          <NavLink to={p("/tickets")}>My tickets</NavLink>
          <NavLink to={p("/help")}>Help articles</NavLink>
        </nav>
        <span className="spacer" />
        {staff && !me.portal_only && (
          <Link to="/" className="topbar-link">
            Back to console
          </Link>
        )}
        {staff && me.portal_only && (
          // A full page load: the app shows the password sign-in for this.
          <a href="/?console-sign-in" className="topbar-link">
            Sign in to the console
          </a>
        )}
        <span>{me.display_name}</span>
        <button type="button" className="linkish topbar-link" onClick={() => logout.mutate()}>
          Sign out
        </button>
      </header>
      <main id="portal-main" tabIndex={-1} className="portal-main">
        <RouteFocus mainId="portal-main" />
        <NoticeBanner />
        <NoticeDialog />
        <Outlet />
        <SourceNotice className="source-notice portal-source" />
      </main>
    </div>
  );
}

/** The portal's routes. Requesters get them at the root; agents and admins under /portal. */
export function portalRoutes(me: Me, base = ""): RouteObject {
  return {
    path: base || "/",
    element: (
      <PortalBase.Provider value={base}>
        <PortalShell me={me} />
      </PortalBase.Provider>
    ),
    children: [
      { index: true, element: <Home /> },
      { path: "new", element: <NewRequest /> },
      { path: "tickets", element: <MyTickets /> },
      { path: "help", element: <HelpList /> },
      { path: "help/:articleId", element: <HelpArticle /> },
      // Same path as ticket links in emails, so they open here for requesters.
      { path: "tickets/:ticketId", element: <PortalTicket me={me} /> },
      { path: "*", element: <p>Page not found.</p> },
    ],
  };
}

/** Requesters, and technicians signed in with Windows (who also keep their /portal links). */
export const portalRouter = (me: Me) =>
  createBrowserRouter(me.role === "requester" ? [portalRoutes(me)] : [portalRoutes(me), portalRoutes(me, "/portal")]);
