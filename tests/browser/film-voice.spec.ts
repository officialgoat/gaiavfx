import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { components } from "../../frontend/src/shared/api.generated";
import {
  EXPRESSIVE_FOLDER,
  filmAsset,
  mockRecast,
  NOT_BUILT,
  notAnalysedDialogue,
  reelAsset,
  voiceFixture,
} from "./recast-fixtures";

// Recast voice P4 (ADR 0016 §2, ADR 0017 §7 and CP3 items 2 and 7): the film's trackbar gains a Voice step after Cast.
// It asks before the film's dialogue analysis starts, as a new film's analysis asks first; a step that cannot run says
// why (D2); the sheet shows the analysis's stage, its line counts or its error with Retry. The page never shows the
// analysis's operation and never reads the lines yet (speaker lanes are U6/P8).
//
// Recast voice P5e (ADR 0016 §1, §4; the user on 23 October): a character's card chooses Library recordings of the
// target voice (journaled), asks before the GPU-hours build, shows its progress, plays the audition and takes the
// approval; the Voice sheet sums each character's voice up. No operation, artifact or folder reaches the page.

test.use({ reducedMotion: "reduce" });

type S = components["schemas"];
type Mock = Awaited<ReturnType<typeof mockRecast>>;

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

const trackbar = (page: Page) => page.getByRole("toolbar", { name: "Recast" });
const step = (page: Page, name: string) =>
  trackbar(page).getByRole("button", { name: new RegExp(`^${name}: `) });
const voice = (page: Page) => step(page, "Voice");
const why = (page: Page) => page.locator("#trackbar-step-reason");
const notice = (page: Page) => page.locator(".film-message.notice");
const renderMenu = (page: Page) =>
  page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Render", exact: true });
const dialogueCalls = (mock: Mock) =>
  mock.calls.filter((c) => /\/dialogue(\/retry)?$/.test(c.path));

/** No version's asset name, operation, artifact or expressive recordings folder reaches the page: text, titles,
 * labels, alt text or values. */
async function expectBlind(page: Page) {
  const leaks = await page.evaluate((folder) => {
    const secrets = [
      "PRIVATE SEED ARM ROUND",
      "hidden-operation",
      "hidden-artifact",
      folder,
    ];
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
  }, EXPRESSIVE_FOLDER);
  expect(leaks).toEqual([]);
}

async function useTheme(page: Page, theme: "dark" | "light") {
  await page.emulateMedia({ colorScheme: theme });
  await page.addInitScript((value) => {
    localStorage.setItem(
      "gaiavfx-preferences",
      JSON.stringify({ state: { theme: value }, version: 1 }),
    );
  }, theme);
}

/** axe's default rule set (WCAG and best practice) on the whole page. */
async function expectAxeClean(page: Page) {
  const audit = await new AxeBuilder({ page }).analyze();
  expect(
    audit.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => n.target.join(" ")),
    })),
  ).toEqual([]);
}

/** The film's dialogue analysis in a state, as the service reports it (the operation id must never show). */
const dialogueIn = (
  state: S["DialogueStatusDTO"]["state"],
  patch: Partial<S["DialogueStatusDTO"]> = {},
): S["DialogueStatusDTO"] => ({
  ...notAnalysedDialogue(),
  operation_id: state === "not_started" ? null : "hidden-operation",
  attempt: state === "not_started" ? 0 : 1,
  state,
  ...patch,
});

/** Twelve lines, three of them confirmed, by character (A25). */
function analysed(mock: Mock) {
  mock.value.dialogue = dialogueIn("succeeded", {
    stage: "done",
    done: 6,
    total: 6,
  });
  mock.value.lines = { segments: 12, proposed: 5, confirmed: 3, rejected: 1 };
  const [mara, elias] = mock.value.characters;
  Object.assign(mara, { lines_proposed: 2, lines_confirmed: 3 });
  Object.assign(elias, { lines_proposed: 3, lines_confirmed: 0 });
}

const SEPARATOR_FAILED =
  "The dialogue separator stopped: its engine is not installed. Set it up in Settings, then retry.";

test("A22: the Voice step after Cast asks first; Cancel and Escape start nothing; Analyse dialogue starts it once", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  await page.goto("/");
  await openFilm(page);
  // After Cast, before Plan (ADR 0017 §7 gains one step).
  await expect(
    trackbar(page).locator(".trackbar-steps > button strong"),
  ).toHaveText([
    "Analyse",
    "Cast",
    "Voice",
    "Plan",
    "Render",
    "Review",
    "Conform",
  ]);
  await expect(voice(page)).toHaveAccessibleName("Voice: not analysed");
  await expect(voice(page)).toContainText("not analysed");
  await expect(voice(page)).not.toHaveAttribute("aria-disabled", "true");
  // Focusable by keyboard, from Cast.
  await step(page, "Cast").focus();
  await page.keyboard.press("Tab");
  await expect(voice(page)).toBeFocused();

  const ask = page.getByRole("dialog", { name: "Analyse the dialogue?" });
  await page.keyboard.press("Enter");
  await expect(ask).toBeVisible();
  await expect(ask).toContainText(filmAsset.name);
  await expect(ask).toContainText(
    "separates the dialogue from the rest of the soundtrack, transcribes it with word times",
  );
  await expect(ask).toContainText(
    "runs on the GPU through the local engines, in the background",
  );
  await expect(ask).toContainText(
    "Nothing in the film changes: the lines it proposes for each character wait for your confirmation.",
  );
  await ask.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(ask).toHaveCount(0);
  await expect(voice(page)).toBeFocused();
  // Escape is a cancel too.
  await voice(page).click();
  await expect(ask).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(ask).toHaveCount(0);
  expect(dialogueCalls(mock)).toEqual([]);

  await voice(page).click();
  await ask
    .getByRole("button", { name: "Analyse dialogue", exact: true })
    .click();
  await expect(ask).toHaveCount(0);
  await expect(notice(page)).toContainText("Dialogue analysis started.");
  await expect(voice(page)).toHaveAccessibleName(
    "Voice: analysing · separate · 2 of 6",
  );
  await expect(voice(page)).toContainText("analysing · separate · 2 of 6");
  expect(dialogueCalls(mock)).toEqual([
    {
      path: "/recast-1/dialogue",
      method: "POST",
      body: { expected_revision: 1 },
    },
  ]);
  // A running analysis opens its progress, which offers no second start.
  await voice(page).click();
  const running = page.getByRole("dialog", { name: "Analysing the dialogue" });
  await expect(running.getByRole("status")).toHaveText(
    "Separating the dialogue · 2 of 6",
  );
  await expect(
    running.getByRole("button", { name: "Analyse dialogue" }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(running).toHaveCount(0);
  expect(dialogueCalls(mock)).toHaveLength(1);
  // The Recast card says the same words as the step.
  await page.getByRole("button", { name: /^Recast\s*(Draft|Final)$/ }).click();
  await expect(
    page
      .locator(".film-properties")
      .locator(
        'xpath=.//h3[normalize-space()="Dialogue"]/following-sibling::p[1]',
      ),
  ).toHaveText("analysing · separate · 2 of 6");
  expect(mock.dialogue.lineReads).toBe(0);
  await expectBlind(page);
});

const GAPS: [string, string][] = [
  ["no_audio", "This film has no audio track."],
  [
    "channels",
    "This film's audio has 6 channels; dialogue analysis supports mono and stereo for now.",
  ],
];
for (const [code, message] of GAPS)
  test(`A23: a film with the ${code} gap disables Voice with its sentence, on the step and in the Render menu`, async ({
    page,
  }) => {
    const mock = await mockRecast(page);
    mock.existing();
    mock.value.dialogue = dialogueIn("not_started", {
      reason: message,
      gap: { code, message },
    });
    if (code === "no_audio") mock.value.film.has_audio = false;
    await page.goto("/");
    await openFilm(page);
    await expect(voice(page)).toHaveAttribute("aria-disabled", "true");
    await expect(voice(page)).toHaveAttribute("title", message);
    await voice(page).focus();
    await expect(why(page)).toHaveText(`Voice: ${message}`);
    await expect(voice(page)).toHaveAttribute(
      "aria-describedby",
      "trackbar-step-reason",
    );
    // A click (forced past the disabled check) says why and opens nothing.
    await page.keyboard.press("Escape");
    await expect(why(page)).toHaveText("");
    await voice(page).click({ force: true });
    await expect(why(page)).toHaveText(`Voice: ${message}`);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await renderMenu(page).click();
    const item = page.getByRole("menuitem", { name: /^Analyse dialogue…/ });
    await expect(item).toHaveAttribute("aria-disabled", "true");
    await expect(item).toContainText(message);
    await item.click({ force: true });
    await expect(
      page.getByRole("dialog", { name: "Analyse the dialogue?" }),
    ).toHaveCount(0);
    await page.keyboard.press("Escape");
    expect(dialogueCalls(mock)).toEqual([]);
    await expectBlind(page);
  });

test("A24: while the film analysis runs, Voice waits for it", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.analysis = {
    ...mock.value.analysis,
    state: "running",
    stage: "faces",
    done: 1,
    total: 3,
  };
  await page.goto("/");
  await openFilm(page);
  await expect(voice(page)).toHaveAttribute("aria-disabled", "true");
  await voice(page).focus();
  await expect(why(page)).toHaveText(
    "Voice: Wait for the film analysis to finish.",
  );
  await renderMenu(page).click();
  await expect(
    page.getByRole("menuitem", { name: /^Analyse dialogue…/ }),
  ).toContainText("Wait for the film analysis to finish.");
  await page.keyboard.press("Escape");
  expect(dialogueCalls(mock)).toEqual([]);
});

test("A25: an analysed film's Voice step counts its lines; the sheet shows them by character and starts nothing", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  analysed(mock);
  await page.goto("/");
  await openFilm(page);
  await expect(voice(page)).toHaveAccessibleName(
    "Voice: 12 lines · 3 confirmed",
  );
  await expect(voice(page)).toContainText("12 lines · 3 confirmed");
  await voice(page).click();
  const sheet = page.getByRole("dialog", { name: "Dialogue analysed" });
  await expect(sheet).toBeVisible();
  await expect(sheet.locator(".voice-status")).toHaveText(
    "12 lines · 5 proposed · 3 confirmed · 1 rejected",
  );
  const rows = sheet.getByRole("listitem");
  // Each row also sums the character's voice up (P5e).
  await expect(rows).toHaveText([
    "Mara 2 proposed · 3 confirmed Voice: no recordings",
    "Elias 3 proposed · 0 confirmed Voice: no recordings",
    "The courier 0 proposed · 0 confirmed Voice: no recordings",
  ]);
  await expect(sheet).toContainText(
    "Speaker lanes and line review arrive with the next increment.",
  );
  await expect(
    sheet.getByRole("button", { name: /Analyse dialogue|Retry/ }),
  ).toHaveCount(0);
  await expectBlind(page);
  await sheet.getByRole("button", { name: "Close", exact: true }).click();
  await expect(sheet).toHaveCount(0);
  expect(dialogueCalls(mock)).toEqual([]);
  expect(mock.dialogue.lineReads).toBe(0);
  await expectBlind(page);
});

test("A26: a failed analysis shows its sentence; Retry starts it again once", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.dialogue = dialogueIn("failed", {
    stage: "separate",
    done: 1,
    total: 3,
    error: SEPARATOR_FAILED,
  });
  await page.goto("/");
  await openFilm(page);
  await expect(voice(page)).toHaveAccessibleName("Voice: failed");
  await expect(voice(page)).not.toHaveAttribute("aria-disabled", "true");
  await voice(page).click();
  const sheet = page.getByRole("dialog", { name: "Dialogue analysis failed" });
  await expect(sheet).toContainText(SEPARATOR_FAILED);
  await sheet.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(sheet).toHaveCount(0);
  await expect(notice(page)).toContainText("Dialogue analysis started again.");
  await expect(voice(page)).toHaveAccessibleName(
    "Voice: analysing · audio · 0 of 3",
  );
  expect(dialogueCalls(mock)).toEqual([
    {
      path: "/recast-1/dialogue/retry",
      method: "POST",
      body: { expected_revision: 1 },
    },
  ]);
  expect(mock.value.dialogue.attempt).toBe(2);
  await expectBlind(page);
});

for (const theme of ["dark", "light"] as const)
  test(`A27: the page passes axe's default rules with the Voice sheet asking and with its lines, ${theme}`, async ({
    page,
  }) => {
    await useTheme(page, theme);
    const mock = await mockRecast(page);
    mock.existing();
    await page.goto("/");
    await openFilm(page);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await voice(page).click();
    const ask = page.getByRole("dialog", { name: "Analyse the dialogue?" });
    await expect(ask).toBeVisible();
    await expectAxeClean(page);
    await page.keyboard.press("Escape");
    analysed(mock);
    await expect(voice(page)).toHaveAccessibleName(
      "Voice: 12 lines · 3 confirmed",
    );
    await voice(page).click();
    const sheet = page.getByRole("dialog", { name: "Dialogue analysed" });
    await expect(sheet.getByRole("listitem")).toHaveCount(3);
    await expectAxeClean(page);
    await expectBlind(page);
  });

test("A27: a stored line decision is the newest step of the film's one undo stack", async ({
  page,
}) => {
  const mock = await mockRecast(page);
  mock.existing();
  mock.value.edits = [
    {
      id: "edit-lines",
      command: "confirm_lines",
      kind: "edit",
      summary: "Confirmed 3 lines for Mara",
      created_at: "2026-10-23T10:00:00Z",
      undone_at: null,
    },
  ];
  await page.goto("/");
  await openFilm(page);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(
    page.getByRole("menuitem", {
      name: /^Undo: Confirmed 3 lines for Mara/,
    }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(step(page, "Cast")).not.toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Control+z");
  await expect
    .poll(() => mock.calls.filter((c) => c.path.endsWith("/undo")))
    .toEqual([
      {
        path: "/recast-1/edits/edit-lines/undo",
        method: "POST",
        body: { expected_revision: 1 },
      },
    ]);
});

// --- P5e: a character's voice ---------------------------------------------------------------------------------------

const card = (page: Page) => page.locator(".film-properties");
/** The card's summary row for the voice. */
const voiceRow = (page: Page) =>
  card(page).locator(
    'xpath=.//dt[normalize-space()="Voice"]/following-sibling::dd[1]',
  );
/** The voice section's state line. */
const voiceStatus = (page: Page) =>
  card(page).locator(".film-card-voice > p[role='status']");
const voiceCalls = (mock: Mock) =>
  mock.calls.filter((c) => /\/voice(\/[a-z]+)?$/.test(c.path));
const MARA = "/recast-1/characters/character-0/voice";

/** Mara's card in Properties. */
async function openMara(page: Page) {
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator('[data-row="cast-character-0"] .cast-header').click();
  await expect(
    card(page).getByRole("heading", { name: "Mara", exact: true }),
  ).toBeVisible();
}

async function openWithVoice(page: Page, voice: S["VoiceDTO"] | null) {
  const mock = await mockRecast(page, 8);
  mock.existing();
  mock.value.characters[0].voice = voice;
  await page.goto("/");
  await openFilm(page);
  await openMara(page);
  return mock;
}

test("A40: a character without a voice chooses Library recordings within 15–60 s; one PUT, journaled with Undo", async ({
  page,
}) => {
  const assetQueries: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/assets")
      assetQueries.push(url.searchParams.get("kind") ?? "");
  });
  const mock = await openWithVoice(page, null);
  await expect(voiceRow(page)).toHaveText("None");
  await expect(
    card(page).getByRole("heading", { name: "Voice", exact: true }),
  ).toBeVisible();
  await expect(card(page).locator(".film-card-voice")).toContainText(
    "No recordings yet.",
  );
  await expect(
    card(page).getByRole("button", { name: "Build voice…" }),
  ).toHaveCount(0);
  // The Library panel lists every kind; from here on, only the dialog's picker asks.
  assetQueries.length = 0;
  await card(page)
    .getByRole("button", { name: "Choose recordings…", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Recordings for Mara's voice",
  });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(
    "Choose 15 to 60 seconds of clean recordings of the target voice: speech without music or other voices.",
  );
  // Audio only: the Library's recordings, with their lengths.
  const picks = dialog.locator(".recast-asset-list button");
  await expect(picks).toHaveCount(3);
  await expect(picks.nth(0)).toHaveAccessibleName("Add mara-take-01.wav, 10 s");
  expect(assetQueries).toContain("audio");
  await expect(dialog).not.toContainText(filmAsset.name);
  const total = dialog.getByRole("status");
  const use = dialog.getByRole("button", {
    name: "Use these recordings",
    exact: true,
  });
  await expect(total).toHaveText("0 s of 15–60 s");
  await expect(use).toBeDisabled();
  await dialog
    .getByRole("button", { name: "Add mara-take-01.wav, 10 s" })
    .click();
  await expect(total).toHaveText("10 s of 15–60 s · 1 recording");
  await expect(use).toBeDisabled();
  // A chosen recording is not offered twice.
  await expect(
    dialog.getByRole("button", { name: "Add mara-take-01.wav, 10 s" }),
  ).toBeDisabled();
  await dialog
    .getByRole("button", { name: "Add mara-take-02.wav, 20 s" })
    .click();
  await expect(total).toHaveText("30 s of 15–60 s · 2 recordings");
  await expect(use).toBeEnabled();
  // Over 60 s is refused before any request; Remove takes one out again.
  await dialog
    .getByRole("button", { name: "Add mara-take-03.wav, 45 s" })
    .click();
  await expect(total).toHaveText("75 s of 15–60 s · 3 recordings");
  await expect(use).toBeDisabled();
  await dialog
    .getByRole("button", { name: "Remove mara-take-03.wav", exact: true })
    .click();
  await expect(
    dialog
      .getByRole("list", { name: "Recordings for Mara's voice" })
      .getByRole("listitem"),
  ).toHaveText(["mara-take-01.wav10 s", "mara-take-02.wav20 s"]);
  await expect(total).toHaveText("30 s of 15–60 s · 2 recordings");
  expect(voiceCalls(mock)).toEqual([]);
  await use.click();
  await expect(dialog).toHaveCount(0);
  expect(voiceCalls(mock)).toEqual([
    {
      path: MARA,
      method: "PUT",
      body: { expected_revision: 1, asset_ids: ["voice-a", "voice-b"] },
    },
  ]);
  await expect(notice(page)).toContainText(
    "Set Mara's voice from 2 recordings (30 s).",
  );
  await expect(voiceRow(page)).toHaveText("2 recordings · 30 s · not built");
  await expect(voiceStatus(page)).toHaveText(NOT_BUILT);
  await expect(
    card(page).getByRole("button", { name: "Build voice…" }),
  ).toBeEnabled();
  await expectBlind(page);
  // Journaled: the notice's Undo takes the recordings back.
  await notice(page).getByRole("button", { name: "Undo", exact: true }).click();
  await expect(notice(page)).toContainText(
    "Undone: Set Mara's voice from 2 recordings (30 s)",
  );
  await expect(voiceRow(page)).toHaveText("None");
  expect(mock.calls.filter((c) => c.path.endsWith("/undo"))).toHaveLength(1);
});

const OUTSIDE =
  "Choose between 15 and 60 seconds of recordings; these are 10 s.";
const PROPOSED = "Confirm the proposed recordings first.";

for (const [name, voice] of [
  [
    "recordings outside 15–60 s",
    voiceFixture({
      samples: [{ asset_id: "voice-a", name: "mara-take-01.wav", seconds: 10 }],
      seconds: 10,
      reason: OUTSIDE,
    }),
  ],
  [
    "proposed recordings",
    voiceFixture({ state: "proposed", proposed_by: "agent", reason: PROPOSED }),
  ],
] as const)
  test(`A41: with ${name}, Build voice… is disabled with the service's reason as its title`, async ({
    page,
  }) => {
    const mock = await openWithVoice(page, voice);
    const build = card(page).getByRole("button", { name: "Build voice…" });
    await expect(build).toBeDisabled();
    await expect(build).toHaveAttribute("title", voice.reason!);
    await expect(voiceStatus(page)).toHaveText(voice.reason!);
    if (voice.state === "proposed") {
      await expect(voiceRow(page)).toHaveText("2 recordings · 30 s · proposed");
      await expect(card(page).locator(".film-card-voice")).toContainText(
        "proposed by the agent",
      );
      // Confirming is a journaled cast edit; then the build can be asked for.
      await card(page)
        .getByRole("button", {
          name: "Confirm recordings for Mara",
          exact: true,
        })
        .click();
      await expect(notice(page)).toContainText(
        "Confirmed Mara's voice recordings.",
      );
      await expect(build).toBeEnabled();
      expect(voiceCalls(mock)).toEqual([
        {
          path: `${MARA}/confirm`,
          method: "POST",
          body: { expected_revision: 1 },
        },
      ]);
    } else expect(voiceCalls(mock)).toEqual([]);
    await expectBlind(page);
  });

test("A41/A42: Build voice… asks first; Cancel and Escape start nothing; Build voice posts once and the card shows its progress", async ({
  page,
}) => {
  const mock = await openWithVoice(page, voiceFixture());
  const build = card(page).getByRole("button", { name: "Build voice…" });
  // "Build the voice to hear an audition." names the next step; it blocks nothing.
  await expect(build).toBeEnabled();
  await build.click();
  const ask = page.getByRole("dialog", { name: "Build Mara's voice?" });
  await expect(ask).toBeVisible();
  // The user's ask (23 October): the GPU hours, said first.
  await expect(ask).toHaveAccessibleDescription(
    "It takes about 2.5 to 3.5 GPU hours, in the background.",
  );
  await expect(ask).toContainText(
    "It grows Mara’s 2 recordings (30 s) into a training set with the local voice generators, then trains a voice from it.",
  );
  await expect(ask).toContainText(
    "the recast keeps working meanwhile, and the build’s progress shows on Mara’s card.",
  );
  await expect(ask).toContainText(
    "Up to eight of Mara’s confirmed lines then come back in the new voice as an audition for your approval.",
  );
  await expect(ask).toContainText(
    "Nothing in the film converts until you approve the voice.",
  );
  // A42: without a folder, the generators make the screams.
  await expect(ask).toContainText(
    "No expressive recordings folder is set: screams and cries come from the generators. Choose a folder in Settings.",
  );
  await expect(ask).not.toContainText("changed since this voice was built");
  await ask.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(ask).toHaveCount(0);
  await build.click();
  await expect(ask).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(ask).toHaveCount(0);
  expect(voiceCalls(mock)).toEqual([]);
  await build.click();
  await ask.getByRole("button", { name: "Build voice", exact: true }).click();
  await expect(ask).toHaveCount(0);
  await expect(notice(page)).toContainText("Voice build started.");
  expect(voiceCalls(mock)).toEqual([
    {
      path: `${MARA}/build`,
      method: "POST",
      body: { expected_revision: 1, rebuild: false },
    },
  ]);
  // A43: the build's stage and counts, in the card's state line and summary.
  await expect(voiceStatus(page)).toHaveText("Generating material · 3 of 27");
  await expect(voiceRow(page)).toHaveText(
    "building · generating material · 3 of 27",
  );
  await expect(build).toHaveCount(0);
  await expectBlind(page);
});

test("A41/A42: a voice built before the expressive recordings changed builds again with rebuild", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  Object.assign(mock.voiceLibrary, {
    folder: EXPRESSIVE_FOLDER,
    state: "ready",
    clips: 16,
    categories: { scream: 12, sob: 4 },
    skipped: 1,
  });
  mock.existing();
  mock.value.characters[0].voice = voiceFixture({
    model_state: "waiting",
    reason: null,
    audition_asset_id: reelAsset.id,
    library_changed: true,
  });
  await page.goto("/");
  await openFilm(page);
  await openMara(page);
  await expect(card(page).locator(".film-card-voice")).toContainText(
    "The expressive recordings changed since this voice was built.",
  );
  await card(page).getByRole("button", { name: "Build voice…" }).click();
  const ask = page.getByRole("dialog", { name: "Build Mara's voice?" });
  await expect(ask).toContainText(
    "Expressive recordings: 16 clips in 2 categories",
  );
  await expect(ask).toContainText(
    "The expressive recordings changed since this voice was built; building again uses them.",
  );
  await expectBlind(page);
  await ask.getByRole("button", { name: "Build voice", exact: true }).click();
  await expect(notice(page)).toContainText("Voice build started.");
  expect(voiceCalls(mock)).toEqual([
    {
      path: `${MARA}/build`,
      method: "POST",
      body: { expected_revision: 1, rebuild: true },
    },
  ]);
  await expectBlind(page);
});

test("A43: a building voice shows its stage on the card and in the Voice sheet; a failed one says why and Retry posts once", async ({
  page,
}) => {
  const mock = await mockRecast(page, 8);
  mock.existing();
  analysed(mock);
  mock.value.characters[0].voice = voiceFixture({
    model_state: "making",
    stage: "material",
    done: 3,
    total: 27,
    reason: null,
  });
  await page.goto("/");
  await openFilm(page);
  await openMara(page);
  await expect(voiceStatus(page)).toHaveText("Generating material · 3 of 27");
  await voice(page).click();
  const sheet = page.getByRole("dialog", { name: "Dialogue analysed" });
  const rows = sheet.getByRole("listitem");
  await expect(rows.filter({ hasText: "Mara" })).toContainText(
    "Voice: building · generating material · 3 of 27",
  );
  await expect(rows.filter({ hasText: "Elias" })).toContainText(
    "Voice: no recordings",
  );
  await expectBlind(page);
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  const failed =
    "The voice build failed while training the voice. Retry; if it fails again, the app log has the details.";
  mock.value.characters[0].voice = voiceFixture({
    model_state: "failed",
    stage: "train",
    done: 14,
    total: 27,
    error: failed,
    reason: null,
  });
  await expect(voiceStatus(page)).toHaveText(failed);
  await expect(voiceRow(page)).toHaveText("failed");
  await expectBlind(page);
  await card(page).getByRole("button", { name: "Retry", exact: true }).click();
  await expect(voiceStatus(page)).toHaveText("Generating material · 3 of 27");
  expect(voiceCalls(mock)).toEqual([
    {
      path: `${MARA}/retry`,
      method: "POST",
      body: { expected_revision: 1 },
    },
  ]);
});

for (const action of ["Approve", "Reject"] as const)
  test(`A44: a waiting audition plays its reel; ${action} voice is a single human request`, async ({
    page,
  }) => {
    const mock = await openWithVoice(
      page,
      voiceFixture({
        model_state: "waiting",
        reason: null,
        audition_asset_id: reelAsset.id,
      }),
    );
    await expect(voiceStatus(page)).toHaveText("Waiting for your review");
    await expect(voiceRow(page)).toHaveText("audition ready");
    const player = card(page).getByLabel("Play audition.wav", { exact: true });
    await expect(player).toBeVisible();
    await expect(player).toHaveJSProperty("tagName", "AUDIO");
    await expect(
      card(page).getByRole("button", {
        name: "Approve voice for Mara",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      card(page).getByRole("button", {
        name: "Reject voice for Mara",
        exact: true,
      }),
    ).toBeVisible();
    await expectBlind(page);
    await card(page)
      .getByRole("button", { name: `${action} voice for Mara`, exact: true })
      .click();
    expect(voiceCalls(mock)).toEqual([
      {
        path: `${MARA}/${action.toLowerCase()}`,
        method: "POST",
        body: { expected_revision: 1 },
      },
    ]);
    await expect(
      card(page).getByRole("button", { name: /voice for Mara/ }),
    ).toHaveCount(0);
    if (action === "Approve") {
      await expect(voiceStatus(page)).toHaveText(
        "Approved: confirmed lines will convert in the next increment",
      );
      await expect(voiceRow(page)).toHaveText("approved");
      await expect(
        card(page).getByRole("button", { name: /Retry/ }),
      ).toHaveCount(0);
    } else {
      await expect(voiceStatus(page)).toHaveText("Rejected");
      await expect(voiceRow(page)).toHaveText("rejected");
      // Retry audition hears the voice again with the lines confirmed now.
      await card(page)
        .getByRole("button", { name: "Retry audition", exact: true })
        .click();
      await expect(voiceStatus(page)).toHaveText(
        "Converting the audition · 0 of 3",
      );
      expect(voiceCalls(mock).at(-1)).toEqual({
        path: `${MARA}/retry`,
        method: "POST",
        body: { expected_revision: 2 },
      });
    }
    await expectBlind(page);
  });

test("A45: Clear removes confirmed recordings as a journaled edit; the card never shows an operation, artifact or folder", async ({
  page,
}) => {
  const mock = await openWithVoice(page, voiceFixture());
  await expect(
    card(page)
      .getByRole("list", { name: "Recordings for Mara's voice" })
      .getByRole("listitem"),
  ).toHaveText(["mara-take-02.wav20 s", "mara-take-01.wav10 s"]);
  await expectBlind(page);
  await card(page)
    .getByRole("button", {
      name: "Clear voice recordings for Mara",
      exact: true,
    })
    .click();
  await expect(notice(page)).toContainText("Cleared Mara's voice recordings.");
  await expect(voiceRow(page)).toHaveText("None");
  expect(voiceCalls(mock)).toEqual([
    { path: MARA, method: "DELETE", body: {} },
  ]);
  await expectBlind(page);
});

for (const theme of ["dark", "light"] as const)
  test(`A46: axe passes on the character card's voice and on both voice sheets, ${theme}`, async ({
    page,
  }) => {
    await useTheme(page, theme);
    const mock = await mockRecast(page, 8);
    mock.existing();
    Object.assign(mock.voiceLibrary, {
      folder: EXPRESSIVE_FOLDER,
      state: "ready",
      clips: 16,
      categories: { scream: 12, sob: 4 },
      skipped: 0,
    });
    mock.value.characters[0].voice = voiceFixture({
      model_state: "waiting",
      reason: null,
      audition_asset_id: reelAsset.id,
      library_changed: true,
    });
    await page.goto("/");
    await openFilm(page);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await openMara(page);
    await expect(card(page).getByLabel("Play audition.wav")).toBeVisible();
    await expectAxeClean(page);
    await card(page)
      .getByRole("button", { name: "Change recordings…", exact: true })
      .click();
    const samples = page.getByRole("dialog", {
      name: "Recordings for Mara's voice",
    });
    await expect(samples.locator(".recast-asset-list button")).toHaveCount(3);
    await expectAxeClean(page);
    await page.keyboard.press("Escape");
    await expect(samples).toHaveCount(0);
    await card(page).getByRole("button", { name: "Build voice…" }).click();
    const ask = page.getByRole("dialog", { name: "Build Mara's voice?" });
    await expect(ask).toContainText("16 clips in 2 categories");
    await expectAxeClean(page);
    await expectBlind(page);
    await page.keyboard.press("Escape");
    expect(voiceCalls(mock)).toEqual([]);
  });
