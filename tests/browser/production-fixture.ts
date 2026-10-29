import { expect, type APIRequestContext } from "@playwright/test";
import { seedDirect } from "./direct-fixture";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { developmentPython } from "../../scripts/python.mjs";
import type { components } from "../../frontend/src/shared/api.generated";
type S = components["schemas"];

export async function seedProduction(
  request: APIRequestContext,
  render = false,
) {
  const data = await seedDirect(request);
  const headers = data.headers;
  async function post<T>(path: string, body: unknown = {}): Promise<T> {
    const response = await request.post(path, { headers, data: body });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  async function get<T>(path: string): Promise<T> {
    const response = await request.get(path, { headers });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  async function patch(path: string, body: unknown) {
    const response = await request.patch(path, { headers, data: body });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  async function finished(id: string) {
    let value: S["OperationDTO"];
    await expect
      .poll(
        async () => {
          value = await get(`/api/production/operations/${id}`);
          if (["failed", "partial", "needs_attention"].includes(value.state))
            throw new Error(JSON.stringify(value));
          return value.state;
        },
        { timeout: 120_000, intervals: [200, 500, 1000] },
      )
      .toBe("succeeded");
    return value!;
  }
  const placeId = data.from.place_id;
  const side = await post<S["AngleDTO"]>(
    `/api/direction/places/${placeId}/angles`,
    { name: "Along the wall" },
  );
  const segment = await get<S["SegmentDTO"]>(
    `/api/direction/segments/${data.second.id}`,
  );
  const independent = await patch(`/api/direction/segments/${segment.id}`, {
    expected_revision: segment.revision,
    delivered_frames: 102,
    content: { ...segment.content, states: [], events: [], speech: [] },
  });
  await post(`/api/direction/segments/${segment.id}/phases`, {
    expected_revision: independent.revision,
    start_frame: 36,
    content: {
      description: "Arin turns beneath the arcade.",
      screen_direction: "toward camera",
      sound: "footsteps",
    },
  });
  const plateOperation = await post<S["OperationDTO"]>(
    "/api/production/removals",
    {
      place_id: placeId,
      angle_id: data.from.id,
      source_asset: data.asset.id,
      wording: {
        whom: "the walker",
        object_pronoun: "him",
        behind: "stone walls",
      },
      seeds: [11],
      request_key: crypto.randomUUID(),
    },
  );
  await finished(plateOperation.id);
  const plates = await post<S["ReviewDTO"]>(
    `/api/production/removals/${plateOperation.id}/review`,
  );
  const plate = await post<S["PlaceDTO"]>("/api/production/plates/approve", {
    candidate_id: plates.members[0].candidate_id,
  });
  const body = {
    place_id: placeId,
    seed: 12,
    request_key: crypto.randomUUID(),
    plan: {
      what: "an empty courtyard",
      sound: "wind",
      first_view: "gate",
      first_angle_id: data.from.id,
      first_frame_asset: plate.plate_asset_id!,
      views: [
        [data.to.id, "reverse view"],
        [side.id, "along the wall"],
      ],
      overhead: "straight down",
    },
  };
  const survey = await post<S["OperationDTO"]>("/api/production/surveys", body);
  const replay = await request.post("/api/production/surveys", {
    headers,
    data: body,
  });
  expect(replay.status()).toBe(200);
  expect((await replay.json()).id).toBe(survey.id);
  const surveyed = await finished(survey.id);
  const views = await post<S["ReviewDTO"]>(
    `/api/production/surveys/${survey.id}/review`,
  );
  if (!render)
    return {
      ...data,
      survey,
      views,
      get,
      post,
      patch,
      finished,
      render: undefined,
      review: undefined,
    };
  const approved: S["ProductionEvidenceDTO"][] = [];
  for (const m of views.members)
    approved.push(
      await post("/api/production/views/approve", {
        candidate_id: m.candidate_id,
      }),
    );
  const targetCandidate = surveyed.candidates.find(
    (c) => c.output_key === data.to.id,
  )!;
  const view = approved.find((e) => e.asset_id === targetCandidate.asset_id)!;
  const insertion = await post<S["OperationDTO"]>(
    "/api/production/insertions",
    {
      cut_id: data.cut.id,
      evidence_id: view.id,
      subject_asset: data.asset.id,
      seeds: [13],
      request_key: crypto.randomUUID(),
      wording: {
        who: "Arin",
        looks: "plaid shirt",
        pronouns: "he",
        facing: "toward_camera",
        plate: "empty courtyard",
        keep: "walls",
        where: "by rail",
        size: "one third",
        behind: "the arcade",
      },
    },
  );
  await finished(insertion.id);
  const pins = await post<S["ReviewDTO"]>(
    `/api/production/insertions/${insertion.id}/review`,
  );
  await post("/api/production/pins/approve", {
    candidate_id: pins.members[0].candidate_id,
  });
  const layout = await get<S["ProductionSheetTilesDTO"]>(
    `/api/production/places/${placeId}/sheet-tiles`,
  );
  const sheet = await post<S["OperationDTO"]>("/api/production/sheets", {
    ...layout,
    origin: "survey",
  });
  const assembled = await finished(sheet.id);
  const artifact = JSON.parse(
    execFileSync(
      developmentPython(path.resolve(".")),
      ["tests/browser-context.py", process.env.GAIAVFX_TEST_LIBRARY!],
      { encoding: "utf8", windowsHide: true },
    ),
  );
  const rendered = await post<S["OperationDTO"]>("/api/production/renders", {
    cut_id: data.cut.id,
    seeds: [760001, 760002, 760003],
    sheet_asset: assembled.candidates[0].asset_id,
    context_artifact: artifact.id,
  });
  await finished(rendered.id);
  const review = await post<S["ReviewDTO"]>(
    `/api/production/renders/${rendered.id}/review`,
  );
  return {
    ...data,
    survey,
    views,
    get,
    post,
    patch,
    finished,
    render: rendered,
    review,
  };
}
