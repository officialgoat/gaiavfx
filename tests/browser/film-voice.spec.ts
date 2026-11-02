import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  card,
  expect,
  expectBlind as expectNoVersionNames,
  laneHeader,
  notice,
  openFilm,
  recastCommands,
  test,
  type RecastWorld,
} from "./recast-world";

// Recast voice P4 (ADR 0016 §2, ADR 0017 §7 and CP3 items 2 and 7): the film's trackbar gains a Voice step after Cast.
// It asks before the film's dialogue analysis starts, as a new film's analysis asks first; a step that cannot run says
// why (D2); the sheet shows the analysis's stage, its line counts or its error with Retry. The page never shows the
// analysis's operation and never reads the lines yet (speaker lanes are U6/P8).
//
// Recast voice P5e (ADR 0016 §1, §4; the user on 23 October): a character's card chooses Library recordings of the
// target voice (journaled), asks before the GPU-hours build, shows its progress, plays the audition and takes the
// approval; the Voice sheet sums each character's voice up. No operation, artifact or folder reaches the page.
//
// The recast is the service's own (tests/browser_recast.py): its dialogue analysis, line counts, voices and their
// builds are Python's, and the analysis and builds wait until a spec settles them.

test.use({ reducedMotion: "reduce" });
/** Mara (the user's picture), Elias and The courier take 2 s shots in turn; every scene below starts from them. */
const CAST = { characters: ["Mara", "Elias", "The courier"] };
test.use({ scene: CAST });

const trackbar = (page: Page) => page.getByRole("toolbar", { name: "Recast" });
const step = (page: Page, name: string) =>
  trackbar(page).getByRole("button", { name: new RegExp(`^${name}: `) });
const voice = (page: Page) => step(page, "Voice");
const why = (page: Page) => page.locator("#trackbar-step-reason");
const renderMenu = (page: Page) =>
  page
    .getByRole("navigation", { name: "Application menus" })
    .getByRole("button", { name: "Render", exact: true });

const commands = new WeakMap<Page, ReturnType<typeof recastCommands>>();
const callsOf = (page: Page) => commands.get(page)!;
const dialogueCalls = (page: Page) =>
  callsOf(page).filter((c) => /\/dialogue(\/retry)?$/.test(c.path));
const voiceCalls = (page: Page) =>
  callsOf(page).filter((c) => /\/voice(\/[a-z]+)?$/.test(c.path));
/** The page's reads of the film's lines: none yet (speaker lanes are U6/P8). */
const lineReads = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }) => {
  commands.set(page, recastCommands(page));
  const reads: string[] = [];
  lineReads.set(page, reads);
  page.on("request", (request) => {
    if (request.method() === "GET" && /\/lines(\?|$)/.test(request.url()))
      reads.push(request.url());
  });
});

async function start(page: Page, world: RecastWorld) {
  await page.goto("/");
  await openFilm(page, world);
}

/** No version's asset name, operation, artifact or expressive recordings folder reaches the page: the world's fakes
 * name their artifacts `hidden-…`, and its folders lie under `.cache/recast-world`. */
async function expectBlind(page: Page, world: RecastWorld) {
  const { dialogue } = await world.read();
  await expectNoVersionNames(page, [
    "hidden-",
    "recast-world",
    ...(dialogue.operation_id ? [dialogue.operation_id] : []),
  ]);
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

const plural = (count: number, word: string) =>
  `${count} ${count === 1 ? word : `${word}s`}`;

/** The Voice step's words for the film's dialogue as the service reads it now. */
async function dialogueWords(world: RecastWorld) {
  const { dialogue, lines } = await world.read();
  if (dialogue.state === "succeeded")
    return `${plural(lines.segments, "line")} · ${lines.confirmed} confirmed`;
  return ["analysing", dialogue.stage, `${dialogue.done} of ${dialogue.total}`]
    .filter(Boolean)
    .join(" · ");
}

test("A22: the Voice step after Cast asks first; Cancel and Escape start nothing; Analyse dialogue starts it once", async ({
  page,
  world,
}) => {
  await start(page, world);
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
  await expect(ask).toContainText(world.title);
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
  expect(dialogueCalls(page)).toEqual([]);

  const revision = (await world.read()).revision;
  await voice(page).click();
  await ask
    .getByRole("button", { name: "Analyse dialogue", exact: true })
    .click();
  await expect(ask).toHaveCount(0);
  await expect(notice(page)).toContainText("Dialogue analysis started.");
  const words = await dialogueWords(world);
  expect(words).toMatch(/^analysing · /);
  await expect(voice(page)).toHaveAccessibleName(`Voice: ${words}`);
  await expect(voice(page)).toContainText(words);
  expect(dialogueCalls(page)).toEqual([
    {
      path: `/${world.id}/dialogue`,
      method: "POST",
      body: { expected_revision: revision },
    },
  ]);
  // A running analysis opens its progress, which offers no second start.
  await voice(page).click();
  const running = page.getByRole("dialog", { name: "Analysing the dialogue" });
  const { dialogue } = await world.read();
  await expect(running.getByRole("status")).toContainText(
    `${dialogue.done} of ${dialogue.total}`,
  );
  await expect(
    running.getByRole("button", { name: "Analyse dialogue" }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(running).toHaveCount(0);
  expect(dialogueCalls(page)).toHaveLength(1);
  // The Recast card says the same words as the step.
  await page.getByRole("button", { name: /^Recast\s*(Draft|Final)$/ }).click();
  await expect(
    card(page).locator(
      'xpath=.//h3[normalize-space()="Dialogue"]/following-sibling::p[1]',
    ),
  ).toHaveText(words);
  expect(lineReads.get(page)).toEqual([]);
  await expectBlind(page, world);
});

const GAPS = [
  ["none", "no_audio", "This film has no audio track."],
  [
    "six",
    "channels",
    "This film's audio has 6 channels; dialogue analysis supports mono and stereo for now.",
  ],
] as const;
for (const [audio, code, message] of GAPS)
  test.describe(`a film with ${audio === "none" ? "no audio" : "six audio channels"}`, () => {
    test.use({ scene: { ...CAST, audio } });
    test(`A23: a film with the ${code} gap disables Voice with its sentence, on the step and in the Render menu`, async ({
      page,
      world,
    }) => {
      await start(page, world);
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
      expect(dialogueCalls(page)).toEqual([]);
      await expectBlind(page, world);
    });
  });

test.describe("the film's analysis is still running", () => {
  test.use({ scene: { ...CAST, analysed: false } });

  test("A24: while the film analysis runs, Voice waits for it", async ({
    page,
    world,
  }) => {
    await start(page, world);
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
    expect(dialogueCalls(page)).toEqual([]);
  });
});

test.describe("the dialogue is analysed and Mara's proposed lines are confirmed", () => {
  test.use({
    scene: {
      ...CAST,
      dialogue: "succeeded",
      confirmed: [0],
    },
  });

  test("A25: an analysed film's Voice step counts its lines; the sheet shows them by character and starts nothing", async ({
    page,
    world,
  }) => {
    const { lines, characters } = await world.read();
    expect(lines.confirmed).toBeGreaterThan(0);
    await start(page, world);
    const words = await dialogueWords(world);
    await expect(voice(page)).toHaveAccessibleName(`Voice: ${words}`);
    await expect(voice(page)).toContainText(words);
    await voice(page).click();
    const sheet = page.getByRole("dialog", { name: "Dialogue analysed" });
    await expect(sheet).toBeVisible();
    // The user rejected no line, so the counts leave rejections out.
    expect(lines.rejected).toBe(0);
    await expect(sheet.locator(".voice-status")).toHaveText(
      `${plural(lines.segments, "line")} · ${lines.proposed} proposed · ${lines.confirmed} confirmed`,
    );
    // Each row also sums the character's voice up (P5e).
    await expect(sheet.getByRole("listitem")).toHaveText(
      characters.map(
        (c) =>
          `${c.label} ${c.lines_proposed} proposed · ${c.lines_confirmed} confirmed Voice: no recordings`,
      ),
    );
    await expect(sheet).toContainText(
      "Speaker lanes and line review arrive with the next increment.",
    );
    await expect(
      sheet.getByRole("button", { name: /Analyse dialogue|Retry/ }),
    ).toHaveCount(0);
    await expectBlind(page, world);
    await sheet.getByRole("button", { name: "Close", exact: true }).click();
    await expect(sheet).toHaveCount(0);
    expect(dialogueCalls(page)).toEqual([]);
    expect(lineReads.get(page)).toEqual([]);
    await expectBlind(page, world);
  });

  test("A27: a stored line decision is the newest step of the film's one undo stack", async ({
    page,
    world,
  }) => {
    const { edits, revision } = await world.read();
    const decision = edits![0];
    expect(decision.command).toBe("confirm_lines");
    await start(page, world);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(
      page.getByRole("menuitem", {
        name: new RegExp(`^Undo: ${decision.summary}`),
      }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(step(page, "Cast")).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await page.keyboard.press("Control+z");
    await expect
      .poll(() => callsOf(page).filter((c) => c.path.endsWith("/undo")))
      .toEqual([
        {
          path: `/${world.id}/edits/${decision.id}/undo`,
          method: "POST",
          body: { expected_revision: revision },
        },
      ]);
  });
});

test.describe("the dialogue analysis failed", () => {
  test.use({ scene: { ...CAST, dialogue: "failed" } });

  test("A26: a failed analysis shows its sentence; Retry starts it again once", async ({
    page,
    world,
  }) => {
    const { dialogue, revision } = await world.read();
    expect(dialogue.error).toBeTruthy();
    await start(page, world);
    await expect(voice(page)).toHaveAccessibleName("Voice: failed");
    await expect(voice(page)).not.toHaveAttribute("aria-disabled", "true");
    await voice(page).click();
    const sheet = page.getByRole("dialog", {
      name: "Dialogue analysis failed",
    });
    await expect(sheet).toContainText(dialogue.error!);
    await sheet.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(sheet).toHaveCount(0);
    await expect(notice(page)).toContainText(
      "Dialogue analysis started again.",
    );
    await expect(voice(page)).toHaveAccessibleName(
      `Voice: ${await dialogueWords(world)}`,
    );
    expect(dialogueCalls(page)).toEqual([
      {
        path: `/${world.id}/dialogue/retry`,
        method: "POST",
        body: { expected_revision: revision },
      },
    ]);
    expect((await world.read()).dialogue.attempt).toBe(2);
    await expectBlind(page, world);
  });
});

for (const theme of ["dark", "light"] as const)
  test(`A27: the page passes axe's default rules with the Voice sheet asking and with its lines, ${theme}`, async ({
    page,
    world,
  }) => {
    await useTheme(page, theme);
    await start(page, world);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await voice(page).click();
    const ask = page.getByRole("dialog", { name: "Analyse the dialogue?" });
    await expect(ask).toBeVisible();
    await expectAxeClean(page);
    await page.keyboard.press("Escape");
    // Another client starts the analysis, and it finishes.
    await world.elsewhere("POST", "/dialogue");
    await world.settle("dialogue_analysis");
    await expect(voice(page)).toHaveAccessibleName(
      `Voice: ${await dialogueWords(world)}`,
    );
    await voice(page).click();
    const sheet = page.getByRole("dialog", { name: "Dialogue analysed" });
    await expect(sheet.getByRole("listitem")).toHaveCount(3);
    await expectAxeClean(page);
    await expectBlind(page, world);
  });

// --- P5e: a character's voice ---------------------------------------------------------------------------------------

/** The card's summary row for the voice. */
const voiceRow = (page: Page) =>
  card(page).locator(
    'xpath=.//dt[normalize-space()="Voice"]/following-sibling::dd[1]',
  );
/** The voice section's state line. */
const voiceStatus = (page: Page) =>
  card(page).locator(".film-card-voice > p[role='status']");
const mara = (world: RecastWorld) =>
  `/${world.id}/characters/${world.characters[0]}/voice`;
const NOT_BUILT = "Build the voice to hear an audition.";
const TRAINING =
  "The voice build failed while training the voice. Retry; if it fails again, the app log has the details.";

/** The world's film, open on Mara's card in Properties. */
async function openMara(page: Page, world: RecastWorld) {
  await start(page, world);
  await page.getByRole("tab", { name: "Properties" }).click();
  await page.locator(laneHeader(world.characters[0])).click();
  await expect(
    card(page).getByRole("heading", { name: "Mara", exact: true }),
  ).toBeVisible();
}

/** Mara's takes, in the Library: 10, 20 and 45 s. */
const TAKES = [
  { name: "mara-take-01", seconds: 10 },
  { name: "mara-take-02", seconds: 20 },
  { name: "mara-take-03", seconds: 45 },
];

test.describe("Mara's takes are in the Library", () => {
  test.use({ scene: { ...CAST, recordings: TAKES } });

  test("A40: a character without a voice chooses Library recordings within 15–60 s; one PUT, journaled with Undo", async ({
    page,
    world,
  }) => {
    const [one, two, three] = world.recordings;
    const assetQueries: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/v1/assets")
        assetQueries.push(url.searchParams.get("kind") ?? "");
    });
    await openMara(page, world);
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
    const add = (name: string, seconds: number) =>
      dialog.getByRole("button", {
        name: `Add ${name}, ${seconds} s`,
        exact: true,
      });
    await expect(add(one.name, 10)).toBeVisible();
    expect(assetQueries).toContain("audio");
    await expect(dialog).not.toContainText(world.film.name);
    const total = dialog.getByRole("status");
    const use = dialog.getByRole("button", {
      name: "Use these recordings",
      exact: true,
    });
    await expect(total).toHaveText("0 s of 15–60 s");
    await expect(use).toBeDisabled();
    await add(one.name, 10).click();
    await expect(total).toHaveText("10 s of 15–60 s · 1 recording");
    await expect(use).toBeDisabled();
    // A chosen recording is not offered twice.
    await expect(add(one.name, 10)).toBeDisabled();
    await add(two.name, 20).click();
    await expect(total).toHaveText("30 s of 15–60 s · 2 recordings");
    await expect(use).toBeEnabled();
    // Over 60 s is refused before any request; Remove takes one out again.
    await add(three.name, 45).click();
    await expect(total).toHaveText("75 s of 15–60 s · 3 recordings");
    await expect(use).toBeDisabled();
    await dialog
      .getByRole("button", { name: `Remove ${three.name}`, exact: true })
      .click();
    await expect(
      dialog
        .getByRole("list", { name: "Recordings for Mara's voice" })
        .getByRole("listitem"),
    ).toHaveText([`${one.name}10 s`, `${two.name}20 s`]);
    await expect(total).toHaveText("30 s of 15–60 s · 2 recordings");
    expect(voiceCalls(page)).toEqual([]);
    const revision = (await world.read()).revision;
    await use.click();
    await expect(dialog).toHaveCount(0);
    expect(voiceCalls(page)).toEqual([
      {
        path: mara(world),
        method: "PUT",
        body: { expected_revision: revision, asset_ids: [one.id, two.id] },
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
    await expectBlind(page, world);
    // Journaled: the notice's Undo takes the recordings back.
    await notice(page)
      .getByRole("button", { name: "Undo", exact: true })
      .click();
    await expect(notice(page)).toContainText(
      "Undone: Set Mara's voice from 2 recordings (30 s)",
    );
    await expect(voiceRow(page)).toHaveText("None");
    expect(callsOf(page).filter((c) => c.path.endsWith("/undo"))).toHaveLength(
      1,
    );
  });
});

// Recordings outside 15–60 s cannot be chosen or proposed: the service refuses them, and A40's picker says so first.
test.describe("Gaia proposed 30 s of Mara's takes", () => {
  test.use({
    scene: {
      ...CAST,
      recordings: TAKES,
      voices: [{ recordings: [1, 0], proposed: true }],
    },
  });

  test("A41: with proposed recordings, Build voice… is disabled with the service's reason as its title", async ({
    page,
    world,
  }) => {
    const reason = "Confirm the proposed recordings first.";
    await openMara(page, world);
    const build = card(page).getByRole("button", { name: "Build voice…" });
    await expect(build).toBeDisabled();
    await expect(build).toHaveAttribute("title", reason);
    await expect(voiceStatus(page)).toHaveText(reason);
    await expect(voiceRow(page)).toHaveText("2 recordings · 30 s · proposed");
    await expect(card(page).locator(".film-card-voice")).toContainText(
      "proposed by the agent",
    );
    // Confirming is a journaled cast edit; then the build can be asked for.
    const revision = (await world.read()).revision;
    await card(page)
      .getByRole("button", { name: "Confirm recordings for Mara", exact: true })
      .click();
    await expect(notice(page)).toContainText(
      "Confirmed Mara's voice recordings.",
    );
    await expect(build).toBeEnabled();
    expect(voiceCalls(page)).toEqual([
      {
        path: `${mara(world)}/confirm`,
        method: "POST",
        body: { expected_revision: revision },
      },
    ]);
    await expectBlind(page, world);
  });
});

test.describe("Mara's voice is 30 s of her takes, not built", () => {
  test.use({
    scene: { ...CAST, recordings: TAKES, voices: [{ recordings: [1, 0] }] },
  });

  test("A41/A42: Build voice… asks first; Cancel and Escape start nothing; Build voice posts once and the card shows its progress", async ({
    page,
    world,
  }) => {
    await openMara(page, world);
    const build = card(page).getByRole("button", { name: "Build voice…" });
    // "Build the voice to hear an audition." names the next step; it blocks nothing.
    await expect(voiceStatus(page)).toHaveText(NOT_BUILT);
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
    expect(voiceCalls(page)).toEqual([]);
    const revision = (await world.read()).revision;
    await build.click();
    await ask.getByRole("button", { name: "Build voice", exact: true }).click();
    await expect(ask).toHaveCount(0);
    await expect(notice(page)).toContainText("Voice build started.");
    expect(voiceCalls(page)).toEqual([
      {
        path: `${mara(world)}/build`,
        method: "POST",
        body: { expected_revision: revision, rebuild: false },
      },
    ]);
    // A43: the build's stage and counts, in the card's state line and summary.
    await expect(voiceStatus(page)).toHaveText("Generating material · 0 of 27");
    await expect(voiceRow(page)).toHaveText(
      "building · generating material · 0 of 27",
    );
    await expect(build).toHaveCount(0);
    await expectBlind(page, world);
  });

  test("A45: Clear removes confirmed recordings as a journaled edit; the card never shows an operation, artifact or folder", async ({
    page,
    world,
  }) => {
    const [one, two] = world.recordings;
    await openMara(page, world);
    await expect(
      card(page)
        .getByRole("list", { name: "Recordings for Mara's voice" })
        .getByRole("listitem"),
    ).toHaveText([`${two.name}20 s`, `${one.name}10 s`]);
    await expectBlind(page, world);
    await card(page)
      .getByRole("button", {
        name: "Clear voice recordings for Mara",
        exact: true,
      })
      .click();
    await expect(notice(page)).toContainText(
      "Cleared Mara's voice recordings.",
    );
    await expect(voiceRow(page)).toHaveText("None");
    expect(voiceCalls(page)).toEqual([
      { path: mara(world), method: "DELETE", body: {} },
    ]);
    await expectBlind(page, world);
  });
});

/** An expressive recordings folder of 16 clips in two categories, set in Settings after Mara's voice was built from
 * an earlier one; her audition waits for review. */
const CHANGED = {
  recordings: TAKES,
  voices: [{ recordings: [1, 0], build: "waiting" as const }],
  expressive: { scream: 12, sob: 4 },
  expressive_changed: true,
};

test.describe("Mara's voice was built before the expressive recordings changed", () => {
  test.use({ scene: { ...CAST, ...CHANGED } });

  test("A41/A42: a voice built before the expressive recordings changed builds again with rebuild", async ({
    page,
    world,
  }) => {
    await openMara(page, world);
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
    await expectBlind(page, world);
    const revision = (await world.read()).revision;
    await ask.getByRole("button", { name: "Build voice", exact: true }).click();
    await expect(notice(page)).toContainText("Voice build started.");
    expect(voiceCalls(page)).toEqual([
      {
        path: `${mara(world)}/build`,
        method: "POST",
        body: { expected_revision: revision, rebuild: true },
      },
    ]);
    await expectBlind(page, world);
  });

  for (const theme of ["dark", "light"] as const)
    test(`A46: axe passes on the character card's voice and on both voice sheets, ${theme}`, async ({
      page,
      world,
    }) => {
      await useTheme(page, theme);
      await openMara(page, world);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(card(page).getByLabel("Play audition.wav")).toBeVisible();
      await expectAxeClean(page);
      await card(page)
        .getByRole("button", { name: "Change recordings…", exact: true })
        .click();
      const samples = page.getByRole("dialog", {
        name: "Recordings for Mara's voice",
      });
      for (const { name } of world.recordings)
        await expect(
          samples.locator(".recast-asset-list button", { hasText: name }),
        ).toHaveCount(1);
      await expectAxeClean(page);
      await page.keyboard.press("Escape");
      await expect(samples).toHaveCount(0);
      await card(page).getByRole("button", { name: "Build voice…" }).click();
      const ask = page.getByRole("dialog", { name: "Build Mara's voice?" });
      await expect(ask).toContainText("16 clips in 2 categories");
      await expectAxeClean(page);
      await expectBlind(page, world);
      await page.keyboard.press("Escape");
      expect(voiceCalls(page)).toEqual([]);
    });
});

test.describe("Mara's voice is building and the dialogue is analysed", () => {
  test.use({
    scene: {
      ...CAST,
      dialogue: "succeeded",
      recordings: TAKES,
      voices: [{ recordings: [1, 0], build: "building" }],
    },
  });

  test("A43: a building voice shows its stage on the card and in the Voice sheet; a failed one says why and Retry posts once", async ({
    page,
    world,
  }) => {
    await openMara(page, world);
    await expect(voiceStatus(page)).toHaveText("Generating material · 0 of 27");
    await voice(page).click();
    const sheet = page.getByRole("dialog", { name: "Dialogue analysed" });
    const rows = sheet.getByRole("listitem");
    await expect(rows.filter({ hasText: "Mara" })).toContainText(
      "Voice: building · generating material · 0 of 27",
    );
    await expect(rows.filter({ hasText: "Elias" })).toContainText(
      "Voice: no recordings",
    );
    await expectBlind(page, world);
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    // The build stops while training.
    await world.settle("voice_build", "failed");
    await expect(voiceStatus(page)).toHaveText(TRAINING);
    await expect(voiceRow(page)).toHaveText("failed");
    await expectBlind(page, world);
    const revision = (await world.read()).revision;
    await card(page)
      .getByRole("button", { name: "Retry", exact: true })
      .click();
    await expect(voiceStatus(page)).toHaveText("Generating material · 0 of 27");
    expect(voiceCalls(page)).toEqual([
      {
        path: `${mara(world)}/retry`,
        method: "POST",
        body: { expected_revision: revision },
      },
    ]);
  });
});

test.describe("Mara's audition waits for review", () => {
  test.use({
    scene: {
      ...CAST,
      dialogue: "succeeded",
      confirmed: [0],
      recordings: TAKES,
      voices: [{ recordings: [1, 0], build: "waiting" }],
    },
  });

  for (const action of ["Approve", "Reject"] as const)
    test(`A44: a waiting audition plays its reel; ${action} voice is a single human request`, async ({
      page,
      world,
    }) => {
      await openMara(page, world);
      await expect(voiceStatus(page)).toHaveText("Waiting for your review");
      await expect(voiceRow(page)).toHaveText("audition ready");
      const player = card(page).getByLabel("Play audition.wav", {
        exact: true,
      });
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
      await expectBlind(page, world);
      const revision = (await world.read()).revision;
      await card(page)
        .getByRole("button", { name: `${action} voice for Mara`, exact: true })
        .click();
      await expect
        .poll(() => voiceCalls(page))
        .toEqual([
          {
            path: `${mara(world)}/${action.toLowerCase()}`,
            method: "POST",
            body: { expected_revision: revision },
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
        const rejected = (await world.read()).revision;
        const retried = page.waitForResponse((r) =>
          r.url().endsWith("/voice/retry"),
        );
        await card(page)
          .getByRole("button", { name: "Retry audition", exact: true })
          .click();
        const response = await retried;
        expect(response.ok(), await response.text()).toBe(true);
        await expect(voiceStatus(page)).toHaveText(
          /^Converting the audition · 0 of \d+$/,
        );
        expect(voiceCalls(page).at(-1)).toEqual({
          path: `${mara(world)}/retry`,
          method: "POST",
          body: { expected_revision: rejected },
        });
      }
      await expectBlind(page, world);
    });
});
