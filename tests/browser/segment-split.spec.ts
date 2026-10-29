import { test, expect, type Page } from "@playwright/test";
import { openMovie, seedDirect } from "./direct-fixture";
import { seedProduction } from "./production-fixture";

test.describe("Split segments through Direction receipts", () => {
  let data: Awaited<ReturnType<typeof seedDirect>>;
  test.beforeEach(async ({ request }) => {
    data = await seedDirect(request);
    const phase = await request.post(
      `/api/direction/segments/${data.second.id}/phases`,
      {
        headers: data.headers,
        data: {
          expected_revision: data.cut.receipt.after.revision,
          start_frame: 72,
          content: {
            description: "Pause at the gate",
            screen_direction: "",
            sound: "",
            cast: data.second.content.subjects,
          },
        },
      },
    );
    expect(phase.ok(), await phase.text()).toBeTruthy();
  });

  async function confirm(page: Page, frame: number) {
    const dialog = page.getByRole("dialog", {
      name: "Split segment",
      exact: true,
    });
    await expect(dialog).toContainText(`at frame ${frame}?`);
    await dialog
      .getByRole("button", { name: "Split segment", exact: true })
      .click();
    await expect(dialog).toBeHidden();
    await expect(page.locator(".segment-item")).toHaveCount(3);
    await expect(page.locator(".studio-status")).toContainText("Segment split");
  }

  async function undo(page: Page) {
    await page.keyboard.press("Control+z");
    await expect(page.locator(".segment-item")).toHaveCount(2);
    await expect(page.locator(".studio-status")).toContainText("Change undone");
  }

  test("cut and phase context menus, confirmation, Undo and Redo", async ({
    page,
    request,
  }) => {
    await openMovie(page, data.movie.title);
    const cut = page.getByRole("button", { name: /Cut f36/ });
    await cut.click({ button: "right" });
    await page
      .getByRole("menuitem", { name: "Split segment here", exact: true })
      .click();
    await confirm(page, 36);
    const response = await request.get(
      `/api/direction/movies/${data.movie.id}`,
      { headers: data.headers },
    );
    const movie = await response.json();
    expect(
      movie.segments.map(
        (s: { delivered_frames: number }) => s.delivered_frames,
      ),
    ).toEqual([120, 36, 84]);
    expect(movie.segments[2].continues).toBe(true);
    expect(movie.segments[2].cuts).toEqual([]);
    expect(movie.segments[2].phases[0].start_frame).toBe(0);
    await undo(page);
    await page.keyboard.press("Control+Shift+z");
    await expect(page.locator(".segment-item")).toHaveCount(3);
    await expect(page.locator(".studio-status")).toContainText("Change redone");
    await undo(page);
    await page
      .getByRole("button", { name: "Phase: Pause at the gate", exact: true })
      .click({ button: "right" });
    await page
      .getByRole("menuitem", {
        name: "Split segment at this phase",
        exact: true,
      })
      .click();
    await confirm(page, 72);
    await undo(page);
  });

  test("Razor snaps to the nearest legal point, supports cancel and returns to Select", async ({
    page,
  }) => {
    await openMovie(page, data.movie.title);
    await page.getByRole("button", { name: "Razor tool", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Razor tool", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    const first = page.getByRole("button", { name: "Segment 01", exact: true });
    await expect(first).toBeDisabled();
    await expect(first).toHaveAttribute("title", /Add a cut or phase boundary/);
    const second = page.getByRole("button", {
      name: "Segment 02",
      exact: true,
    });
    const box = (await second.boundingBox())!;
    await second.click({
      position: { x: box.width * 0.65, y: box.height / 2 },
    });
    const dialog = page.getByRole("dialog", {
      name: "Split segment",
      exact: true,
    });
    await expect(dialog).toContainText("at frame 72?");
    await page.screenshot({
      path: test.info().outputPath("razor-confirmation.png"),
    });
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.locator(".segment-item")).toHaveCount(2);
    await second.click({
      position: { x: box.width * 0.65, y: box.height / 2 },
    });
    await confirm(page, 72);
    await page.keyboard.press("v");
    await expect(
      page.getByRole("button", { name: "Select tool", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await undo(page);
  });

  test("Ctrl+K uses the playhead, with reasons for non-boundaries and the first phase", async ({
    page,
  }) => {
    await openMovie(page, data.movie.title);
    // Selection deliberately differs from the segment under the playhead.
    await page.getByRole("button", { name: "Segment 01", exact: true }).click();
    const ruler = page.getByRole("slider", { name: "Playhead", exact: true });
    await ruler.focus();
    await page.keyboard.press("Home");
    await page.keyboard.press("Control+k");
    await expect(
      page.getByRole("dialog", { name: "Split segment", exact: true }),
    ).toBeHidden();
    await page.getByRole("button", { name: "Segment", exact: true }).click();
    await expect(
      page.getByRole("menuitem", { name: /Split segment at playhead/ }),
    ).toHaveAttribute("aria-disabled", "true");
    await expect(
      page.getByRole("menuitem", { name: /Split segment at playhead/ }),
    ).toContainText("Split at a cut or a phase boundary");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    // A closed menu returns the focus to where it was before the pointer opened it (usability round 2, C8): the
    // ruler. Wait for it before moving the focus again.
    await expect(ruler).toBeFocused();
    await ruler.focus();
    await expect(ruler).toBeFocused();
    for (let i = 0; i < 8; i++) {
      await ruler.press("Shift+ArrowRight");
      await expect(ruler).toHaveAttribute(
        "aria-valuenow",
        String((i + 1) * 24),
      );
    }
    await expect(ruler).toHaveAttribute("aria-valuenow", "192");
    await page.keyboard.press("Control+k");
    await confirm(page, 72);
    await undo(page);
    await page
      .getByRole("button", { name: /Phase: Arin walks toward/ })
      .first()
      .click({ button: "right" });
    await expect(
      page.getByRole("menuitem", { name: /Split segment at this phase/ }),
    ).toHaveAttribute("aria-disabled", "true");
    await expect(
      page.getByRole("menuitem", { name: /Split segment at this phase/ }),
    ).toContainText("Split at a cut or a phase boundary");
  });

  // Pilot T8-F1: after the split confirmation, opened from the Segment menu, Ctrl+Z and Ctrl+Shift+Z work at once:
  // the closed sheet hands focus back to the menu's trigger instead of losing it.
  test("Undo and Redo keys work right after a split confirmed from the Segment menu", async ({
    page,
  }) => {
    await openMovie(page, data.movie.title);
    const ruler = page.getByRole("slider", { name: "Playhead", exact: true });
    await ruler.focus();
    await page.keyboard.press("Home");
    for (let i = 0; i < 8; i++) await ruler.press("Shift+ArrowRight");
    await expect(ruler).toHaveAttribute("aria-valuenow", "192");
    await page.getByRole("button", { name: "Segment", exact: true }).click();
    await page
      .getByRole("menuitem", { name: /Split segment at playhead/ })
      .click();
    await confirm(page, 72);
    await expect(
      page.getByRole("button", { name: "Segment", exact: true }),
    ).toBeFocused();
    await undo(page);
    await page.keyboard.press("Control+Shift+z");
    await expect(page.locator(".segment-item")).toHaveCount(3);
    await expect(page.locator(".studio-status")).toContainText("Change redone");
  });

  // Round 2 C14 (T8-F1): a split keeps the takes with the first segment and marks them changed, so the
  // new continuation lists a requirement for a current take instead of reading "Ready to render".
  test("the continuation made by a split needs a current take of the first segment", async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    const headers = data.headers;
    async function call<T>(url: string, body?: unknown, method = "POST") {
      const response = await request.fetch(url, {
        headers,
        method: body === undefined ? "GET" : method,
        data: body,
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      return (await response.json()) as T;
    }
    type Segment = { revision: number; content: object };
    const url = `/api/direction/segments/${data.first.id}`;
    let first = await call<Segment>(url);
    await call(
      url,
      {
        expected_revision: first.revision,
        content: { ...first.content, states: [] },
      },
      "PATCH",
    );
    first = await call<Segment>(url);
    await call(`${url}/phases`, {
      expected_revision: first.revision,
      start_frame: 72,
      content: {
        description: "Arin reaches the railing",
        screen_direction: "",
        sound: "",
        cast: data.first.content.subjects,
      },
    });
    // FakeComfy renders the first segment; the user selects its take before the split.
    const render = await call<{ id: string }>("/api/production/renders", {
      segment_id: data.first.id,
      seeds: [1],
      request_key: `split-take-${data.first.id}`,
    });
    await expect
      .poll(
        async () =>
          (
            await call<{ state: string }>(
              `/api/production/operations/${render.id}`,
            )
          ).state,
        { timeout: 90_000 },
      )
      .toBe("succeeded");
    const review = await call<{
      id: string;
      members: { candidate_id: string }[];
    }>(`/api/production/renders/${render.id}/review`, {});
    await call(`/api/production/reviews/${review.id}/select`, {
      candidate_id: review.members[0].candidate_id,
      expected_revision: 0,
    });
    await openMovie(page, data.movie.title);
    await page
      .getByRole("button", { name: "Phase: Arin reaches the railing" })
      .click({ button: "right" });
    await page
      .getByRole("menuitem", {
        name: "Split segment at this phase",
        exact: true,
      })
      .click();
    await confirm(page, 72);
    await page.getByRole("button", { name: "Segment 02", exact: true }).click();
    await page.getByRole("tab", { name: "Properties", exact: true }).click();
    const properties = page.locator(".properties-content");
    await expect(properties.getByLabel("Render requirements")).toContainText(
      "Select a current take of segment 1 first.",
    );
    await expect(
      properties.getByText("Needs preparation", { exact: true }),
    ).toBeVisible();
    // Round 2 T8-F8: the button's tooltip says what it does, not only its shortcut.
    const button = properties.getByRole("button", { name: /Render segment/ });
    await expect(button).toHaveAttribute("title", /^Render segment \(.+\)$/);
    await button.click();
    const form = page.getByRole("dialog", {
      name: "Render segment",
      exact: true,
    });
    await expect(form.getByLabel("Render requirements")).toContainText(
      "Select a current take of segment 1 first.",
    );
    await expect(
      form.getByRole("button", { name: "Render segment", exact: true }),
    ).toBeDisabled();
  });

  test("a protected cut explains its refusal and another phase remains available", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    // The existing fixture uses the real preparation APIs with FakeComfy, never a GPU.
    const prepared = await seedProduction(request);
    data = prepared;
    for (const member of prepared.views.members)
      await prepared.post("/api/production/views/approve", {
        candidate_id: member.candidate_id,
      });
    const evidence = await request.get(
      `/api/direction/angles/${data.to.id}/evidence`,
      { headers: data.headers },
    );
    const segment = await request.get(
      `/api/direction/segments/${data.second.id}`,
      { headers: data.headers },
    );
    await prepared.post(`/api/direction/segments/${data.second.id}/phases`, {
      expected_revision: (await segment.json()).revision,
      start_frame: 72,
      content: {
        description: "Pause at the gate",
        screen_direction: "",
        sound: "",
        cast: data.second.content.subjects,
      },
    });
    const judgment = await request.post(
      `/api/direction/cuts/${data.cut.id}/suitability`,
      {
        headers: data.headers,
        data: {
          reference_kind: "view",
          reference_id: (await evidence.json())[0].id,
          verdict: "suits",
          note: "Keep this history",
        },
      },
    );
    expect(judgment.ok(), await judgment.text()).toBeTruthy();
    await openMovie(page, data.movie.title);
    await page
      .getByRole("button", { name: /Cut f36/ })
      .click({ button: "right" });
    const action = page.getByRole("menuitem", { name: /Split segment here/ });
    await expect(action).toHaveAttribute("aria-disabled", "true");
    await expect(action).toContainText("suitability history");
    await expect(action).toContainText("phase boundary instead");
    await page.keyboard.press("Escape");
    await page
      .getByRole("button", { name: "Phase: Pause at the gate", exact: true })
      .click({ button: "right" });
    await page
      .getByRole("menuitem", {
        name: "Split segment at this phase",
        exact: true,
      })
      .click();
    await confirm(page, 72);
  });
});
