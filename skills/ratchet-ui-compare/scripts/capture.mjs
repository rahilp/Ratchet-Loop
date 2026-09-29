#!/usr/bin/env node
/**
 * capture.mjs — capture the reference (prototype / old app / design) and the real app
 * in MATCHING states, plus a step-by-step journey on both, for the Ratchet UI gate.
 *
 *   node capture.mjs --spec .ratchet/runs/<slug>/states/CP1.json --out .ratchet/runs/<slug>/reviews/CP1/capture
 *   node capture.mjs --smoke path/to/prototype.html          # render every ?state= and report errors
 *
 * Needs Playwright:  npm i -D playwright && npx playwright install chromium
 * (or set PLAYWRIGHT_MODULE to a resolvable module path, or CHROMIUM_PATH to a browser binary)
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith("--") ? arr[i + 1] : true]);
    return acc;
  }, [])
);

async function loadPlaywright() {
  const tries = [process.env.PLAYWRIGHT_MODULE, "playwright", "@playwright/test", "playwright-core"].filter(Boolean);
  const req = createRequire(path.join(process.cwd(), "noop.js"));
  for (const m of tries) {
    for (const load of [() => import(pathToFileURL(req.resolve(m)).href), () => import(m)]) {
      try { const mod = await load(); const pw = mod.chromium ? mod : mod.default; if (pw?.chromium) return pw; } catch {}
    }
  }
  console.error("RATCHET capture: Playwright not found. Install it in the project:\n  npm i -D playwright && npx playwright install chromium");
  process.exit(3);
}

function toUrl(base, sub = "") {
  if (!base) throw new Error("missing target base");
  if (/^https?:\/\//.test(base) || base.startsWith("file://")) {
    if (!sub) return base;
    if (/^https?:\/\//.test(sub)) return sub;
    if (sub.startsWith("?") || sub.startsWith("#")) return base.replace(/[?#].*$/, "") + sub;
    return new URL(sub, base.endsWith("/") ? base : base + "/").href;
  }
  // local file path
  const abs = path.resolve(base);
  return pathToFileURL(abs).href + (sub && (sub.startsWith("?") || sub.startsWith("#")) ? sub : "");
}

async function runStep(page, step, base) {
  const t = 15000;
  if (step.goto) return page.goto(toUrl(base, step.goto), { waitUntil: "load", timeout: 30000 });
  if (step.click) return page.locator(step.click).first().click({ timeout: t });
  if (step.fill) return page.locator(step.fill).first().fill(String(step.value ?? ""), { timeout: t });
  if (step.press) return page.keyboard.press(step.press);
  if (step.hover) return page.locator(step.hover).first().hover({ timeout: t });
  if (step.select) return page.locator(step.select).first().selectOption(String(step.value), { timeout: t });
  if (step.check) return page.locator(step.check).first().check({ timeout: t });
  if (step.uncheck) return page.locator(step.uncheck).first().uncheck({ timeout: t });
  if (step.wait) return page.waitForTimeout(Number(step.wait));
  if (step.waitFor) return page.locator(step.waitFor).first().waitFor({ timeout: t });
  if (step.scroll) {
    if (step.scroll === "bottom") return page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    return page.locator(step.scroll).first().scrollIntoViewIfNeeded({ timeout: t });
  }
  throw new Error("unknown step " + JSON.stringify(step));
}

async function settle(page) {
  try { await page.waitForLoadState("networkidle", { timeout: 5000 }); } catch {}
  try { await page.evaluate(() => document.fonts && document.fonts.ready); } catch {}
  await page.addStyleTag({ content: "*,*::before,*::after{animation-duration:0s!important;transition-duration:0s!important;caret-color:transparent!important}" }).catch(() => {});
  await page.waitForTimeout(250);
}

async function aria(page) {
  try { return await page.locator("body").ariaSnapshot(); } catch {}
  try { return JSON.stringify(await page.accessibility.snapshot(), null, 1); } catch {}
  return "";
}

async function captureTarget(browser, spec, state, target, outDir) {
  const base = spec.targets[target];
  const side = state[target] || {};
  const ctx = await browser.newContext({
    viewport: spec.viewport || { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
    colorScheme: spec.colorScheme || "light",
    locale: spec.locale || "en-US",
    timezoneId: spec.timezone || "UTC",
  });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on("console", (m) => m.type() === "error" && !/favicon/i.test(m.text() + (m.location()?.url || "")) && consoleErrors.push(m.text().slice(0, 300)));
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + String(e).slice(0, 300)));
  const rec = { target, url: null, screenshot: null, aria: null, steps: [], journey: [], consoleErrors };
  try {
    for (const s of (spec.login && spec.login[target]) || []) await runStep(page, s, base);
    rec.url = toUrl(base, side.path || "");
    await page.goto(rec.url, { waitUntil: "load", timeout: 30000 });
    for (const s of side.steps || []) {
      try { await runStep(page, s, base); rec.steps.push({ step: s, ok: true }); }
      catch (e) { rec.steps.push({ step: s, ok: false, error: String(e).split("\n")[0] }); }
    }
    await settle(page);
    const shot = path.join(outDir, `${target}.png`);
    const mask = (state.mask || []).map((sel) => page.locator(sel));
    await page.screenshot({ path: shot, fullPage: !!state.full_page, mask });
    rec.screenshot = shot;
    const a = await aria(page);
    if (a) { rec.aria = path.join(outDir, `${target}.aria.txt`); fs.writeFileSync(rec.aria, a); }
    // behaviour journey: same steps on both sides, screenshot after each
    let i = 0;
    for (const s of state.journey || []) {
      i++;
      const r = { i, step: s, ok: true };
      try { await runStep(page, s, base); } catch (e) { r.ok = false; r.error = String(e).split("\n")[0]; }
      await settle(page);
      r.screenshot = path.join(outDir, `${target}-journey-${String(i).padStart(2, "0")}.png`);
      await page.screenshot({ path: r.screenshot, fullPage: false });
      r.url = page.url();
      rec.journey.push(r);
    }
  } catch (e) {
    rec.fatal = String(e).split("\n")[0];
  }
  await ctx.close();
  return rec;
}

async function launch(pw) {
  const opts = { headless: true };
  if (process.env.CHROMIUM_PATH) opts.executablePath = process.env.CHROMIUM_PATH;
  try { return await pw.chromium.launch(opts); }
  catch (e) {
    for (const p of ["/opt/pw-browsers/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]) {
      if (fs.existsSync(p)) { try { return await pw.chromium.launch({ ...opts, executablePath: p }); } catch {} }
    }
    throw e;
  }
}

async function smoke(file) {
  const pw = await loadPlaywright();
  const browser = await launch(pw);
  const html = fs.readFileSync(file, "utf8");
  const m = html.match(/RATCHET-PROTOTYPE states:\s*([^>]*?)\s*-->/);
  const states = m ? m[1].split(",").map((s) => s.trim()).filter(Boolean) : ["default"];
  const out = path.join(path.dirname(file), "smoke");
  fs.mkdirSync(out, { recursive: true });
  let bad = 0;
  for (const st of states) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errs = [];
    page.on("pageerror", (e) => errs.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errs.push(m.text()));
    await page.goto(toUrl(file, `?state=${encodeURIComponent(st)}`));
    await settle(page);
    const text = await page.locator("body").innerText();
    if (/\bundefined\b|\bNaN\b|\[object Object\]/.test(text)) errs.push("rendered text contains undefined/NaN/[object Object]");
    await page.screenshot({ path: path.join(out, `${st}.png`) });
    console.log(`${errs.length ? "✗" : "✓"} state=${st}${errs.length ? "  " + errs.join(" | ").slice(0, 300) : ""}`);
    bad += errs.length ? 1 : 0;
    await page.close();
  }
  await browser.close();
  console.log(`screenshots: ${out}`);
  process.exit(bad ? 1 : 0);
}

async function main() {
  if (args.smoke) return smoke(String(args.smoke));
  if (!args.spec || !args.out) {
    console.error("usage: capture.mjs --spec states/CPn.json --out <dir>   |   capture.mjs --smoke prototype.html");
    process.exit(2);
  }
  const spec = JSON.parse(fs.readFileSync(String(args.spec), "utf8"));
  for (const k of ["reference", "app"]) if (!spec.targets?.[k]) { console.error(`spec.targets.${k} missing`); process.exit(2); }
  const pw = await loadPlaywright();
  const browser = await launch(pw);
  const outRoot = path.resolve(String(args.out));
  fs.mkdirSync(outRoot, { recursive: true });
  const manifest = { checkpoint: spec.checkpoint, viewport: spec.viewport || { width: 1280, height: 800 },
                     captured_at: new Date().toISOString(), states: [] };
  for (const state of spec.states || []) {
    const dir = path.join(outRoot, state.name.replace(/[^a-z0-9_-]+/gi, "-"));
    fs.mkdirSync(dir, { recursive: true });
    const [ref, app] = await Promise.all([
      captureTarget(browser, spec, state, "reference", dir),
      captureTarget(browser, spec, state, "app", dir),
    ]);
    manifest.states.push({ name: state.name, scope: state.scope || "", dir, reference: ref, app });
    const warn = [ref, app].filter((r) => r.fatal || r.steps.some((s) => !s.ok));
    console.log(`${warn.length ? "!" : "✓"} ${state.name}: ref=${ref.fatal || "ok"} app=${app.fatal || "ok"}` +
      `${ref.consoleErrors.length + app.consoleErrors.length ? `  console errors: ref ${ref.consoleErrors.length}, app ${app.consoleErrors.length}` : ""}`);
  }
  await browser.close();
  const mp = path.join(outRoot, "manifest.json");
  fs.writeFileSync(mp, JSON.stringify(manifest, null, 2));
  console.log(`manifest: ${mp}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
