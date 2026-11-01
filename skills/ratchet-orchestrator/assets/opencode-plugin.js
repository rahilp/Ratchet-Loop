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
// The guard is in: tool.execute.before asks `ratchet.py hook guard --host opencode` and stops a tool call the engine
// refuses (by throwing). The prompt is in: chat.message sends what the user typed to `ratchet.py hook prompt --host
// opencode` (which records it as their reply) and adds the engine's guidance to the message as a synthetic text part.
// Every other outcome lets the call or the message go through (fail open): a missing engine, a crash, a bad reply or a
// hang (of the engine for 30 s, or of the session lookup for 10 s) must never block every tool or break a message, and
// a message that cannot be vouched for is not recorded.
// The stop emulation is not added yet.
//
// The marker: chat.message fires for EVERY user-role message, including the ones this plugin sends itself with
// client.session.prompt, and it carries no "typed by a human" flag. So this plugin puts a fixed first line (the prefix
// below plus a random token made when the plugin loads) on every text it sends itself, and never forwards a message
// carrying that token. The engine independently drops any text whose line starts with the fixed prefix, so a text
// from an earlier load of this plugin is dropped too. `ratchetMarkedText(reason)` is the one place that builds such a
// text: it is a plain key on the returned hooks object (OpenCode ignores keys it has no hook for) and a later step
// reuses it. It must not become a second exported function: OpenCode v1 may call every function export as a plugin.
// Text from a sub-agent (a session with a parentID) or from a session that cannot be looked up in time is not recorded.
// Edit nothing here: `ratchet.py install-hooks --host opencode` rewrites this file, and removes it with --uninstall.

import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";

const RATCHET_ENGINE = __RATCHET_ENGINE_PATH__;
const GUARD_ARGS = "hook guard --host opencode".split(" ");
const PROMPT_ARGS = "hook prompt --host opencode".split(" ");
const PLUGIN_TURN_PREFIX = "[RATCHET PLUGIN TURN - NOT USER INPUT]";
const SEEN_LIMIT = 2000;
const GUARD_TIMEOUT_MS = 30000;
const SESSION_LOOKUP_TIMEOUT_MS = 10000;
const FALLBACK_REASON = "RATCHET GUARD: this tool call is refused by Ratchet (it would change Ratchet's own records).";

// Run the engine with an argument list (no shell), send it one JSON payload and resolve with its stdout, or with
// null on any failure (cannot start, non-zero exit, timeout). The child is killed when the time limit passes.
function askEngine(args, payload) {
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
      child = spawn("python3", [RATCHET_ENGINE, ...args], {
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

// True when the engine's reply is one JSON object (the prompt hook ran to its end, whether or not it had anything to say).
function engineReplied(stdout) {
  if (typeof stdout !== "string") return false;
  try {
    const reply = JSON.parse(stdout);
    return reply !== null && typeof reply === "object" && !Array.isArray(reply);
  } catch (e) {
    void e;
    return false;
  }
}

// The guidance message in the engine's reply: one JSON object whose `message` is a non-blank string, else null.
function guidanceFrom(stdout) {
  if (typeof stdout !== "string") return null;
  let reply;
  try {
    reply = JSON.parse(stdout);
  } catch (e) {
    void e;
    return null;
  }
  if (reply === null || typeof reply !== "object" || Array.isArray(reply)) return null;
  return typeof reply.message === "string" && reply.message.trim() !== "" ? reply.message : null;
}

// True only when the session is looked up and it is a top-level one (no parentID). A sub-agent's session has a
// parentID; any failure to look it up (a throw, a rejection, an error answer, an odd answer, or no answer within
// SESSION_LOOKUP_TIMEOUT_MS) is "not the user's". OpenCode waits for chat.message before it saves the user's message,
// so the lookup is raced against a timer (cleared once the lookup settles, so nothing keeps the process alive).
async function isTopLevelSession(client, sessionID) {
  let timer = null;
  try {
    const limit = new Promise((resolve) => {
      timer = setTimeout(() => resolve(null), SESSION_LOOKUP_TIMEOUT_MS);
    });
    const got = await Promise.race([Promise.resolve(client.session.get({ path: { id: sessionID } })), limit]);
    if (got === null || typeof got !== "object" || got.data === null || typeof got.data !== "object") return false;
    const parent = got.data.parentID;
    return parent === undefined || parent === null || parent === "";
  } catch (e) {
    void e;
    return false;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// An id for the guidance part: it starts with `prt` as OpenCode part ids do, and extends the LARGEST part id of the
// message so it sorts after every part (OpenCode reads a message's parts in id order).
function guidancePartId(parts) {
  let base = "prt";
  for (const p of parts) if (p && typeof p.id === "string" && p.id.startsWith("prt") && p.id > base) base = p.id;
  return `${base}_ratchet_${randomUUID()}`;
}

export const RatchetPlugin = async (ctx) => {
  let project = process.cwd();
  try {
    const given = ctx && (ctx.directory || ctx.worktree);
    if (typeof given === "string" && given) project = given;
  } catch (e) {
    void e;
  }
  const token = randomUUID();
  const seen = new Set();
  const inflight = new Set();

  // What the user typed in this message (its non-synthetic text parts), or null when there is nothing to record.
  const typedText = (parts) => {
    const texts = [];
    for (const p of parts) {
      if (p && p.type === "text" && !p.synthetic && !p.ignored && typeof p.text === "string" && p.text.trim() !== "") {
        texts.push(p.text);
      }
    }
    return texts.length ? texts.join("\n") : null;
  };

  const onChatMessage = async (input, output) => {
    if (input === null || typeof input !== "object" || output === null || typeof output !== "object") return;
    const parts = output.parts;
    const sessionID = input.sessionID;
    if (!Array.isArray(parts) || typeof sessionID !== "string" || sessionID === "") return;
    const text = typedText(parts);
    if (text === null || text.includes(token)) return; // nothing typed, or this plugin's own text
    const messageID = typeof input.messageID === "string" && input.messageID !== "" ? input.messageID : output.message && output.message.id;
    const hasId = typeof messageID === "string" && messageID !== "";
    // The same message delivered twice is one reply. An id counts as seen only once the session was verified and the
    // engine answered, so a failed try (a lookup failure, a crashed engine) can be retried; an id in flight is not
    // started a second time, so two simultaneous deliveries still record once.
    if (hasId) {
      if (seen.has(messageID) || inflight.has(messageID)) return;
      inflight.add(messageID);
    }
    let guidance = null;
    try {
      if (!(await isTopLevelSession(ctx && ctx.client, sessionID))) return;
      const answer = await askEngine(
        PROMPT_ARGS,
        JSON.stringify({
          hook_event_name: "chat.message",
          session_id: sessionID,
          message_id: typeof messageID === "string" ? messageID : undefined,
          cwd: project,
          prompt: text,
        }),
      );
      if (!engineReplied(answer)) return;
      if (hasId) {
        seen.add(messageID);
        if (seen.size > SEEN_LIMIT) seen.delete(seen.values().next().value);
      }
      guidance = guidanceFrom(answer);
    } finally {
      if (hasId) inflight.delete(messageID);
    }
    if (guidance === null) return;
    parts.push({
      id: guidancePartId(parts),
      sessionID,
      messageID: typeof messageID === "string" ? messageID : undefined,
      type: "text",
      text: guidance,
      synthetic: true,
    });
  };

  return {
    ratchetMarkedText: (reason) => `${PLUGIN_TURN_PREFIX} ${token}\n${typeof reason === "string" ? reason : String(reason)}`,
    "chat.message": async (input, output) => {
      try {
        await onChatMessage(input, output);
      } catch (e) {
        void e;
      }
    },
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
        refusal = refusalFrom(await askEngine(GUARD_ARGS, payload));
      } catch (e) {
        void e;
        refusal = null;
      }
      if (refusal !== null) throw new Error(refusal);
    },
  };
};
