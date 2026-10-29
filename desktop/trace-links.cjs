const cloudHosts = new Set([
  "cloud.langfuse.com",
  "us.cloud.langfuse.com",
  "jp.cloud.langfuse.com",
  "hipaa.cloud.langfuse.com",
]);
function validateTraceLink(value, traceId, kind) {
  if (!/^[a-f0-9]{32}$/.test(traceId) || !["trace", "session"].includes(kind))
    throw new Error("Invalid trace destination.");
  const url = new URL(value);
  const path =
    kind === "trace"
      ? new RegExp(`^/project/[a-zA-Z0-9_-]+/traces/${traceId}$`)
      : /^\/project\/[a-zA-Z0-9_-]+\/sessions\/[a-zA-Z0-9_-]+$/;
  if (
    url.protocol !== "https:" ||
    !cloudHosts.has(url.hostname) ||
    url.username ||
    url.password ||
    url.port ||
    url.search ||
    url.hash ||
    !path.test(url.pathname)
  )
    throw new Error("The trace link is not a trusted Langfuse destination.");
  return url.href;
}
module.exports = { validateTraceLink };
