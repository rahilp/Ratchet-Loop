import { chromium } from 'playwright';
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
const [,, outDir, W='1920', H='1080', from='0', to='', step='1'] = process.argv;
const root = process.cwd();
const srv = http.createServer((q, r) => { const f = path.join(root, decodeURIComponent(q.url.split('?')[0]));
  fs.readFile(f, (e, d) => { if (e) { r.writeHead(404); return r.end(); }
    r.writeHead(200, {'content-type': f.endsWith('.js') ? 'text/javascript' : 'text/html'}); r.end(d); }); }).listen(0);
const port = srv.address().port;
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell', args: ['--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist'] });
const pg = await b.newPage({ viewport: { width: +W, height: +H } });
pg.on('console', m => console.log('console:', m.text())); pg.on('pageerror', e => console.log('err:', e.message));
await pg.goto(`http://127.0.0.1:${port}/scene.html?w=${W}&h=${H}&${process.env.Q || ""}`);
await pg.waitForFunction('window.ready === true', null, { timeout: 120000 });
const total = await pg.evaluate('window.FRAMES');
fs.mkdirSync(outDir, { recursive: true });
await pg.evaluate(() => { window.renderFrame(1); window.renderFrame(2); });
const end = to === '' ? total : +to;
for (let i = +from; i < end; i += +step) {
  const t0 = Date.now();
  await pg.evaluate(i => window.renderFrame(i), i);
  await pg.screenshot({ path: path.join(outDir, `f${String(i).padStart(4,'0')}.png`) });
  if (i % 10 === 0 || +step > 1) console.log('frame', i, Date.now() - t0, 'ms');
}
await b.close(); srv.close();
