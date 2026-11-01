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
// This step only installs the file: the plugin below returns no hooks, so it enforces nothing yet.
// Edit nothing here: `ratchet.py install-hooks --host opencode` rewrites this file, and removes it with --uninstall.

const RATCHET_ENGINE = __RATCHET_ENGINE_PATH__;

export const RatchetPlugin = async (ctx) => {
  void ctx;
  void RATCHET_ENGINE;
  return {};
};
