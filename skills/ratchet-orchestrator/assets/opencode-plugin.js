// Ratchet Loop OpenCode plugin, written by ratchet.py install-hooks
//
// Written for OpenCode 1.18.27 (the version installed when this was built), checked against the OpenCode v1.19.5
// source. It uses OpenCode's released v1 plugin API: a plugin file in .opencode/plugins/ (this project) or in the
// user's OpenCode config folder, whose named function returns an object of hooks.
//   Docs: https://opencode.ai/docs/plugins/ and https://opencode.ai/docs/sdk/
// The hooks Ratchet will use, and where each one is documented:
//   - the guard uses tool.execute.before (docs: Plugins, "tool.execute.before"; the docs example blocks a tool call
//     by throwing an Error; source: packages/opencode/src/session/tools.ts, awaited before the tool runs);
//   - the prompt uses chat.message (source: packages/opencode/src/session/prompt.ts, awaited before the user's
//     message is saved; it is in the plugin types, not on the docs page);
//   - the stop emulation uses the session.idle event (docs: Plugins, "session.idle"; OpenCode has no hook that can
//     hold a turn open, so this can only start a new turn after the old one ended, which is best-effort).
// The dev-branch v2 plugin API (@opencode/plugin) is NOT supported; only the released v1 plugin API is.
//
// This step adds the guard: tool.execute.before asks `ratchet.py hook guard --host opencode` and stops a tool call
// the engine refuses (by throwing). Every other outcome lets the call go through (fail open): a missing engine, a
// crash, a bad reply or a hang must never block every tool. The prompt and stop hooks are not added yet.
// Edit nothing here: `ratchet.py install-hooks --host opencode` rewrites this file, and removes it with --uninstall.

import { spawn } from "node:child_process";

const RATCHET_ENGINE = __RATCHET_ENGINE_PATH__;
const GUARD_ARGS = "hook guard --host opencode".split(" ");
const GUARD_TIMEOUT_MS = 30000;
const FALLBACK_REASON = "RATCHET GUARD: this tool call is refused by Ratchet (it would change Ratchet's own records).";

// Run the engine with an argument list (no shell), send it one JSON payload and resolve with its stdout, or with
// null on any failure (cannot start, non-zero exit, timeout). The child is killed when the time limit passes.
function askEngine(payload) {
  return new Promise((resolve) => {
    let done = false;
    let timer = null;
    let child = null;
    const finish = (value) => {
      if (done) return;
      done = true;
      if (timer) clearTimeout(timer);
      resolve(value);
    };
    try {
      child = spawn("python3", [RATCHET_ENGINE, ...GUARD_ARGS], {
        stdio: ["pipe", "pipe", "ignore"],
        windowsHide: true,
      });
      let out = "";
      child.on("error", () => finish(null));
      child.stdin.on("error", () => {});
      child.stdout.on("error", () => {});
      child.stdout.on("data", (chunk) => {
        if (out.length < 1000000) out += chunk;
      });
      child.on("close", (code) => finish(code === 0 ? out : null));
      timer = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch (e) {
          void e;
        }
        finish(null);
      }, GUARD_TIMEOUT_MS);
      child.stdin.end(payload);
    } catch (e) {
      void e;
      finish(null);
    }
  });
}

// The refusal message when the engine's reply is one JSON object whose `block` is exactly true and whose `message` is
// a string or absent (a blank or absent one gets the fixed sentence), else null. A `message` that is present but not a
// string means the reply is damaged, so it is not a refusal (fail open).
function refusalFrom(stdout) {
  if (typeof stdout !== "string") return null;
  let reply;
  try {
    reply = JSON.parse(stdout);
  } catch (e) {
    void e;
    return null;
  }
  if (reply === null || typeof reply !== "object" || Array.isArray(reply) || reply.block !== true) return null;
  if ("message" in reply && typeof reply.message !== "string") return null;
  return typeof reply.message === "string" && reply.message.trim() !== "" ? reply.message : FALLBACK_REASON;
}

export const RatchetPlugin = async (ctx) => {
  let project = process.cwd();
  try {
    const given = ctx && (ctx.directory || ctx.worktree);
    if (typeof given === "string" && given) project = given;
  } catch (e) {
    void e;
  }
  return {
    "tool.execute.before": async (input, output) => {
      let refusal = null;
      try {
        const payload = JSON.stringify({
          hook_event_name: "tool.execute.before",
          session_id: input.sessionID,
          call_id: input.callID,
          cwd: project,
          tool_name: input.tool,
          tool_input: output.args,
        });
        refusal = refusalFrom(await askEngine(payload));
      } catch (e) {
        void e;
        refusal = null;
      }
      if (refusal !== null) throw new Error(refusal);
    },
  };
};
