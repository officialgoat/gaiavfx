import { test, expect, type Page, type Route } from "@playwright/test";
import { filmAsset, mockRecast } from "./recast-fixtures";
import { seedDirect } from "./direct-fixture";
import { PROCESS_MEDIA } from "../../frontend/src/features/generation/process-media";
import type { components } from "../../frontend/src/shared/api.generated";

type S = components["schemas"];

// CP3-C (ADR 0017 amendment 11, ADR 0012 Amendment 7 §8): a film has its own conversation, keyed to its recast.
// Plan mode is always on in a film, so its toggle is disabled with the reason and nothing asks to turn it off.

test.use({ reducedMotion: "reduce" });

const FILM_PLANS = "A film's conversation always plans before it renders.";

async function openFilm(page: Page) {
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page.getByRole("menuitem", { name: /Open film/ }).click();
  await page
    .getByRole("dialog", { name: "Open film" })
    .getByRole("button", { name: new RegExp(filmAsset.name) })
    .first()
    .click();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
}

/** No version's asset name or operation reaches the page: text, titles, labels, alt text or values. */
async function expectBlind(page: Page) {
  const leaks = await page.evaluate(() => {
    const secrets = ["PRIVATE SEED ARM ROUND", "hidden-operation"];
    const found: string[] = [];
    const text = document.body.innerText;
    for (const secret of secrets)
      if (text.includes(secret)) found.push(`text: ${secret}`);
    for (const element of Array.from(document.querySelectorAll("*")))
      for (const attribute of Array.from(element.attributes))
        for (const secret of secrets)
          if (attribute.value.includes(secret))
            found.push(`${element.tagName} ${attribute.name}: ${secret}`);
    return found;
  });
  expect(leaks).toEqual([]);
}

function filmSession(
  recastId: string,
  changes: Partial<S["HarnessSessionDTO"]> = {},
): S["HarnessSessionDTO"] {
  return {
    id: "film-session",
    movie_id: "",
    scope_kind: "film",
    scope_id: recastId,
    state: "idle",
    revision: 1,
    board: {
      version: 1,
      revision: 0,
      brief: "",
      understanding: "",
      decisions: [],
      references: [],
      open_questions: [],
    },
    budgets: {
      max_turns_per_message: 24,
      max_session_turns: 400,
      max_input_tokens: 4_000_000,
      max_output_tokens: 500_000,
      request_turns: 0,
      turns_spent: 0,
      input_tokens_spent: 0,
      output_tokens_spent: 0,
    },
    stop_reason: "",
    waiting_on: "",
    jobs_reserved: 0,
    jobs_limit: 0,
    autonomy: "off",
    plan_mode: true,
    plan: null,
    plan_history: [],
    activity: "idle",
    summary: "",
    plan_jobs_max: 50,
    ...changes,
  };
}

function turn(
  ordinal: number,
  role: "human" | "agent",
  action: Record<string, unknown>,
): S["TurnDTO"] {
  return {
    id: `film-turn-${ordinal}`,
    ordinal,
    role,
    state: "accepted",
    action,
    result: role === "agent" ? { ok: true, payload: {} } : null,
    receipts: [],
    provenance: null,
    created_at: "2026-10-22T10:00:00Z",
    updated_at: "2026-10-22T10:00:00Z",
  };
}

/** The film's agent API: the recast is mocked, so its conversation is too. Movie conversations stay real. */
async function mockFilmAgent(page: Page, recastId: string) {
  const requests: { method: string; url: URL; body: unknown }[] = [];
  let state: S["SessionStateDTO"] | null = null;
  await page.route("**/api/agent/sessions**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    requests.push({ method, url, body: request.postDataJSON() });
    const film =
      url.searchParams.get("recast_id") === recastId ||
      url.pathname.includes("/film-session") ||
      (method === "POST" &&
        url.pathname === "/api/agent/sessions" &&
        request.postDataJSON()?.scope_kind === "film");
    if (!film) return route.continue();
    if (url.searchParams.has("recast_id"))
      return route.fulfill({ json: state ? [state.session] : [] });
    if (method === "POST" && url.pathname === "/api/agent/sessions") {
      state = { session: filmSession(recastId), turns: [] };
      return route.fulfill({ status: 201, json: state.session });
    }
    if (method === "POST" && url.pathname.endsWith("/messages")) {
      const text = (request.postDataJSON() as S["MessageInput"]).text;
      state = {
        session: filmSession(recastId, { revision: 3 }),
        turns: [
          turn(0, "human", { message: text, attachments: [] }),
          turn(1, "agent", {
            tool: "say",
            thought: "",
            input: { text: "Mara is on screen longest; attach her picture." },
          }),
        ],
      };
      return route.fulfill({ status: 202, json: state });
    }
    if (method === "GET" && state) return route.fulfill({ json: state });
    return route.fulfill({
      status: 409,
      json: { detail: "Not part of this film test." },
    });
  });
  return {
    requests,
    set state(next: S["SessionStateDTO"]) {
      state = next;
    },
  };
}

test("a film talks in its own conversation, always plans, and mentions its characters", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const movie = (await seedDirect(request)).movie;
  const mock = await mockRecast(page);
  mock.existing();
  // A hidden character is not offered to @ mentions.
  mock.value.characters[2].hidden = true;
  const recastId = mock.value.id;
  const agent = await mockFilmAgent(page, recastId);
  await page.goto("/");
  await openFilm(page);
  // The movie loaded at startup may have listed its own conversations before the film opened; from the film's
  // first listing on, every request is the film's.
  const listsFilm = (r: (typeof agent.requests)[number]) =>
    r.method === "GET" && r.url.searchParams.get("recast_id") === recastId;
  const filmRequests = () =>
    agent.requests.slice(Math.max(0, agent.requests.findIndex(listsFilm)));
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  const region = page.getByRole("region", { name: "Film conversation" });
  await expect(region).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Movie conversation" }),
  ).toHaveCount(0);
  await expect(page.locator(".film-chat-note")).toHaveCount(0);
  await expect(region.locator(".chat-welcome h2")).toHaveText(
    "Ask about this film's cast, or plan a recast run.",
  );
  await expect.poll(() => agent.requests.some(listsFilm)).toBe(true);
  // The plan-mode toggle is on, disabled, and says why; the composer repeats the rule.
  const toggle = region.getByRole("button", { name: /Plan mode/ });
  await expect(toggle).toBeDisabled();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(toggle).toHaveAttribute("title", FILM_PLANS);
  await expect(region.locator(".composer-rule")).toContainText(FILM_PLANS);
  // @ offers the film's visible characters by label.
  const input = page.getByLabel("Message to Gaia");
  await expect(input).toBeEnabled();
  await input.fill("Recast @");
  const picker = page.getByRole("region", { name: "Mention an item" });
  await expect(picker.locator(".chat-mention-option")).toHaveText([
    /^Mara/,
    /^Elias/,
  ]);
  await expect(picker).toContainText("Characters");
  await picker.getByRole("button", { name: /^Mara/ }).click();
  await expect(input).toHaveValue("Recast @Mara ");
  await input.press("Shift+Tab");
  await input.pressSequentially("with her picture");
  await input.press("Enter");
  await expect(
    region
      .locator(".chat-message")
      .filter({ hasText: "Mara is on screen longest" }),
  ).toBeVisible();
  const created = filmRequests().find(
    (r) => r.method === "POST" && r.url.pathname === "/api/agent/sessions",
  );
  expect(created?.body).toEqual({
    movie_id: "",
    scope_kind: "film",
    scope_id: recastId,
  });
  const sent = filmRequests().find((r) => r.url.pathname.endsWith("/messages"));
  expect((sent?.body as S["MessageInput"]).text).toBe(
    "Recast @Mara with her picture\n\nReferences:\n@Mara (character: character-0)",
  );
  // Neither the first send nor Shift+Tab asked to turn plan mode off; nothing reached a movie's conversation.
  expect(
    agent.requests.filter((r) => r.url.pathname.endsWith("/plan-mode")),
  ).toEqual([]);
  expect(
    filmRequests().some((r) => r.url.searchParams.has("movie_id")),
  ).toBeFalsy();
  await expectBlind(page);
  // Opening a movie afterwards lists the movie's conversations by its id.
  const movieStart = agent.requests.length;
  await page.getByRole("button", { name: "File", exact: true }).click();
  await page
    .getByRole("menuitem", { name: "Open movie…", exact: false })
    .click();
  await page
    .getByRole("dialog", { name: "Open movie", exact: true })
    .getByRole("button", { name: movie.title, exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Movie conversation" }),
  ).toBeVisible();
  // A movie listed at startup may still be fresh in the cache; its 10-second refresh lists it again.
  await expect
    .poll(
      () =>
        agent.requests
          .slice(movieStart)
          .some(
            (r) =>
              r.method === "GET" &&
              r.url.searchParams.get("movie_id") === movie.id,
          ),
      { timeout: 15_000 },
    )
    .toBe(true);
  expect(agent.requests.slice(movieStart).some(listsFilm)).toBeFalsy();
  await expect(page.getByRole("button", { name: /Plan mode/ })).toBeEnabled();
});

// Astra R-C: a film's versions are reviewed blind, so its picker asks for the Library without their composites and
// process files.
test("the film's Attach from Library leaves out process files and composites", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await mockFilmAgent(page, mock.value.id);
  const listings: URL[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.pathname.endsWith("/assets")) listings.push(url);
  });
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  const region = page.getByRole("region", { name: "Film conversation" });
  const before = listings.length;
  await region.getByRole("button", { name: "Attach a reference" }).click();
  await expect
    .poll(() =>
      listings
        .slice(before)
        .filter((u) => u.searchParams.get("limit") === "30"),
    )
    .not.toEqual([]);
  for (const url of listings
    .slice(before)
    .filter((u) => u.searchParams.get("limit") === "30")) {
    expect(url.searchParams.get("exclude")).toBe(PROCESS_MEDIA);
    expect(url.searchParams.get("keep")).toBe("character_replacement");
  }
  await expectBlind(page);
});

test("a recast run's review step waits in the film's review queue, not in Review", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  const recastId = mock.value.id;
  const agent = await mockFilmAgent(page, recastId);
  agent.state = {
    session: filmSession(recastId, {
      state: "waiting_human",
      activity: "waiting_human",
      plan: {
        id: "film-plan",
        revision: 1,
        state: "executing",
        goal: "Recast Mara in the first shots",
        budget_jobs: 2,
        note: "",
        proposed_turn: 0,
        revision_requested: false,
        questions: [],
        grant: {
          budget_jobs: 2,
          step_kinds: ["render", "review"],
          approved_turn: 1,
          reserved_at_approval: 0,
        },
        steps: [
          {
            kind: "render",
            title: "Recast the first shots",
            target: recastId,
            jobs: 2,
            status: "done",
            receipts: [],
            review_of: "",
          },
          {
            kind: "review",
            title: "Review the recast",
            target: "",
            jobs: 0,
            status: "awaiting_user",
            receipts: [],
            review_of: `recast:${recastId}`,
          },
        ],
      },
    }),
    turns: [],
  };
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  const plan = page.getByRole("region", { name: "Plan revision 1" });
  await expect(plan).toBeVisible();
  await expect(plan.locator(".plan-review")).toHaveText(
    "Awaiting your review in the film's review queue",
  );
  await expect(
    plan.getByRole("button", { name: "Open in Review" }),
  ).toHaveCount(0);
  await expectBlind(page);
});
