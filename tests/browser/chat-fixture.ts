import { expect, type APIRequestContext } from "@playwright/test";
import { seedDirect } from "./direct-fixture";

export const referenceReading = (assetId: string) => [
  { tool: "describe_reference", thought: "", input: { asset_id: assetId } },
  {
    subject: "A walker in plaid",
    place: "Courtyard",
    lighting: "Daylight",
    shot_size: "full",
    camera_height: "eye level",
    mood: "Quiet",
    notable: [],
  },
];

export async function seedChat(request: APIRequestContext) {
  const data = await seedDirect(request);
  // Chat's render proposals need compilable content; the Direct fixture's
  // frame-zero state is intentionally not supported by the render compiler.
  const segment = await (
    await request.get(`/api/direction/segments/${data.second.id}`, {
      headers: data.headers,
    })
  ).json();
  const updated = await request.patch(
    `/api/direction/segments/${data.second.id}`,
    {
      headers: data.headers,
      data: {
        expected_revision: segment.revision,
        content: { ...segment.content, states: [] },
      },
    },
  );
  expect(updated.ok(), await updated.text()).toBeTruthy();
  const phase = await request.post(
    `/api/direction/segments/${data.second.id}/phases`,
    {
      headers: data.headers,
      data: {
        expected_revision: (await updated.json()).revision,
        start_frame: data.cut.frame,
        content: {
          description: "Arin continues along the railing in the reverse view.",
          screen_direction: "right to left",
          sound: "Footsteps on stone; a soft courtyard breeze.",
        },
      },
    },
  );
  expect(phase.ok(), await phase.text()).toBeTruthy();
  const { script, restore } = await scriptedAgent(request, data.headers);
  const proposal = (goal = "Make the reverse clear", jobs = 4) => ({
    tool: "propose_plan",
    thought: "",
    input: {
      goal,
      steps: [
        {
          kind: "edit",
          title: "Set the reverse relation",
          target: data.cut.id,
          jobs: 0,
        },
        {
          kind: "prepare",
          title: "Prepare the plate, survey, pin and sheet",
          target: data.second.id,
          // Removal 2, survey 1, pin 2, sheet 0, before the render budget.
          jobs: 5,
        },
        {
          kind: "render",
          title: "Explore the shot",
          target: data.second.id,
          jobs,
        },
        {
          kind: "review",
          title: "Choose the take",
          target: data.second.id,
          jobs: 0,
        },
      ],
      questions: ["Keep the courtyard light?"],
    },
  });
  const session = async () =>
    (
      await (
        await request.get(`/api/agent/sessions?movie_id=${data.movie.id}`, {
          headers: data.headers,
        })
      ).json()
    )[0];
  return { ...data, script, proposal, session, restore };
}

/** Points the agent at the scripted model: `script` installs its next actions, `restore` the old connections. */
export async function scriptedAgent(
  request: APIRequestContext,
  headers: Record<string, string>,
) {
  const url = process.env.GAIAVFX_SCRIPTED_AGENT_URL!;
  const old = await (
    await request.get("/api/v1/settings/connections", { headers })
  ).json();
  const response = await request.put("/api/v1/settings/connections", {
    headers,
    data: {
      ...old,
      assistant_agent_connection: "scripted-chat",
      assistant_connections: [
        {
          id: "scripted-chat",
          name: "Scripted UI fixture",
          provider: "openai_compatible",
          base_url: `${url}/v1`,
          model: "scripted-gaia",
          vision: true,
          streaming: false,
          structured_output: "json_schema",
          // The scripted reader receives the original image as a data URL.
          context_tokens: 2_000_000,
        },
      ],
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const script = async (actions: unknown[], delay = 0) => {
    const installed = await request.post(`${url}/script`, {
      data: { actions, delay },
    });
    expect(installed.ok()).toBeTruthy();
  };
  const restore = () =>
    request.put("/api/v1/settings/connections", { headers, data: old });
  return { script, restore };
}
