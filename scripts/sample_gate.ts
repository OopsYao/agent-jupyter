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

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    if (event.toolName === "bash") {
      return {
        block: true,
        reason:
          `${BLOCKED_REASON_PREFIX} bash is visible for behavior sampling, ` +
          "but process and filesystem execution is disabled.",
      };
    }

    if (event.toolName === "read" && event.input.path !== ALLOWED_READ_PATH) {
      return {
        block: true,
        reason:
          `${BLOCKED_REASON_PREFIX} read is permitted only for the exact ` +
          `agent-jupyter Skill path (${ALLOWED_READ_PATH}).`,
      };
    }

    return undefined;
  });
}
