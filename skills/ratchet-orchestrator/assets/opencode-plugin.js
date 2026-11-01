// Ratchet Loop OpenCode plugin, written by ratchet.py install-hooks
//
// Written for OpenCode 1.18.27 (the version installed when this was built), checked against the OpenCode v1.19.5
// source. It uses OpenCode's released v1 plugin API: a plugin file in .opencode/plugins/ (this project) or in the
// user's OpenCode config folder, whose named function returns an object of hooks.
//   Docs: https://opencode.ai/docs/plugins/ and https://opencode.ai/docs/sdk/
// The hooks Ratchet uses, and where each one is documented:
//   - the guard uses tool.execute.before (docs: Plugins, "tool.execute.before"; the docs example blocks a tool call
//     by throwing an Error; source: packages/opencode/src/session/tools.ts, awaited before the tool runs);
//   - the prompt uses chat.message (source: packages/opencode/src/session/prompt.ts, awaited before the user's
//     message is saved; it is in the plugin types, not on the docs page);
//   - the stop emulation uses the session.idle event, delivered to the `event` hook (docs: Plugins, "session.idle";
//     OpenCode has no hook that can hold a turn open, and calls `event` fire-and-forget, so this can only start a new
//     turn after the old one ended, which is best-effort).
// The dev-branch v2 plugin API (@opencode/plugin) is NOT supported; only the released v1 plugin API is.
//
// The guard is in: tool.execute.before asks `ratchet.py hook guard --host opencode` and stops a tool call the engine
// refuses (by throwing). The prompt is in: chat.message sends what the user typed to `ratchet.py hook prompt --host
// opencode` (which records it as their reply) and adds the engine's guidance to the message as a synthetic text part.
// Every other outcome lets the call or the message go through (fail open): a missing engine, a crash, a bad reply or a
// hang (of the engine for 30 s, or of the session lookup for 10 s) must never block every tool or break a message, and
// a message that cannot be vouched for is not recorded.
// The stop emulation is in, best-effort only: on session.idle the `event` hook asks `ratchet.py hook stop --host
// opencode` and, when the engine says a gate is still open, sends the reason as a NEW message (client.session.promptAsync,
// else client.session.prompt). The turn had already ended and the user saw it end, so this is advisory, not a hard stop.
// It is capped so it can never loop: at most 3 re-prompts in a row with an unchanged reason (a changed reason restarts
// that count), at most 20 per session until the user types again (a typed reply resets both), one in flight per session,
// all state per session; once capped it stops asking the engine. It never re-prompts a sub-agent's session, a session
// that cannot be looked up, or a session the user cancelled or that ended on an error, until they type again. OpenCode
// publishes a cancel in more than one shape, so every one is read: a session.error (a cancel in mid-stream is a
// MessageAbortedError, and any other error except ContextOverflowError also holds the session), an assistant
// message.updated carrying a MessageAbortedError (a cancel between steps), a rejected permission (permission.replied
// reject) and a dismissed question (question.rejected), which hold until the user types, and a session.idle that follows
// a session.status "retry" with no "busy" in between (a cancel while OpenCode waits to retry a provider call). Right
// before sending, the session's last assistant message is fetched (client.session.messages) and nothing is sent when it
// carries an error, has no `finish`, has finish "tool-calls" or "unknown" (not a finished turn), or cannot be fetched
// in time. The stop state of every session lives in one Map on globalThis (Symbol.for
// "ratchet.opencode.stop.v1"), shared by every load of this plugin, because OpenCode loads it twice when it is in both the
// user and the project folder: the second copy then sees the first one's in-flight re-prompt and counters and sends
// nothing. A failed prompt call is swallowed and never retried. The `event` hook never throws.
//
// The marker: chat.message fires for EVERY user-role message, including the ones this plugin sends itself with
// client.session.promptAsync (or prompt), and it carries no "typed by a human" flag. So this plugin puts a fixed first line (the prefix
// below plus a random token made when the plugin loads) on every text it sends itself, and never forwards a message
// carrying that token. The engine independently drops any text whose line starts with the fixed prefix, so a text
// from an earlier load of this plugin is dropped too. `ratchetMarkedText(reason)` is the one place that builds such a
// text: the stop re-prompt builds its text with the same `markedText`, and the `ratchetMarkedText` key on the returned
// hooks object exposes it (OpenCode ignores keys it has no hook for). It must not become a second exported function:
// OpenCode v1 may call every function export as a plugin.
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
const STOP_ARGS = "hook stop --host opencode".split(" ");
const MAX_REPROMPTS_IN_ROW = 3; // re-prompts in a row whose reason text is unchanged (no gate progress)
const MAX_REPROMPTS_PER_SESSION = 20; // re-prompts per session until the user types again
const STOP_LIMIT = 2000; // sessions kept in the shared stop state (the oldest is dropped beyond this)
// A change to the shape of an entry in this shared Map must bump the version in this key (an older load may share it).
const STOP_SLOT = Symbol.for("ratchet.opencode.stop.v1");
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

// The reason to re-prompt with when the engine's stop reply is one JSON object whose `block` is exactly true and whose
// `message` is a non-blank string, else null (a damaged or empty block reply sends nothing: fail open).
function stopReasonFrom(stdout) {
  if (typeof stdout !== "string") return null;
  let reply;
  try {
    reply = JSON.parse(stdout);
  } catch (e) {
    void e;
    return null;
  }
  if (reply === null || typeof reply !== "object" || Array.isArray(reply) || reply.block !== true) return null;
  return typeof reply.message === "string" && reply.message.trim() !== "" ? reply.message : null;
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

// True when the stop emulation may re-prompt after the session's LAST assistant message: that message is found, carries no
// error, and has a `finish` other than "tool-calls" or "unknown" (OpenCode itself ends a turn on those when the user rejects
// a permission request or dismisses a question, and does not treat them as a finished turn: prompt.ts). A message with an
// error (a cancel or a provider failure) or without a `finish` (a turn cut
// short, e.g. cancelled while OpenCode waited to retry) is not a turn to continue; so is a lookup that fails, answers
// oddly or takes longer than SESSION_LOOKUP_TIMEOUT_MS (the safe side is to send nothing). A client with no
// `session.messages` cannot be asked, and the other cancel signals stand alone.
async function lastTurnFinished(client, sessionID) {
  if (!client || !client.session || typeof client.session.messages !== "function") return true;
  let timer = null;
  try {
    const limit = new Promise((resolve) => {
      timer = setTimeout(() => resolve(null), SESSION_LOOKUP_TIMEOUT_MS);
    });
    const got = await Promise.race([Promise.resolve(client.session.messages({ path: { id: sessionID }, query: { limit: 10 } })), limit]);
    if (got === null || typeof got !== "object" || !Array.isArray(got.data)) return false;
    for (let i = got.data.length - 1; i >= 0; i--) {
      const info = got.data[i] && got.data[i].info;
      if (info === null || typeof info !== "object" || info.role !== "assistant") continue;
      if (info.error) return false;
      return typeof info.finish === "string" && info.finish !== "" && info.finish !== "tool-calls" && info.finish !== "unknown";
    }
    return false;
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
  // session id -> { last, row, total, flying, stopped, retry }: the stop emulation's caps and its "do not re-prompt"
  // flags, per session. ONE Map for every load of this plugin in the process (see the header), made once.
  if (!(globalThis[STOP_SLOT] instanceof Map)) globalThis[STOP_SLOT] = new Map();
  const stops = globalThis[STOP_SLOT];
  const markedText = (reason) => `${PLUGIN_TURN_PREFIX} ${token}\n${typeof reason === "string" ? reason : String(reason)}`;
  // The session's entry, made (and the oldest one dropped past STOP_LIMIT) when it has none yet.
  const stopOf = (sessionID) => {
    let st = stops.get(sessionID);
    if (!st) {
      st = { last: null, row: 0, total: 0, flying: false, stopped: false, retry: false };
      stops.set(sessionID, st);
      if (stops.size > STOP_LIMIT) stops.delete(stops.keys().next().value);
    }
    return st;
  };

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
      // A message the user really typed (not marker-shaped text) starts the stop emulation's caps over and ends a cancel.
      if (!text.split("\n").some((line) => line.trimStart().startsWith(PLUGIN_TURN_PREFIX))) {
        const st = stops.get(sessionID);
        if (st) {
          st.last = null;
          st.row = 0;
          st.total = 0;
          st.stopped = false;
          st.retry = false;
        }
      }
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

  // session.idle: ask the engine whether a gate is still open and, if so, send its reason as a new message. Skips (all
  // BEFORE the engine is asked, since its stop count is shared by the whole run): a session that is cancelled or ended
  // on an error, one with a re-prompt in flight (also one sent by another load of this plugin), one at a cap, and any
  // session that is not a looked-up top-level one. The session's entry is claimed (flying) before the first await, so a
  // second load handling the same idle finds it taken; an entry made only for a session that turns out not to be a
  // top-level one is dropped again.
  const onIdle = async (sessionID) => {
    let st = stops.get(sessionID);
    if (st && (st.stopped || st.flying || st.row >= MAX_REPROMPTS_IN_ROW || st.total >= MAX_REPROMPTS_PER_SESSION)) return;
    const created = !st;
    st = stopOf(sessionID);
    st.flying = true;
    let handedOver = false;
    try {
      const client = ctx && ctx.client;
      if (!(await isTopLevelSession(client, sessionID))) {
        if (created && !st.stopped && !st.retry && stops.get(sessionID) === st) stops.delete(sessionID);
        return;
      }
      if (st.stopped) return;
      const answer = await askEngine(STOP_ARGS, JSON.stringify({ hook_event_name: "session.idle", session_id: sessionID, cwd: project }));
      const reason = stopReasonFrom(answer);
      if (reason === null || st.stopped) return;
      if (st.row >= MAX_REPROMPTS_IN_ROW || st.total >= MAX_REPROMPTS_PER_SESSION) return;
      if (!(await lastTurnFinished(client, sessionID)) || st.stopped) return;
      if (st.last === reason) {
        st.row += 1;
      } else {
        st.last = reason;
        st.row = 1;
      }
      st.total += 1; // counted before the call: a failed attempt uses up the cap too, and is never retried
      const send = typeof client.session.promptAsync === "function" ? "promptAsync" : "prompt";
      const sent = client.session[send]({ path: { id: sessionID }, body: { parts: [{ type: "text", text: markedText(reason) }] } });
      handedOver = true; // the handler does not wait for the call to end: `prompt` may only return when the next turn ends
      const done = () => {
        st.flying = false;
      };
      Promise.resolve(sent).then(done, done);
    } finally {
      if (!handedOver) st.flying = false;
    }
  };

  const onEvent = async (arg) => {
    const event = arg && arg.event;
    if (event === null || typeof event !== "object") return;
    const props = event.properties;
    if (props === null || typeof props !== "object") return;
    let sessionID = props.sessionID;
    if ((typeof sessionID !== "string" || sessionID === "") && props.info && typeof props.info === "object") {
      sessionID = event.type === "session.deleted" ? props.info.id : props.info.sessionID; // these carry the id in `info`
    }
    if (typeof sessionID !== "string" || sessionID === "") return;
    if (event.type === "session.error") {
      // a cancel in mid-stream (MessageAbortedError) or a provider failure: hold the session. A context overflow is not held:
      // OpenCode compacts and finishes the turn normally (lastTurnFinished still catches a turn that ended on it).
      if (!(props.error && props.error.name === "ContextOverflowError")) stopOf(sessionID).stopped = true;
    } else if (event.type === "permission.replied" || event.type === "question.rejected") {
      // OpenCode ends the turn itself when the user rejects a permission request or dismisses a question: hold the session
      if (event.type === "question.rejected" || props.reply === "reject") stopOf(sessionID).stopped = true;
    } else if (event.type === "message.updated") {
      const info = props.info;
      if (info && typeof info === "object" && info.role === "assistant" && info.error && info.error.name === "MessageAbortedError") {
        stopOf(sessionID).stopped = true; // a cancel between steps is only written on the assistant message
      }
    } else if (event.type === "session.status") {
      const kind = props.status && props.status.type;
      if (kind === "retry") {
        stopOf(sessionID).retry = true;
      } else if (kind === "busy") {
        const st = stops.get(sessionID);
        if (st) st.retry = false;
      }
    } else if (event.type === "session.deleted") {
      stops.delete(sessionID);
    } else if (event.type === "session.idle") {
      const st = stops.get(sessionID);
      if (st && st.retry) {
        st.retry = false;
        st.stopped = true; // an idle straight after a retry wait: the user cancelled while OpenCode was waiting
      }
      await onIdle(sessionID);
    }
  };

  return {
    ratchetMarkedText: markedText,
    event: async (arg) => {
      try {
        await onEvent(arg);
      } catch (e) {
        void e;
      }
    },
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
