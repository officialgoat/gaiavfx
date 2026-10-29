import { test, expect, type Page } from "@playwright/test";
import type { components } from "../../frontend/src/shared/api.generated";
import { filmAsset, mockRecast } from "./recast-fixtures";

// Usability round 2 (retained regression coverage C1, C11, C12, T6-F1, T2-F7): a film's
// Jobs read as sentences without ids or paths, a refused conform says what to do, a conformed film's history reads in
// words, the Cast shows faces large enough to tell apart, and a face check says when it waits for ComfyUI.

type Mock = Awaited<ReturnType<typeof mockRecast>>;
type S = components["schemas"];

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

/** The fixture film, open, after `before` has set up the mock and any extra routes. */
async function start(page: Page, before?: (mock: Mock) => unknown) {
  const mock = await mockRecast(page);
  mock.existing();
  await before?.(mock);
  await page.goto("/");
  await openFilm(page);
  return mock;
}

const card = (page: Page) => page.locator(".film-properties");
const panel = (page: Page) => page.locator(".film-project");

async function selectCharacter(page: Page, id: string) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(`[data-row="cast-${id}"] .cast-header`).click();
}

async function openConform(page: Page) {
  await page
    .getByRole("toolbar", { name: "Recast" })
    .getByRole("button", { name: /^Conform: / })
    .click();
  return page.getByRole("dialog", { name: "Conform film" });
}

const operation = (
  id: string,
  kind: string,
  state: string,
  error: string | null = null,
): S["OperationDTO"] => ({
  id,
  kind,
  state,
  awaits_review: false,
  request: {},
  budget_reserved: 0,
  budget_released_at: null,
  cancel_requested: false,
  review: null,
  error,
  created_at: "2026-10-22T10:00:00Z",
  updated_at: "2026-10-22T10:05:00Z",
  jobs: [],
  candidates: [],
});

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

test("C1: a film's Jobs read errors as sentences, hide a render's method, and offer cancel only while running", async ({
  page,
}) => {
  const ffmpeg =
    "0f3e2d1c-1111-4222-8333-444455556666: ffmpeg failed: [AVFilterGraph @ 00000186598de140] Error initializing filters\n" +
    "X:\\TestLibrary\\library\\staging\\operations\\a1\\proxy.mp4: Cannot allocate memory; " +
    "1a2b3c4d-1111-4222-8333-444455556666: Input job 0f3e2d1c-1111-4222-8333-444455556666 ended failed; this job did not run.";
  const refused =
    "474a383d-1111-4222-8333-444455556666: [WinError 10061] No connection could be made because the target machine actively refused it";
  const long =
    "5b6c7d8e-1111-4222-8333-444455556666: The voice model at /opt/gaiavfx/engines/seed-vc/checkpoints/model.pth could not be " +
    "loaded because the checkpoint is truncated or was written by a newer engine than the one installed here and now. Reinstall it.";
  await start(page, () =>
    page.route("**/api/production/operations?**", (route) =>
      route.fulfill({
        json: [
          operation("op-analysis", "film_analysis", "partial", ffmpeg),
          operation("op-face", "face_check", "failed", refused),
          operation("op-voice", "voice_conversion", "failed", long),
          operation(
            "op-assembly",
            "shot_assembly",
            "failed",
            "seed 81723 · PRIVATE SEED ARM ROUND · X:\\x\\composite.mkv",
          ),
          operation("op-window", "character_replacement", "succeeded"),
          operation("op-running", "film_analysis", "running"),
        ],
      }),
    ),
  );
  await page.getByRole("tab", { name: "Jobs" }).click();
  const list = page.locator(".dock-scroll .task-list");
  const jobs = list.locator(".task-item");
  await expect(jobs).toHaveCount(6);
  const [analysis, face, voice, assembly, window, running] = [
    0, 1, 2, 3, 4, 5,
  ].map((i) => jobs.nth(i));
  // Errors read as one sentence: no job ids, FFmpeg tags or local paths.
  await expect(analysis.locator(".error-text")).toHaveText(
    "ffmpeg failed: Error initializing filters.",
  );
  await expect(face.locator(".error-text")).toHaveText(
    "ComfyUI is not reachable. Start it, or check its address in Settings.",
  );
  const sentence = (await voice.locator(".error-text").textContent()) ?? "";
  expect(sentence).toMatch(/^The voice model at could not be loaded .*…$/);
  expect(sentence.length).toBeLessThanOrEqual(141);
  const shown = await list.innerText();
  expect(shown).not.toMatch(UUID);
  expect(shown).not.toMatch(/[A-Za-z]:\\|\/opt\/|WinError|AVFilterGraph/);
  // The raw text waits behind a disclosure.
  await expect(analysis.locator("pre")).toBeHidden();
  await analysis.getByText("Technical details").click();
  await expect(analysis.locator("pre")).toBeVisible();
  await expect(analysis.locator("pre")).toContainText("Cannot allocate memory");
  await expect(analysis.locator("pre")).toContainText(
    "0f3e2d1c-1111-4222-8333-444455556666",
  );
  // A render reads "Recast render" whatever its method, keeps its error hidden and offers no review.
  for (const render of [assembly, window]) {
    await expect(render.getByRole("heading", { level: 3 })).toHaveText(
      "Recast render",
    );
    await expect(render.getByText("Technical details")).toHaveCount(0);
    await expect(
      render.getByRole("button", { name: "Open review" }),
    ).toHaveCount(0);
  }
  await expect(assembly.locator(".error-text")).toHaveText(
    "This render failed. Its details stay hidden while a film is open, so its versions stay blind.",
  );
  await expect(list).not.toContainText(/shot assembly|character replacement/);
  // Only an operation still running can be cancelled.
  for (const settled of [analysis, face, voice, assembly, window])
    await expect(
      settled.getByRole("button", { name: "Cancel operation" }),
    ).toHaveCount(0);
  await expect(
    running.getByRole("button", { name: "Cancel operation" }),
  ).toHaveCount(1);
  await expectBlind(page);
});

test("C12: a chosen conform with nothing chosen says why, and the refusal clears when a setting changes", async ({
  page,
}) => {
  const sent: Record<string, unknown>[] = [];
  const refusal =
    "Nothing to conform: no shot has a chosen version, so the film would be the original. Choose versions in Review, or conform a preview with unreviewed drafts.";
  await start(page, () =>
    page.route("**/api/recasts/recast-1/conform", (route) => {
      sent.push(route.request().postDataJSON());
      return route.fulfill({ status: 409, json: { detail: refusal } });
    }),
  );
  const dialog = await openConform(page);
  const conform = dialog.getByRole("button", {
    name: "Conform film",
    exact: true,
  });
  const alert = dialog.getByRole("alert");
  await conform.click();
  await expect(alert).toHaveText(refusal);
  expect(sent.at(-1)).toMatchObject({
    mode: "chosen",
    codec: "h264",
    range: null,
  });
  // Changing what to conform answers the refusal: it leaves.
  await dialog.getByLabel("Versions").selectOption("preview_unreviewed");
  await expect(alert).toHaveCount(0);
  await conform.click();
  await expect(alert).toHaveText(refusal);
  expect(sent.at(-1)).toMatchObject({ mode: "preview_unreviewed" });
  await dialog.getByLabel("Codec").selectOption("prores");
  await expect(alert).toHaveCount(0);
  expect(sent).toHaveLength(2);
  await expectBlind(page);
});

test("T6-F1: a conformed film's Library history reads as a summary, without ids", async ({
  page,
}) => {
  const mock = await start(page, () =>
    page.route("**/api/v1/assets/conformed-film", (route) =>
      route.fulfill({
        json: {
          ...filmAsset,
          id: "conformed-film",
          name: "The crossing - unreviewed preview - 00.00.02-00.00.08",
          provenance: [
            {
              type: "recast_conform",
              recast_id: "recast-1",
              recast_revision: 7,
              mode: "preview_unreviewed",
              choices: [
                {
                  shot_id: "s0002",
                  version: "version-1-B",
                  patch_artifact: "patch-artifact-1",
                },
                {
                  shot_id: "s0003",
                  version: "version-2-A",
                  patch_artifact: "patch-artifact-2",
                },
              ],
              source_asset: filmAsset.id,
              range: { start: 50, end: 200 },
              audio: "pcm_trimmed",
              video: "reencoded_prores_hq",
              operation_id: "conform-operation-1",
            },
          ],
        } satisfies S["AssetDTO"],
      }),
    ),
  );
  mock.value.conforms.push({
    operation_id: "conform-1",
    mode: "preview_unreviewed",
    range: { start: 50, end: 200 },
    state: "succeeded",
    progress: 1,
    asset_id: "conformed-film",
    created_at: "2026-10-22T10:00:00Z",
    codec: "prores",
    error: null,
  });
  mock.value.revision++;
  const dialog = await openConform(page);
  await dialog.getByRole("button", { name: "Open result in Library" }).click();
  const history = page
    .getByRole("dialog", { name: /unreviewed preview/ })
    .getByRole("region", { name: "Media provenance" });
  await expect(history).toContainText("Conformed from a recast");
  await expect(history).toContainText("Unreviewed preview");
  // The range in the film's own timecode, at its 25 fps.
  await expect(history).toContainText("00:00:02:00–00:00:08:00");
  await expect(history).toContainText("2 shots use a version");
  await expect(history).toContainText("ProRes HQ");
  await expect(history).toContainText("Trimmed original audio");
  const text = await history.innerText();
  for (const raw of [
    "recast_conform",
    "recast-1",
    "version-",
    "patch-artifact",
    "conform-operation",
    "source asset",
    "{",
  ])
    expect(text).not.toContain(raw);
  await expectBlind(page);
});

test("C11: Cast rows and Same person? pairs show faces, and track boxes name their shot and frames", async ({
  page,
}) => {
  const mock = await start(page, (mock) => {
    // Elias has no face crop yet: his row keeps the generic icon.
    mock.value.characters[1].face = null;
  });
  await panel(page).getByRole("button", { name: /^Cast/ }).click();
  const rows = panel(page).locator(".project-record");
  const mara = rows.filter({ hasText: "Mara" });
  const face = mara.locator("img.project-record-face");
  await expect(face).toHaveAttribute("alt", "");
  await expect(face).toHaveAttribute("src", mock.value.characters[0].face!.url);
  const box = (await face.boundingBox())!;
  expect(box.width).toBeGreaterThanOrEqual(30);
  expect(box.height).toBeGreaterThanOrEqual(30);
  const elias = rows.filter({ hasText: "Elias" });
  await expect(elias.locator("img")).toHaveCount(0);
  await expect(elias.locator("svg")).toHaveCount(1);
  // The pair's faces are large enough to judge.
  const pair = panel(page)
    .getByRole("list", { name: "Same person suggestions" })
    .getByRole("listitem")
    .first();
  for (const img of await pair.locator(".film-same-faces img").all()) {
    const size = (await img.boundingBox())!;
    expect(size.width).toBeGreaterThanOrEqual(34);
    expect(size.height).toBeGreaterThanOrEqual(34);
  }
  // The track drawer: thumbnails about 72 px wide, and boxes named by shot and frames.
  await selectCharacter(page, "character-0");
  await card(page).getByRole("button", { name: "Face tracks…" }).click();
  const drawer = page.getByRole("dialog", { name: "Mara · tracks" });
  const first = drawer.getByRole("checkbox", {
    name: "Shot 001, 40 frames seen",
    exact: true,
  });
  await expect(first).toBeVisible();
  const thumbnail = (await drawer
    .getByAltText("Face track")
    .first()
    .boundingBox())!;
  expect(thumbnail.width).toBeGreaterThanOrEqual(70);
  await first.check();
  await expect(
    drawer.getByRole("button", { name: "Move 1 track" }),
  ).toBeEnabled();
  // A track no character holds names its shot and that its identity is unknown.
  await page.keyboard.press("Escape");
  await panel(page)
    .getByRole("button", { name: /^Unknown faces/ })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Unknown faces" }).getByRole("checkbox", {
      name: "Shot 003, unknown identity",
      exact: true,
    }),
  ).toBeVisible();
  await expectBlind(page);
});

test("T2-F7: a face check says it waits for ComfyUI while ComfyUI is not reachable", async ({
  page,
}) => {
  const mock = await start(page, (mock) => {
    mock.value.characters[0].target!.face = "checking";
  });
  await selectCharacter(page, "character-0");
  const mapping = card(page).locator(".film-card-mapping");
  await expect(mapping).toContainText("· checking the face");
  mock.comfy.connected = false;
  await page.reload();
  await expect(page.locator(".timeline-heading")).toContainText(filmAsset.name);
  await selectCharacter(page, "character-0");
  await expect(mapping).toContainText("· the face check waits for ComfyUI");
  await expect(mapping).not.toContainText("checking the face");
  await expect(page.locator(".comfy-offline")).toBeVisible();
  await expectBlind(page);
});
