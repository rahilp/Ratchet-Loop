import { chromium } from 'playwright';
import path from 'node:path'; import fs from 'node:fs';
const [,, page, outDir, from = '0', to = '', step = '1'] = process.argv;
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell' });
const pg = await b.newPage({ viewport: { width: 1920, height: 1080 } });
pg.on('pageerror', e => console.log('err', e.message));
await pg.goto('file://' + path.resolve(page));
await pg.waitForFunction('window.ready === true', null, { timeout: 60000 });
const total = await pg.evaluate('window.FRAMES');
fs.mkdirSync(outDir, { recursive: true });
for (let i = +from; i < (to === '' ? total : +to); i += +step) {
  await pg.evaluate(i => window.renderFrame(i), i);
  await pg.screenshot({ path: path.join(outDir, `f${String(i).padStart(4, '0')}.png`) });
}
await b.close();
