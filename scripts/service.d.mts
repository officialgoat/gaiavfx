import type { ChildProcessWithoutNullStreams } from "node:child_process";

export function startService(options: {
  executable: string;
  args?: string[];
  library: string;
  frontend?: string;
  browserOrigin?: string;
  cwd?: string;
}): Promise<{
  child: ChildProcessWithoutNullStreams;
  url: string;
  token: string;
  stop: () => Promise<void>;
}>;
