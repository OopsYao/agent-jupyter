// Pi supplies the extension module types at runtime; repository-local fallback
// diagnostics do not have access to Pi's node_modules.
// @ts-nocheck
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const ALLOWED_READ_PATH = resolve(
  SCRIPT_DIR,
  "../src/agent_jupyter/resources/agent_jupyter_skill.md",
);
const BLOCKED_REASON_PREFIX = "Behavior sampler gate blocked:";
const GATE_AUDIT_PREFIX = "AGENT_JUPYTER_SAMPLE_GATE:";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    let reason: string | undefined;
    if (event.toolName === "bash") {
      reason =
        `${BLOCKED_REASON_PREFIX} bash is visible for behavior sampling, ` +
        "but process and filesystem execution is disabled.";
    } else if (
      event.toolName === "read" &&
      event.input.path !== ALLOWED_READ_PATH
    ) {
      reason =
        `${BLOCKED_REASON_PREFIX} read is permitted only for the exact ` +
        `agent-jupyter Skill path (${ALLOWED_READ_PATH}).`;
    }

    if (reason === undefined) return undefined;

    console.error(
      GATE_AUDIT_PREFIX +
        JSON.stringify({ toolCallId: event.toolCallId, reason }),
    );
    ctx.abort();
    return undefined;
  });
}
