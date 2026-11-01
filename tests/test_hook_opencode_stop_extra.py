#!/usr/bin/env python3
"""
OpenCode support, step 6, round 1 and 2 fixes: extra cases for the stop emulation (CP6-X01 ...). They sit in their own file
because the planned file tests/test_hook_opencode_stop.py is frozen; they reuse its helpers (worlds, the spy python3,
stand-in engines, verdict helpers) and run the same rendered plugin under node.

    python3 tests/test_hook_opencode_stop_extra.py      # needs node on PATH; builds throwaway git projects

What these pin (each one is shown able to fail with the rendered plugin changed in exactly one place; the
timers, the 2000-entry bound and the session.deleted cleanup have their own mutants in X03 and X07):
  X01  a cancel while OpenCode waits to retry a provider call publishes NO session.error (recorded live on OpenCode
       1.18.27: status busy, retry, an assistant message.updated with no error and no finish, status idle, session.idle).
       The plugin must not re-prompt it, and must not even ask the engine (its stop count is shared by the run).
  X02  a cancel between loop steps is written only on the assistant message (message.updated with a
       MessageAbortedError, no session.error): not re-prompted; a typed reply ends the hold.
  X03  the last assistant message is looked up right before sending: an error, no `finish`, an unreadable answer, a
       failing or a hanging lookup all send nothing; a finished last message sends exactly once; no timer is left behind.
  X04  a normal turn end (and a retry that recovered, busy again) still re-prompts exactly once.
  X05  two installed copies of the plugin in one OpenCode process (user and project folders) send ONE re-prompt per
       idle, three in total over six idles, and a re-prompt from either copy is never recorded as a typed reply.
  X06  any session.error except ContextOverflowError (a provider failure, not only a cancel) holds that session until the user types.
  X07  the shared stop state is bounded, a session.deleted drops a session's entry, and a sub-agent's session leaves
       no entry behind.
  X08  OpenCode ends a turn itself when the user rejects a permission request or dismisses a question (the last
       assistant message finishes "tool-calls" or "unknown", no session.error): never re-prompted, held until the user
       types; an approved permission still re-prompts; the lookup also refuses those two finish values (X03).
  X09  a session.error that is a ContextOverflowError does not hold the session (OpenCode compacts and goes on).
  X10  the message lookup asks for the newest 10 messages only (query limit).

State isolation: the plugin keeps its stop state in one Map on globalThis[Symbol.for('ratchet.opencode.stop.v1')] (shared
by every load, which is the point of X05). The planned cases each run in their own node process with one plugin load per
run, and they stay green unchanged; here the driver deletes that slot before each run.

Each case id appears in its check names as [CP6-Xnn].
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hook_opencode_stop as S  # noqa: E402  (the planned file's helpers; its main() only runs as a script)

S.RESULTS.clear()
PREFIX = S.PREFIX
SLOT = "Symbol.for('ratchet.opencode.stop.v1')"

# The planned driver, with: a `session.messages` on the fake client (a table per session, or none at all), the shared
# stop slot deleted before each run, and a `slot` op that reads the shared Map.
DRIVER_X = S.DRIVER_JS
_PATCHES = [
    ("  return { app: { log: noop('app.log') }, session: {",
     "  const messages = (o) => {\n"
     "    rec('session.messages', o);\n"
     "    const id = o && o.path && o.path.id;\n"
     "    const e = (id in st.msgs) ? st.msgs[id] : st.msgsDflt;\n"
     "    if (e && e.$throw) throw new Error('session.messages threw synchronously');\n"
     "    if (e && e.$reject) return Promise.reject(new Error('session.messages rejected'));\n"
     "    if (e && e.$hang) return new Promise(() => {});\n"
     "    return Promise.resolve(e);\n"
     "  };\n"
     "  return { app: { log: noop('app.log') }, session: { ...(st.noMessages ? {} : { messages }),"),
    ("pending: [], open: [], echoes: [], echoN: 0, hooks: null };",
     "pending: [], open: [], echoes: [], echoN: 0, hooks: null, msgs: run.msgs || {}, msgsDflt: run.msgsDflt, "
     "noMessages: !!run.noMessages };"),
    ("for (const run of scen.runs) {\n  const calls = [];",
     "for (const run of scen.runs) {\n  delete globalThis[Symbol.for('ratchet.opencode.stop.v1')];\n  const calls = [];"),
    ("        else if (step.op === 'exec') res = doExec(step);",
     "        else if (step.op === 'exec') res = doExec(step);\n"
     "        else if (step.op === 'slot') {\n"
     "          const m = globalThis[Symbol.for('ratchet.opencode.stop.v1')];\n"
     "          res = { status: 'resolved', isMap: m instanceof Map, size: m instanceof Map ? m.size : -1,\n"
     "            has: m instanceof Map ? (step.ids || []).filter((i) => m.has(i)) : [] };\n"
     "        }"),
]
for _old, _new in _PATCHES:
    assert _old in DRIVER_X, _old
    DRIVER_X = DRIVER_X.replace(_old, _new, 1)

# A driver for two plugin loads that share one client (the user and project copies in one OpenCode process).
DRIVER_TWO = r"""
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';
const scen = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const problems = [];
process.on('unhandledRejection', (e) => { problems.push('unhandledRejection: ' + ((e && e.message) || String(e))); });
process.on('uncaughtException', (e) => { problems.push('uncaughtException: ' + ((e && e.message) || String(e))); });
delete globalThis[Symbol.for('ratchet.opencode.stop.v1')];
const calls = [], echoes = [], instances = [], echoing = [], sent = [];
const client = { session: {
  get: async (o) => ({ data: { id: o.path.id } }),
  messages: async () => ({ data: scen.messages }),
  promptAsync: (o) => {
    const sid = o.path.id, text = o.body.parts[0].text, n = calls.length + 1;
    // each call pushes its own promise, so the driver waits for it instead of sleeping
    const mine = (async () => {
    calls.push({ sid, text });
    // OpenCode's promptAsync answers at once and the user message reaches every copy's chat.message afterwards
    for (const [k, h] of instances.entries()) {
      echoing.push((async () => {
        try {
          await h['chat.message']({ sessionID: sid, messageID: 'msg_echo_' + n, agent: 'build' },
            { message: { id: 'msg_echo_' + n, sessionID: sid, role: 'user' }, parts: [{ id: 'prt_echo_' + n + '_' + k, type: 'text', text }] });
          echoes.push('resolved');
        } catch (e) { echoes.push('threw'); }
      })());
    }
    return { data: {} };
    })();
    sent.push(mine);
    return mine;
  } } };
for (const [i, p] of scen.plugins.entries()) {
  const mod = await import(pathToFileURL(p).href + '?load=' + i);
  instances.push(await mod.RatchetPlugin({ client, directory: scen.dir, worktree: scen.dir }));
}
// wait for every re-prompt call handed over so far and for every echo it caused (an echo can start while waiting)
const settle = async () => {
  let n = -1;
  while (n !== sent.length + echoing.length) {
    n = sent.length + echoing.length;
    await Promise.all(sent.slice());
    await Promise.all(echoing.slice());
    await new Promise((r) => setImmediate(r));
  }
};
const counts = [];
for (let i = 0; i < scen.idles; i++) {
  const arg = { event: { type: 'session.idle', properties: { sessionID: scen.sid } } };
  await Promise.all(instances.map((h) => h.event(arg)));
  await settle();
  counts.push(calls.length);
}
await settle();
fs.writeFileSync(scen.out, JSON.stringify({ counts, calls, echoes, problems, n: instances.length }));
process.exit(0);
"""


def drive_x(w, runs, driver=None):
    """Like the planned file's drive(): load each run's plugin under node with the spy python3 first on PATH."""
    s = w.s
    n = s.next("xscenario")
    drv = s.work / f"{n}.driver.mjs"
    drv.write_text(driver or DRIVER_X, encoding="utf-8")
    scen, out_path, spy_path = s.work / f"{n}.json", s.work / f"{n}.out.json", s.work / f"{n}.spy.log"
    scen.write_text(json.dumps({"dir": str(w.proj), "out": str(out_path), "prefix": PREFIX, "runs": runs}), encoding="utf-8")
    env = w.env(PATH=str(s.spybin) + os.pathsep + os.environ.get("PATH", ""), OCSPY_LOG=str(spy_path))
    p = subprocess.run([S.NODE, str(drv), str(scen)], cwd=str(s.work), env=env, capture_output=True, text=True, timeout=300)
    return S.Out(p, out_path, spy_path)


def xrun(plugin, steps, msgs=None, dflt=None, no_messages=False, label="run", prompt=None):
    r = S.run_of(plugin, steps, label=label, prompt=prompt)
    r["msgs"] = msgs or {}
    if dflt is not None:
        r["msgsDflt"] = dflt
    r["noMessages"] = no_messages
    return r


# ----------------------------------------------------------------------------------------------- event builders

def status(kind, sid="ses_main"):
    return S.ev("session.status", {"sessionID": sid, "status": {"type": kind}})


def assistant_updated(sid="ses_main", **info):
    return S.ev("message.updated", {"sessionID": sid, "info": {"id": "msg_a", "sessionID": sid, "role": "assistant", **info}})


def aborted_info():
    return {"error": {"name": "MessageAbortedError", "data": {"message": "The operation was aborted."}}}


def msgs_of(*items):
    """A session.messages answer: `items` are the assistant `info` dicts, oldest first."""
    return {"data": [{"info": {"id": f"msg_{i}", "role": "assistant", **info}, "parts": []} for i, info in enumerate(items)]}


FINISHED = msgs_of({"finish": "stop", "time": {"completed": 2}})


def retry_cancel_steps():
    """The sequence recorded live on OpenCode 1.18.27 when the user cancels during a provider retry wait."""
    return [status("busy"), status("retry"), assistant_updated(time={"completed": 5}), status("idle"), S.idle()]


def between_steps_cancel():
    return [status("busy"), assistant_updated(**aborted_info()), status("idle"), S.idle()]


def stop_asks(o):
    return [(c[0] or {}).get("session_id") for c in o.engine_calls("stop")]


def mutate(w, label, old, new):
    """The rendered plugin with exactly one piece of text replaced (the replacement must apply), retargeted to a stub."""
    text = S.real_plugin().read_text(encoding="utf-8")
    assert text.count(old) == 1, (label, old, text.count(old))
    p = w.s.work / "plugins"
    p.mkdir(exist_ok=True)
    src = p / f"{w.s.next('xmut')}.src.js"
    src.write_text(text.replace(old, new), encoding="utf-8")
    return src


def stub_plugin(w, stub, src=None):
    cp, hits = S.retargeted_plugin(w, "xstub", stub, src=src)
    assert hits >= 1
    return cp


# ----------------------------------------------------------------------------------------------------------- X01

def v_x01(o):
    return [("setup: the plugin loads and every step resolves, nothing crashed", o.loaded() and o.clean() and S.all_resolved(o),
             (o.statuses(), o.problems)),
            ("a cancel during a provider retry wait (no session.error ever published) sends no re-prompt",
             o.final_n() == 0, o.ns()),
            ("and the engine is not even asked (its stop count is shared by the whole run)", stop_asks(o) == [], stop_asks(o))]


def case_x01():
    print("[CP6-X01] a cancel during a provider retry wait is never re-prompted")
    sw = S.stub_world()
    stub = S.const_stub(sw)
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), retry_cancel_steps(), no_messages=True)])
    S.emit("CP6-X01", v_x01(o))
    o_msg = drive_x(sw, [xrun(stub_plugin(sw, stub), retry_cancel_steps(), msgs={"ses_main": msgs_of({"time": {"completed": 5}})})])
    S.check("[CP6-X01] with the message lookup answering (last assistant message has no finish) it is still not re-prompted",
            o_msg.final_n() == 0, o_msg.ns())
    ref = drive_x(sw, [xrun(stub_plugin(sw, stub), retry_cancel_steps(), no_messages=True)])
    S.tooth("CP6-X01", "reference plugin", v_x01(ref), True)
    m = mutate(sw, "retry", "stopOf(sessionID).retry = true;", "void 0;")
    S.tooth("CP6-X01", "a plugin that does not read the retry status",
            v_x01(drive_x(sw, [xrun(stub_plugin(sw, stub, m), retry_cancel_steps(), no_messages=True)])), False)
    # the retry marker ends at the next busy: a recovered retry is a normal end (see X04)


# ----------------------------------------------------------------------------------------------------------- X02

def v_x02(o):
    ns = o.ns()
    return [("setup: the plugin loads and every step resolves, nothing crashed", o.loaded() and o.clean() and S.all_resolved(o),
             (o.statuses(), o.problems)),
            ("a cancel between steps (an assistant message.updated with a MessageAbortedError, no session.error) is not "
             "re-prompted at the idle", ns[:4] == [0, 0, 0, 0], ns[:4]),
            ("the engine is asked once only, for the idle after the user typed", stop_asks(o) == ["ses_main"], stop_asks(o)),
            ("a later idle with no new typing is still held", ns[4] == 0, ns),
            ("after the user types, the next idle re-prompts again", ns[-1] == 1, ns)]


def case_x02():
    print("[CP6-X02] a cancel between loop steps is read from the assistant message and held until the user types")
    sw = S.stub_world()
    stub = S.const_stub(sw)
    steps = between_steps_cancel() + [S.idle(), S.typed("go on"), S.idle()]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), steps, no_messages=True)])
    S.emit("CP6-X02", v_x02(o))
    m = mutate(sw, "msgupd", 'info.error && info.error.name === "MessageAbortedError"', "false")
    o2 = drive_x(sw, [xrun(stub_plugin(sw, stub, m), steps, no_messages=True)])
    S.tooth("CP6-X02", "a plugin that ignores the aborted assistant message", v_x02(o2), False)
    S.tooth("CP6-X02", "reference plugin", v_x02(drive_x(sw, [xrun(stub_plugin(sw, stub), steps, no_messages=True)])), True)


# ----------------------------------------------------------------------------------------------------------- X03

def lookup_cases():
    return [("last assistant message carries a MessageAbortedError", {"ses_main": msgs_of({"finish": "stop"}, {**aborted_info(), "finish": "stop"})}, 0),
            ("last assistant message has no finish", {"ses_main": msgs_of({"finish": "stop"}, {"time": {"completed": 3}})}, 0),
            ("last assistant message carries a provider error", {"ses_main": msgs_of({"error": {"name": "APIError"}})}, 0),
            ("no assistant message at all", {"ses_main": {"data": []}}, 0),
            ("last assistant message finished with tool-calls (OpenCode ended the turn after a rejected permission)",
             {"ses_main": msgs_of({"finish": "stop"}, {"finish": "tool-calls"})}, 0),
            ("last assistant message finished with unknown", {"ses_main": msgs_of({"finish": "stop"}, {"finish": "unknown"})}, 0),
            ("the lookup answers an error object", {"ses_main": {"error": {"name": "NotFoundError"}}}, 0),
            ("the lookup answers something that is not a list", {"ses_main": {"data": {"a": 1}}}, 0),
            ("the lookup rejects", {"ses_main": {"$reject": True}}, 0),
            ("the lookup throws synchronously", {"ses_main": {"$throw": True}}, 0),
            ("control: the last assistant message is finished", {"ses_main": FINISHED}, 1)]


def v_x03(o, expect):
    return [("setup: the plugin loads and the idle resolves, nothing crashed", o.loaded() and o.clean() and S.all_resolved(o),
             (o.statuses(), o.problems)),
            (f"{expect} re-prompt(s) sent", o.ns()[:1] == [expect], o.ns()),
            ("it asked the session's messages exactly once",
             sum(1 for c in o.run().get("calls") or [] if c.get("fn") == "session.messages") == 1, None)]


def case_x03():
    print("[CP6-X03] the last assistant message is checked right before a re-prompt")
    sw = S.stub_world()
    stub = S.const_stub(sw)
    for label, table, expect in lookup_cases():
        o = drive_x(sw, [xrun(stub_plugin(sw, stub), [S.idle(), {"op": "timers"}], msgs=table)])
        res = list(v_x03(o, expect))
        timers = o.step(0, 1).get("pending")
        res.append(("no timer is left behind by the lookup", timers == [], timers))
        S.emit("CP6-X03", [(f"{label}: {l}", c, d) for l, c, d in res])
    # a lookup that never answers: the plugin gives up after its bounded wait and sends nothing (real 10 s wait)
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), [S.idle(capMs=60000), {"op": "timers"}], msgs={"ses_main": {"$hang": True}})])
    S.check("[CP6-X03] a lookup that never answers: the idle handler still settles and sends nothing",
            o.loaded() and o.clean() and o.ns() == [0, 0] and o.statuses()[0] == "resolved", (o.statuses(), o.ns(), o.problems))
    S.check("[CP6-X03] a lookup that never answers: no timer is left behind", o.step(0, 1).get("pending") == [], o.step(0, 1))
    # a client with no `session.messages` at all cannot be asked: the other cancel signals stand alone and it sends
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), [S.idle()], no_messages=True)])
    S.check("[CP6-X03] a client without session.messages is not blocked (one re-prompt)", o.ns() == [1], o.ns())
    # teeth
    for name, old, new, expect_ok in (
            ("a plugin that never looks at the last message", 'if (!client || !client.session || typeof client.session.messages !== "function") return true;',
             "return true;", False),
            ("a plugin that sends when the last message has no finish",
             'return typeof info.finish === "string" && info.finish !== "" && info.finish !== "tool-calls" && info.finish !== "unknown";',
             "return true;", False),
            ("a plugin that accepts any non-empty finish (tool-calls and unknown too)",
             'return typeof info.finish === "string" && info.finish !== "" && info.finish !== "tool-calls" && info.finish !== "unknown";',
             'return typeof info.finish === "string" && info.finish !== "";', False),
            ("a plugin that sends when the last message has an error", "if (info.error) return false;", "", False)):
        m = mutate(sw, name, old, new)
        bad = 0
        for label, table, expect in lookup_cases():
            o = drive_x(sw, [xrun(stub_plugin(sw, stub, m), [S.idle()], msgs=table)])
            if o.ns() != [expect]:
                bad += 1
        S.check(f"[CP6-X03] teeth: {name} is caught by at least one lookup case", bad >= 1, bad)
    # the lookup's own timer is cleared: without the clearTimeout the 10 s timer is still pending afterwards
    m = mutate(sw, "timer", "    if (timer) clearTimeout(timer);\n  }\n}\n\n// An id for the guidance part",
               "  }\n}\n\n// An id for the guidance part")
    o = drive_x(sw, [xrun(stub_plugin(sw, stub, m), [S.idle(), {"op": "timers"}], msgs={"ses_main": FINISHED})])
    S.check("[CP6-X03] teeth: a lookup that does not clear its timer leaves one behind (the timer check can fail)",
            o.step(0, 1).get("pending") != [], o.step(0, 1))


# ----------------------------------------------------------------------------------------------------------- X04

def case_x04():
    print("[CP6-X04] a normal turn end still re-prompts exactly once")
    w = S.fresh()
    steps = [status("busy"), assistant_updated(finish="stop", time={"completed": 4}), status("idle"), S.idle()]
    o = drive_x(w, [xrun(w.plugin, steps, msgs={"ses_main": FINISHED})])
    S.emit("CP6-X04", [("setup: the plugin loads and every step resolves, nothing crashed",
                        o.loaded() and o.clean() and S.all_resolved(o), (o.statuses(), o.problems)),
                       ("a normal sequence (busy, finished assistant message, idle) re-prompts exactly once with the real engine",
                        o.final_n() == 1 and o.sids() == ["ses_main"], (o.ns(), o.sids())),
                       ("the engine was asked once", stop_asks(o) == ["ses_main"], stop_asks(o))])
    sw = S.stub_world()
    stub = S.const_stub(sw)
    rec = [status("busy"), status("retry"), status("busy"), assistant_updated(finish="stop"), status("idle"), S.idle()]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), rec, msgs={"ses_main": FINISHED})])
    S.check("[CP6-X04] a retry that recovered (busy again before the idle) is a normal end: one re-prompt",
            o.loaded() and o.clean() and o.final_n() == 1, (o.ns(), o.problems))
    after_cancel = retry_cancel_steps() + [S.typed("never mind"), status("busy"), assistant_updated(finish="stop"), status("idle"), S.idle()]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), after_cancel, msgs={"ses_main": FINISHED})])
    S.check("[CP6-X04] after a retry cancel and the user typing, the next idle re-prompts once",
            o.loaded() and o.clean() and o.final_n() == 1, (o.ns(), o.problems))
    m = mutate(sw, "busy", 'if (st) st.retry = false;\n      }\n    } else if (event.type === "session.deleted")',
               'void 0;\n      }\n    } else if (event.type === "session.deleted")')
    o = drive_x(sw, [xrun(stub_plugin(sw, stub, m), rec, no_messages=True)])
    S.check("[CP6-X04] teeth: a plugin where busy does not end the retry marker wrongly stays silent after a recovered retry",
            o.final_n() == 0, o.ns())


# ----------------------------------------------------------------------------------------------------------- X05

def case_x05():
    print("[CP6-X05] two installed copies send one re-prompt per idle and never record their own message")
    w = S.fresh(max_stop_blocks=10)
    before = S.log_entries(w)
    text = S.real_plugin().read_text(encoding="utf-8")
    d = w.s.work / "plugins"
    d.mkdir(exist_ok=True)
    pa, pb = d / f"{w.s.next('user')}.js", d / f"{w.s.next('project')}.js"
    pa.write_text(text, encoding="utf-8")
    pb.write_text(text, encoding="utf-8")

    def run_two(a, b, idles=6):
        n = w.s.next("two")
        drv, scen, out = w.s.work / f"{n}.mjs", w.s.work / f"{n}.json", w.s.work / f"{n}.out.json"
        spy = w.s.work / f"{n}.spy.log"
        drv.write_text(DRIVER_TWO, encoding="utf-8")
        scen.write_text(json.dumps({"dir": str(w.proj), "out": str(out), "plugins": [str(a), str(b)], "idles": idles,
                                    "sid": "ses_main", "messages": FINISHED["data"]}), encoding="utf-8")
        env = w.env(PATH=str(w.s.spybin) + os.pathsep + os.environ.get("PATH", ""), OCSPY_LOG=str(spy))
        p = subprocess.run([S.NODE, str(drv), str(scen)], cwd=str(w.proj), env=env, capture_output=True, text=True, timeout=300)
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"counts": [], "calls": [], "echoes": [], "problems": [f"no result: {p.stderr[-300:]}"], "n": 0}
        return data, S.Out(p, out, spy)

    def verdict(data, o):
        calls = data["calls"]
        toks = {S.marker_token(c["text"]) for c in calls}
        return [("setup: both copies load, nothing crashed",
                 data["n"] == 2 and data["problems"] == [], (data["n"], data["problems"])),
                ("exactly one re-prompt per idle until the in-a-row cap: 1, 2, 3, then 3 over six idles",
                 data["counts"] == [1, 2, 3, 3, 3, 3], data["counts"]),
                ("only three engine stop calls: the second copy returns before asking the engine", len(o.engine_calls("stop")) == 3,
                 len(o.engine_calls("stop"))),
                ("every re-prompt is one marked message", all(c["text"].split("\n", 1)[0].startswith(PREFIX) for c in calls),
                 [c["text"][:60] for c in calls]),
                ("the echoes reached both copies' chat.message (6 deliveries for 3 re-prompts) and none threw",
                 data["echoes"] == ["resolved"] * 6, data["echoes"]),
                ("no re-prompt from either copy was recorded as the user's reply (human_log unchanged)",
                 S.log_entries(w) == before and not any(PREFIX in json.dumps(e) for e in S.log_entries(w)),
                 (len(before), len(S.log_entries(w)))),
                ("three re-prompts were sent in all, each with a marker token", len(calls) == 3 and None not in toks, toks)]

    data, o = run_two(pa, pb)
    S.emit("CP6-X05", verdict(data, o))
    # teeth: copies that keep their own state per load double the re-prompts
    m = mutate(w, "per-load", "const stops = globalThis[STOP_SLOT];", "const stops = new Map();")
    ma = w.s.work / "plugins" / f"{w.s.next('mutA')}.js"
    ma.write_text(m.read_text(encoding="utf-8"), encoding="utf-8")
    data2, _o2 = run_two(ma, ma)
    S.check("[CP6-X05] teeth: copies with per-load state send two re-prompts for one idle (counts 2, 4, 6)",
            data2["counts"][:3] == [2, 4, 6], data2["counts"])


# ----------------------------------------------------------------------------------------------------------- X06

def v_x06(o):
    return [("setup: the plugin loads and every step resolves, nothing crashed", o.loaded() and o.clean() and S.all_resolved(o),
             (o.statuses(), o.problems)),
            ("after a provider error (a session.error that is not a cancel) the next idle sends nothing and asks nothing "
             "(the engine is asked for the other session, then for this one only after the user typed)",
             o.ns()[1] == 0 and stop_asks(o) == ["ses_other", "ses_main"], (o.ns(), stop_asks(o))),
            ("another session is not held", o.ns()[2] == 1 and o.sids()[:1] == ["ses_other"], (o.ns(), o.sids())),
            ("after the user types, the held session is re-prompted again", o.ns()[-1] == 2 and o.sids()[-1] == "ses_main",
             (o.ns(), o.sids()))]


def case_x06():
    print("[CP6-X06] a provider error holds the session until the user types")
    sw = S.stub_world()
    stub = S.const_stub(sw)
    err = S.ev("session.error", {"sessionID": "ses_main", "error": {"name": "APIError", "data": {"message": "overloaded"}}})
    steps = [err, S.idle(), S.idle("ses_other"), S.typed("try again"), S.idle()]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), steps, no_messages=True)])
    S.emit("CP6-X06", v_x06(o))
    m = mutate(sw, "anyerr", 'if (!(props.error && props.error.name === "ContextOverflowError")) stopOf(sessionID).stopped = true;',
               'if (props.error && props.error.name === "MessageAbortedError") stopOf(sessionID).stopped = true;')
    o2 = drive_x(sw, [xrun(stub_plugin(sw, stub, m), steps, no_messages=True)])
    S.check("[CP6-X06] teeth: a plugin that holds only on a cancel re-prompts after a provider error", o2.ns()[1] == 1, o2.ns())


# ----------------------------------------------------------------------------------------------------------- X07

def case_x07():
    print("[CP6-X07] the shared stop state is bounded and tidy")
    sw = S.stub_world()
    stub = S.const_stub(sw)
    errs = [S.ev("session.error", {"sessionID": f"ses_n{i}", "error": {"name": "APIError"}}) for i in range(2100)]
    steps = errs + [{"op": "slot", "ids": ["ses_n0", "ses_n100", "ses_n2099"]}]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), steps, no_messages=True)])
    slot = o.step(0, len(steps) - 1)
    S.check("[CP6-X07] the plugin keeps its state in a Map at globalThis[Symbol.for('ratchet.opencode.stop.v1')]",
            o.loaded() and o.clean() and slot.get("isMap") is True, (slot, o.problems))
    S.check("[CP6-X07] after 2100 distinct sessions the Map holds at most 2000 (the oldest are dropped, the newest kept)",
            slot.get("size") == 2000 and slot.get("has") == ["ses_n100", "ses_n2099"], slot)
    # session.deleted drops the entry: a cancelled session that is deleted is no longer held
    steps = [S.abort_ev("ses_main"), {"op": "slot", "ids": ["ses_main"]}, S.ev("session.deleted", {"info": {"id": "ses_main"}}),
             {"op": "slot", "ids": ["ses_main"]}]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), steps, no_messages=True)])
    S.check("[CP6-X07] a session.deleted event drops that session's entry",
            o.step(0, 1).get("has") == ["ses_main"] and o.step(0, 3).get("has") == [], (o.step(0, 1), o.step(0, 3)))
    # a sub-agent's session (parentID) leaves no entry behind after its idle
    sub = S.TABLE["ses_child"]
    steps = [S.idle("ses_child"), {"op": "slot", "ids": ["ses_child"]}, S.idle("ses_main"), {"op": "slot", "ids": ["ses_main"]}]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), steps, no_messages=True)])
    S.check("[CP6-X07] a sub-agent session leaves no entry after its idle, while a top-level one does",
            o.step(0, 1).get("has") == [] and o.step(0, 3).get("has") == ["ses_main"] and sub["data"]["parentID"],
            (o.step(0, 1), o.step(0, 3)))
    # the 2000-entry bound: without the eviction the Map keeps every session
    m = mutate(sw, "evict", "if (stops.size > STOP_LIMIT) stops.delete(stops.keys().next().value);", "void 0;")
    steps_n = errs + [{"op": "slot", "ids": ["ses_n0", "ses_n100", "ses_n2099"]}]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub, m), steps_n, no_messages=True)])
    S.check("[CP6-X07] teeth: a plugin that never evicts keeps all 2100 entries (the bound check can fail)",
            o.step(0, len(steps_n) - 1).get("size") == 2100, o.step(0, len(steps_n) - 1))
    # session.deleted: without the branch the entry stays
    steps_d = [S.abort_ev("ses_main"), S.ev("session.deleted", {"info": {"id": "ses_main"}}), {"op": "slot", "ids": ["ses_main"]}]
    m = mutate(sw, "nodelete", 'event.type === "session.deleted") {\n      stops.delete(sessionID);',
               'event.type === "session.deleted") {\n      void 0;')
    o = drive_x(sw, [xrun(stub_plugin(sw, stub, m), steps_d, no_messages=True)])
    S.check("[CP6-X07] teeth: a plugin that ignores session.deleted keeps the entry (the cleanup check can fail)",
            o.step(0, 2).get("has") == ["ses_main"], o.step(0, 2))
    m = mutate(sw, "keep", "if (created && !st.stopped && !st.retry && stops.get(sessionID) === st) stops.delete(sessionID);", "void 0;")
    o = drive_x(sw, [xrun(stub_plugin(sw, stub, m), steps, no_messages=True)])
    S.check("[CP6-X07] teeth: a plugin that keeps the entry of a sub-agent session is caught",
            o.step(0, 1).get("has") == ["ses_child"], o.step(0, 1))


# ----------------------------------------------------------------------------------------------------------- X08

def reject_steps(reply="reject"):
    """OpenCode's sequence when a tool asks for permission and the user answers: the turn then ends by itself."""
    return [status("busy"),
            S.ev("permission.asked", {"id": "per_1", "sessionID": "ses_main", "permission": "bash"}),
            S.ev("permission.replied", {"sessionID": "ses_main", "requestID": "per_1", "reply": reply}),
            status("idle"), S.idle()]


def v_x08(o):
    return [("setup: the plugin loads and every step resolves, nothing crashed", o.loaded() and o.clean() and S.all_resolved(o),
             (o.statuses(), o.problems)),
            ("a rejected permission request ends the turn and is not re-prompted at the idle", o.final_n() == 0, o.ns()),
            ("and the engine is not asked", stop_asks(o) == [], stop_asks(o))]


def v_x08_question(o):
    return [("setup: the plugin loads and every step resolves, nothing crashed", o.loaded() and o.clean() and S.all_resolved(o),
             (o.statuses(), o.problems)),
            ("a dismissed question ends the turn and is not re-prompted at the idle", o.final_n() == 0, o.ns()),
            ("and the engine is not asked", stop_asks(o) == [], stop_asks(o))]


def case_x08():
    print("[CP6-X08] a rejected permission or a dismissed question ends the turn itself and is not re-prompted")
    sw = S.stub_world()
    stub = S.const_stub(sw)
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), reject_steps(), no_messages=True)])
    S.emit("CP6-X08", v_x08(o))
    m = mutate(sw, "noperm", 'if (event.type === "question.rejected" || props.reply === "reject") stopOf(sessionID).stopped = true;',
               "void 0;")
    S.tooth("CP6-X08", "a plugin that ignores permission.replied and question.rejected",
            v_x08(drive_x(sw, [xrun(stub_plugin(sw, stub, m), reject_steps(), no_messages=True)])), False)
    # control: an approved permission is a normal turn end and still re-prompts once
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), reject_steps("once"), msgs={"ses_main": FINISHED})])
    S.check("[CP6-X08] control: a permission answered once (not rejected) still re-prompts exactly once",
            o.loaded() and o.clean() and o.final_n() == 1, (o.ns(), o.problems))
    # a typed reply ends the hold
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), reject_steps() + [S.typed("ok, do it differently"), S.idle()], no_messages=True)])
    S.check("[CP6-X08] after a rejected permission and the user typing, the next idle re-prompts again",
            o.loaded() and o.clean() and o.ns()[-1] == 1, (o.ns(), o.problems))
    # the dismissed question
    qsteps = [status("busy"), S.ev("question.asked", {"id": "que_1", "sessionID": "ses_main", "questions": []}),
              S.ev("question.rejected", {"sessionID": "ses_main", "requestID": "que_1"}), status("idle"), S.idle()]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), qsteps, no_messages=True)])
    S.emit("CP6-X08", [(f"question.rejected: {l}", c, d) for l, c, d in v_x08_question(o)])
    S.tooth("CP6-X08", "a plugin that ignores question.rejected (the dismissed question)",
            v_x08_question(drive_x(sw, [xrun(stub_plugin(sw, stub, mutate(
                sw, "noq", 'event.type === "question.rejected" || props.reply === "reject"', 'props.reply === "reject"')),
                qsteps, no_messages=True)])), False)
    # the other half: the turn really finished on tool-calls (the lookup refuses it): X03 holds those cases


# ----------------------------------------------------------------------------------------------------------- X09

def case_x09():
    print("[CP6-X09] a recovered context overflow does not hold the session")
    sw = S.stub_world()
    stub = S.const_stub(sw)
    overflow = S.ev("session.error", {"sessionID": "ses_main", "error": {"name": "ContextOverflowError", "data": {"message": "too long"}}})
    steps = [status("busy"), overflow, assistant_updated(finish="stop", time={"completed": 4}), status("idle"), S.idle()]
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), steps, msgs={"ses_main": FINISHED})])
    S.check("[CP6-X09] a ContextOverflowError, then a finished turn and an idle: re-prompted exactly once",
            o.loaded() and o.clean() and o.final_n() == 1, (o.ns(), o.problems))
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), [overflow, S.idle()], msgs={"ses_main": msgs_of({"error": {"name": "ContextOverflowError"}})})])
    S.check("[CP6-X09] a turn that really ended on the overflow (the last assistant message has the error) is still not re-prompted",
            o.loaded() and o.clean() and o.final_n() == 0, (o.ns(), o.problems))
    m = mutate(sw, "ovf", 'if (!(props.error && props.error.name === "ContextOverflowError")) stopOf(sessionID).stopped = true;',
               "stopOf(sessionID).stopped = true;")
    o = drive_x(sw, [xrun(stub_plugin(sw, stub, m), steps, msgs={"ses_main": FINISHED})])
    S.check("[CP6-X09] teeth: a plugin that holds on a ContextOverflowError stays silent after the recovered turn",
            o.loaded() and o.final_n() == 0, o.ns())


# ----------------------------------------------------------------------------------------------------------- X10

def case_x10():
    print("[CP6-X10] the message lookup asks for the newest 10 messages only")
    sw = S.stub_world()
    stub = S.const_stub(sw)

    def lookups(o):
        return [c for c in o.run().get("calls") or [] if c.get("fn") == "session.messages"]

    def v_x10(o):
        ls = lookups(o)
        opts = (ls[0].get("opts") if ls and isinstance(ls[0].get("opts"), dict) else {})
        return [("setup: the plugin loads and the idle resolves", o.loaded() and o.clean() and S.all_resolved(o), (o.statuses(), o.problems)),
                ("the lookup is made once, for the session, with query limit 10",
                 len(ls) == 1 and (opts.get("path") or {}).get("id") == "ses_main" and (opts.get("query") or {}).get("limit") == 10, ls),
                ("an old finished message followed by a newer unfinished one is read from the end: no re-prompt", o.final_n() == 0, o.ns())]

    older_first = msgs_of({"finish": "stop"}, {"finish": "tool-calls"})
    o = drive_x(sw, [xrun(stub_plugin(sw, stub), [S.idle()], msgs={"ses_main": older_first})])
    S.emit("CP6-X10", v_x10(o))
    m = mutate(sw, "nolimit", "client.session.messages({ path: { id: sessionID }, query: { limit: 10 } })",
               "client.session.messages({ path: { id: sessionID } })")
    S.tooth("CP6-X10", "a plugin that drops the limit",
            v_x10(drive_x(sw, [xrun(stub_plugin(sw, stub, m), [S.idle()], msgs={"ses_main": older_first})])), False)
    m = mutate(sw, "fromfront", "for (let i = got.data.length - 1; i >= 0; i--) {", "for (let i = 0; i < got.data.length; i++) {")
    S.tooth("CP6-X10", "a plugin that reads the messages from the front",
            v_x10(drive_x(sw, [xrun(stub_plugin(sw, stub, m), [S.idle()], msgs={"ses_main": older_first})])), False)


CASES = [("CP6-X01", case_x01), ("CP6-X02", case_x02), ("CP6-X03", case_x03), ("CP6-X04", case_x04),
         ("CP6-X05", case_x05), ("CP6-X06", case_x06), ("CP6-X07", case_x07),
         ("CP6-X08", case_x08), ("CP6-X09", case_x09), ("CP6-X10", case_x10)]


def main():
    S.need_node()
    for cid, fn in CASES:
        S.guarded(fn, cid)
    print(f"\n{S.PASS} passed, {S.FAIL} failed")
    return 1 if S.FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
