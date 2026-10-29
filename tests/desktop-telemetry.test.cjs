const { test } = require("node:test");
const assert = require("node:assert/strict");
const { validateTraceLink } = require("../desktop/trace-links.cjs");
const id = "0123456789abcdef0123456789abcdef";
test("desktop trace bridge accepts only cloud project trace/session pages", () => {
  const url = `https://cloud.langfuse.com/project/project-1/traces/${id}`;
  assert.equal(validateTraceLink(url, id, "trace"), url);
  const session =
    "https://us.cloud.langfuse.com/project/project-1/sessions/fp-workflow-1";
  assert.equal(validateTraceLink(session, id, "session"), session);
  for (const invalid of [
    `http://cloud.langfuse.com/project/project-1/traces/${id}`,
    `https://cloud.langfuse.com.evil.example/project/project-1/traces/${id}`,
    `https://user:password@cloud.langfuse.com/project/project-1/traces/${id}`,
    url + "?token=secret",
    url + "#other",
    "file:///C:/Windows/System32/cmd.exe",
    "https://cloud.langfuse.com/settings",
    url.replace(id, "a".repeat(32)),
  ])
    assert.throws(() => validateTraceLink(invalid, id, "trace"));
});
