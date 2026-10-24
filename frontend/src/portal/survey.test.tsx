// SPDX-License-Identifier: AGPL-3.0-or-later
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mockApi, renderAt, type Call } from "../test/util";
import { SurveyPage, TicketSurvey } from "./Survey";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

const SURVEY = {
  ticket_label: "#1005", subject: "Toner low", question: "How happy are you with how we handled this request?",
  ratings: { 1: "Very unhappy", 2: "Unhappy", 3: "OK", 4: "Happy", 5: "Very happy" }, rating: null, comment: "", expired: false,
};

test("the emailed link answers in one click, then takes a comment", async () => {
  window.history.pushState({}, "", "/survey/tok-123?r=2");
  let saved: Record<string, unknown> = { ...SURVEY };
  const calls = mockApi({
    "GET /api/branding": {},
    "GET /api/surveys/answer/tok-123": () => saved,
    "POST /api/surveys/answer/tok-123": ({ body }: Call) => (saved = { ...saved, ...(body as object) }),
  });
  renderAt("/", [{ path: "*", element: <SurveyPage /> }]);
  const box = await screen.findByRole("region", { name: "Survey" });
  expect(within(box).getByText("#1005 Toner low")).toBeInTheDocument();
  await waitFor(() => expect(within(box).getByRole("radio", { name: "2 · Unhappy" })).toHaveAttribute("aria-checked", "true"));
  expect(within(box).getByText(/Sorry it wasn't better/)).toBeInTheDocument();
  await userEvent.type(within(box).getByLabelText(/Comment/), "Took three days");
  await userEvent.click(within(box).getByRole("button", { name: "Send comment" }));
  await waitFor(() => expect(calls.filter((c) => c.method === "POST").at(-1)!.body).toEqual({ rating: 2, comment: "Took three days" }));
  expect(calls.filter((c) => c.method === "POST")[0].body).toEqual({ rating: 2, comment: "" });
});

test("a bad link says so; a closed survey says thanks anyway", async () => {
  window.history.pushState({}, "", "/survey/nope");
  mockApi({ "GET /api/branding": {} });
  renderAt("/", [{ path: "*", element: <SurveyPage /> }]);
  expect(await screen.findByText("That survey link isn't valid.")).toBeInTheDocument();

  mockApi({ "GET /api/portal/tickets/9/survey": { ...SURVEY, expired: true, rating: 4 } });
  renderAt("/", [{ path: "*", element: <TicketSurvey ticketId={9} /> }]);
  const box = await screen.findByRole("region", { name: "How did we do?" });
  expect(within(box).getByRole("radio", { name: "4 · Happy" })).toBeDisabled();
});
