import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Markdown } from "../../components/Markdown";
import { HelpArticle, HelpList, HelpSuggestions } from "../../portal/Help";
import { ME, mockApi, renderAt, type Call } from "../../test/util";
import { KnowledgeCard } from "../ticket/KnowledgeCard";
import { KnowledgePage } from "./KnowledgePage";

afterEach(() => vi.unstubAllGlobals());

const ARTICLE = {
  id: 4, title: "Outlook keeps asking for a password", excerpt: "Close Outlook…", category: "Email", audience: "everyone", status: "published",
  views: 3, helpful_yes: 2, helpful_no: 0, published_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z",
};
const FULL = { ...ARTICLE, body: "## Fix\n\n1. Close Outlook\n2. Clear **saved** passwords", keywords: "mail prompt", source_ticket_id: 12, author: null, updated_by: null, created_at: "2026-10-20T00:00:00Z", can_edit: true };

test("Markdown: headings, steps, bold, code; only web and mail links are links", () => {
  render(<Markdown text={"## Steps\n\n1. Open `Credential Manager`\n2. Remove **Outlook**\n\n[Guide](https://example.com/g) and [bad](javascript:alert(1))\n\n> Note: <b>not html</b>"} />);
  expect(screen.getByRole("heading", { name: "Steps" })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Open Credential Manager", "Remove Outlook"]);
  expect(screen.getByText("Credential Manager").tagName).toBe("CODE");
  expect(screen.getByText("Outlook").tagName).toBe("STRONG");
  expect(screen.getByRole("link", { name: "Guide" })).toHaveAttribute("href", "https://example.com/g");
  expect(screen.queryByRole("link", { name: "bad" })).toBeNull();
  expect(screen.getByText(/Note: <b>not html<\/b>/)).toBeInTheDocument();
});

test("write an article and publish it", async () => {
  const calls = mockApi({
    "GET /api/kb/articles": [],
    "GET /api/portal/queues": [{ id: 1, name: "Desk", description: null, categories: ["Email", "Printing"] }],
    "POST /api/kb/articles": ({ body }: Call) => ({ ...FULL, ...(body as object), id: 9 }),
    "GET /api/kb/articles/9": FULL,
  });
  renderAt("/kb/new", [{ path: "/kb/:articleId", element: <KnowledgePage me={ME} /> }]);
  const form = await screen.findByRole("form", { name: "New article" });
  await userEvent.type(within(form).getByLabelText("Title"), "VPN from home");
  await userEvent.type(within(form).getByLabelText(/Category/), "Network");
  await userEvent.type(within(form).getByLabelText("Article text"), "Open **AnyConnect**");
  await userEvent.click(within(form).getByRole("button", { name: "Preview" }));
  expect(within(form).getByText("AnyConnect").tagName).toBe("STRONG");
  await userEvent.click(within(form).getByRole("button", { name: "Publish" }));
  await waitFor(() =>
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ title: "VPN from home", category: "Network", audience: "everyone", status: "published", body: "Open **AnyConnect**" }),
  );
});

test("portal: suggestions while typing, then the article and whether it helped", async () => {
  const calls = mockApi({
    "GET /api/kb/articles": [ARTICLE],
    "GET /api/kb/articles/4": FULL,
    "POST /api/kb/articles/4/feedback": { ...ARTICLE, helpful_no: 1 },
  });
  renderAt("/", [
    { path: "/", element: <HelpSuggestions text="outlook password" category="Email" /> },
    { path: "/help", element: <HelpList /> },
    { path: "/help/:articleId", element: <HelpArticle /> },
  ]);
  const box = await screen.findByRole("region", { name: "These might help" });
  expect(within(box).getByRole("link", { name: ARTICLE.title })).toHaveAttribute("href", "/help/4");
  expect(calls[0].path).toContain("q=outlook+password");
  expect(calls[0].path).toContain("category=Email");

  renderAt("/help/4", [{ path: "/help/:articleId", element: <HelpArticle /> }]);
  const art = await screen.findByRole("article", { name: ARTICLE.title });
  expect(within(art).getAllByRole("listitem")).toHaveLength(2);
  await userEvent.click(within(art).getByRole("button", { name: "No" }));
  expect(await within(art).findByRole("link", { name: "Ask the service desk" })).toBeInTheDocument();
  expect(calls.find((c) => c.method === "POST")!.body).toEqual({ helpful: false });
});

test("ticket: use a matching article, see similar solved tickets, draft one from a solved ticket", async () => {
  let used = false;
  const kb = { articles: [ARTICLE], similar: [{ id: 3, label: "#3", subject: "Outlook password prompt", status: "resolved", category: null, resolved_at: null, score: 0.2 }], linked: [] };
  const calls = mockApi({
    "GET /api/tickets/12/kb": () => (used ? { ...kb, articles: [], linked: [ARTICLE] } : kb),
    "POST /api/tickets/12/kb/links": () => ((used = true), { ...kb, articles: [], linked: [ARTICLE] }),
    "POST /api/tickets/12/kb/draft": { ...FULL, id: 20, status: "draft" },
  });
  const ticket = { id: 12, status: "resolved" } as Parameters<typeof KnowledgeCard>[0]["ticket"];
  renderAt("/", [
    { path: "/", element: <KnowledgeCard ticket={ticket} /> },
    { path: "/kb/:articleId/:mode", element: <p>Editing draft</p> },
  ]);
  const card = await screen.findByRole("region", { name: "Knowledge base" });
  expect(await within(card).findByRole("link", { name: "#3 Outlook password prompt" })).toHaveAttribute("href", "/tickets/3");
  await userEvent.click(within(card).getByRole("button", { name: "Used it" }));
  expect(await within(card).findByLabelText("Used on this ticket")).toHaveTextContent(ARTICLE.title);
  expect(calls.find((c) => c.path === "/api/tickets/12/kb/links")!.body).toEqual({ article_id: 4 });
  await userEvent.click(within(card).getByRole("button", { name: "Draft an article from this ticket" }));
  expect(await screen.findByText("Editing draft")).toBeInTheDocument();
});

test("Markdown shows only images uploaded to articles", () => {
  render(<Markdown text={"![Tray 2](/api/kb/files/0b6e2f1c-1111-4222-8333-444455556666)\n\n![tracker](https://evil.example/pixel.gif)"} />);
  expect(screen.getByRole("img", { name: "Tray 2" })).toHaveAttribute("src", "/api/kb/files/0b6e2f1c-1111-4222-8333-444455556666");
  expect(screen.getAllByRole("img")).toHaveLength(1);
});

test("add an image to an article: its Markdown goes in the text", async () => {
  const calls = mockApi({
    "GET /api/kb/articles": [],
    "GET /api/portal/queues": [],
    "GET /api/kb/articles/4": FULL,
    "POST /api/kb/articles/4/files": { id: "f1", filename: "tray.png", url: "/api/kb/files/f1", markdown: "![tray](/api/kb/files/f1)" },
  });
  renderAt("/kb/4/edit", [{ path: "/kb/:articleId/:mode", element: <KnowledgePage me={ME} /> }]);
  const form = await screen.findByRole("form", { name: "Edit article" });
  const input = form.querySelector<HTMLInputElement>('input[type="file"]')!;
  await userEvent.upload(input, new File([new Uint8Array([137, 80, 78, 71])], "tray.png", { type: "image/png" }));
  await waitFor(() => expect(within(form).getByLabelText("Article text")).toHaveValue("![tray](/api/kb/files/f1)\n" + FULL.body));
  expect(calls.some((c) => c.path === "/api/kb/articles/4/files")).toBe(true);
});

test("an article's history: see what changed and bring a version back", async () => {
  const calls = mockApi({
    "GET /api/kb/articles": [],
    "GET /api/kb/articles/4": FULL,
    "GET /api/kb/articles/4/versions": [
      { number: 2, title: FULL.title, note: "", author: { id: 1, email: "jpark@corp.local", display_name: "J. Park" }, created_at: "2026-10-20T10:00:00Z", is_current: true },
      { number: 1, title: FULL.title, note: "", author: null, created_at: "2026-10-19T10:00:00Z", is_current: false },
    ],
    "GET /api/kb/articles/4/versions/1": { number: 1, title: FULL.title, note: "", author: null, created_at: "2026-10-19T10:00:00Z", is_current: false, body: "old", keywords: "", diff: "--- nothing\n+++ version 1\n+old" },
    "POST /api/kb/articles/4/versions/1/restore": FULL,
  });
  renderAt("/kb/4", [{ path: "/kb/:articleId", element: <KnowledgePage me={ME} /> }]);
  await userEvent.click(await screen.findByRole("button", { name: "History…" }));
  const history = await screen.findByRole("region", { name: "History" });
  await userEvent.click((await within(history).findAllByRole("button", { name: "Show" }))[1]);
  const v1 = await within(history).findByRole("region", { name: "Version 1" });
  expect(within(v1).getByText("+old")).toHaveClass("diff-add");
  await userEvent.click(within(v1).getByRole("button", { name: "Bring version 1 back" }));
  await waitFor(() => expect(calls.some((c) => c.path === "/api/kb/articles/4/versions/1/restore")).toBe(true));
});
