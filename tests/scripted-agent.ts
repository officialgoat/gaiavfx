import { createServer } from "node:http";

/** CPU-only OpenAI-compatible fixture. Actions are installed by the test before
 * sending a real harness message; no provider, ComfyUI or GPU is contacted. */
export async function startScriptedAgent() {
  let actions: unknown[] = [];
  let requests: unknown[] = [];
  let delay = 0;
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(Buffer.from(chunk));
    const body = JSON.parse(Buffer.concat(chunks).toString() || "{}");
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/script") {
      actions = body.actions ?? [];
      requests = [];
      delay = body.delay ?? 0;
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    if (req.url === "/requests") {
      res.end(JSON.stringify(requests));
      return;
    }
    if (req.url !== "/v1/chat/completions") {
      res.writeHead(404);
      res.end("{}");
      return;
    }
    requests.push(body);
    const action = actions.shift() ?? {
      tool: "say",
      thought: "",
      input: { text: "The scripted actions are complete." },
    };
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    res.end(
      JSON.stringify({
        id: `script-${requests.length}`,
        object: "chat.completion",
        created: 1,
        model: "scripted-gaia",
        choices: [
          {
            index: 0,
            message: { role: "assistant", content: JSON.stringify(action) },
            finish_reason: "stop",
          },
        ],
        usage: { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140 },
      }),
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("Scripted agent did not bind a port");
  return {
    url: `http://127.0.0.1:${address.port}`,
    stop: () =>
      new Promise<void>((resolve, reject) => {
        server.closeAllConnections();
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}
