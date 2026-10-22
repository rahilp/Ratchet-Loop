(() => {
  'use strict';
  const doc = document.documentElement;
  doc.classList.add('js');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const saveData = navigator.connection && navigator.connection.saveData;

  // reveal the hero once the display face is in, so the headline never reflows in view
  const fontsIn = new Promise(res => {
    const link = document.querySelector('link[data-fonts]');
    const go = () => Promise.all([document.fonts.load('600 64px Geist'), document.fonts.load('400 17px Geist')]).then(res, res);
    if (!link || !document.fonts) return res();
    if (link.rel === 'stylesheet' && link.sheet) go(); else link.addEventListener('load', () => setTimeout(go, 0), { once: true });
  });
  Promise.race([fontsIn, new Promise(r => setTimeout(r, 1500))])
    .then(() => requestAnimationFrame(() => doc.classList.add('is-ready')));

  /* ---------- nav ---------- */
  const nav = document.querySelector('[data-nav]');
  const onNav = () => nav.classList.toggle('is-solid', scrollY > 30);
  addEventListener('scroll', onNav, { passive: true }); onNav();
  const toggle = document.querySelector('[data-menu-toggle]');
  const setMenu = open => {
    nav.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  };
  toggle.addEventListener('click', () => setMenu(!nav.classList.contains('is-open')));
  document.querySelectorAll('[data-menu] a').forEach(a => a.addEventListener('click', () => setMenu(false)));
  addEventListener('keydown', e => { if (e.key === 'Escape' && nav.classList.contains('is-open')) { setMenu(false); toggle.focus(); } });

  /* ---------- hero film: pick a source that suits the screen ---------- */
  const film = document.querySelector('.film');
  const video = document.querySelector('.film__video');
  if (video && !reduce.matches && !saveData) {
    const small = innerWidth < 760 || Math.max(screen.width, screen.height) * Math.min(devicePixelRatio || 1, 2) < 1600;
    const add = (src, type) => { const s = document.createElement('source'); s.src = src; s.type = type; video.appendChild(s); };
    if (small) add(video.dataset.srcSm, 'video/mp4');
    else { add(video.dataset.srcLgWebm, 'video/webm'); add(video.dataset.srcLg, 'video/mp4'); }
    // poster frame: reuse whichever format the <picture> already fetched
    const still = document.querySelector('.film__poster img');
    if (still) video.poster = still.currentSrc || still.src;
    video.load();
    video.addEventListener('playing', () => film.classList.add('is-playing'), { once: true });
    const p = video.play(); if (p && p.catch) p.catch(() => {});
    // pause off-screen to save battery
    new IntersectionObserver(([e]) => { e.isIntersecting ? video.play().catch(() => {}) : video.pause(); }).observe(film);
  } else if (video) {
    video.remove();
  }

  /* ---------- parallax: transform-only, rAF, only for layers on screen ---------- */
  const layers = [...document.querySelectorAll('[data-depth]')].map(el => ({ el, d: parseFloat(el.dataset.depth) || 0, on: false }));
  let ticking = false, vh = innerHeight, mobileScale = innerWidth < 760 ? 0.55 : 1;
  const io = new IntersectionObserver(es => es.forEach(e => {
    const l = layers.find(x => x.el === e.target); if (l) l.on = e.isIntersecting;
  }), { rootMargin: '20% 0px' });
  const paint = () => {
    ticking = false;
    for (const l of layers) {
      if (!l.on) continue;
      const host = l.el.closest('section').getBoundingClientRect();
      const off = Math.min(0, host.top);   // layers drift only once their section starts to scroll away
      l.el.style.transform = `translate3d(0, ${(off * l.d * -0.45 * mobileScale).toFixed(1)}px, 0)`;
    }
  };
  const req = () => { if (!ticking) { ticking = true; requestAnimationFrame(paint); } };
  const startParallax = () => {
    layers.forEach(l => io.observe(l.el));
    addEventListener('scroll', req, { passive: true });
    req();
  };
  const stopParallax = () => {
    removeEventListener('scroll', req);
    layers.forEach(l => { io.unobserve(l.el); l.el.style.transform = ''; });
  };
  addEventListener('resize', () => { vh = innerHeight; mobileScale = innerWidth < 760 ? 0.55 : 1; req(); }, { passive: true });
  if (!reduce.matches) startParallax();
  reduce.addEventListener && reduce.addEventListener('change', e => e.matches ? stopParallax() : startParallax());

  /* ---------- reveal ---------- */
  const rev = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('is-in'); rev.unobserve(e.target); }
  }), { rootMargin: '0px 0px -8% 0px' });
  document.querySelectorAll('.reveal').forEach(el => rev.observe(el));

  /* ---------- 05: the one-way ratchet ---------- */
  const rotor = document.querySelector('[data-rotor]');
  const pawl = document.querySelector('[data-pawl]');
  const count = document.querySelector('[data-count]');
  const hint = document.querySelector('.gates__hint');
  const studs = [...document.querySelectorAll('.stud')];
  const gates = [...document.querySelectorAll('[data-gate]')];
  const TEETH_PER_GATE = 3, PITCH = 360 / 24;
  let locked = 0;
  const lockTo = n => {
    if (n <= locked) { if (locked > 0 && n < locked) hint.classList.add('is-on'); return; }   // never backwards
    locked = n;
    rotor.style.transform = `rotate(${locked * TEETH_PER_GATE * PITCH}deg)`;
    pawl.classList.remove('is-lift'); void pawl.getBBox(); pawl.classList.add('is-lift');
    count.textContent = locked;
    gates.forEach((g, i) => {
      const on = i < locked; g.classList.toggle('is-locked', on);
      g.querySelector('[data-state]').textContent = on ? 'Passed' : 'Waiting';
    });
    studs.forEach((s, i) => s.classList.toggle('is-hot', i < locked * TEETH_PER_GATE));
    if (locked === gates.length) hint.classList.add('is-on');
  };
  const gio = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) lockTo(+e.target.dataset.gate);
    else if (e.boundingClientRect.top > 0) lockTo(+e.target.dataset.gate - 1);   // scrolled back above it
  }), { rootMargin: '-45% 0px -45% 0px' });
  gates.forEach(g => gio.observe(g));

  /* ---------- 02: equation diagram, plays once in view, Replay restarts it ---------- */
  const eq = document.querySelector('[data-eq]');
  if (eq) {
    new IntersectionObserver(([e], o) => { if (e.isIntersecting) { eq.classList.add('is-in'); o.disconnect(); } }, { threshold: .3 }).observe(eq);
    eq.querySelector('[data-eq-replay]').addEventListener('click', () => {
      eq.classList.remove('is-in'); void eq.offsetWidth; eq.classList.add('is-in');
    });
  }

  /* ---------- a stepper: play / pause / step / replay, tabs, autoplay only while on screen ---------- */
  const stepper = ({ root, count, ms, onRender, tablist, playBtn, playLabel, stepBtn, replayBtn, lastHold = 1 }) => {
    let i = 0, playing = !reduce.matches, inView = false, timer = 0;
    root.style.setProperty('--dur', ms + 'ms');
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const render = () => {
      tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === i)); t.classList.toggle('is-past', k < i); t.tabIndex = k === i ? 0 : -1; });
      tabs[i].classList.remove('is-run'); void tabs[i].offsetWidth; tabs[i].classList.add('is-run');   // restart the stage's progress bar
      root.style.setProperty('--dur', (i === count - 1 ? ms * lastHold : ms) + 'ms');
      root.classList.toggle('is-waiting', !inView);
      onRender(i, playing);
    };
    const schedule = () => {
      clearTimeout(timer);
      if (playing && inView) timer = setTimeout(() => { i = (i + 1) % count; render(); schedule(); }, i === count - 1 ? ms * lastHold : ms);
    };
    const setPlaying = on => {
      playing = on; root.classList.toggle('is-paused', !on);
      const word = on ? 'Pause' : 'Play'; playBtn.setAttribute('aria-label', word); playBtn.title = word; playLabel.textContent = word;
      render(); schedule();
    };
    playBtn.addEventListener('click', () => setPlaying(!playing));
    stepBtn.addEventListener('click', () => { i = (i + 1) % count; setPlaying(false); });
    replayBtn.addEventListener('click', () => { i = 0; setPlaying(true); });
    tabs.forEach((t, k) => t.addEventListener('click', () => { i = k; setPlaying(false); }));
    tablist.addEventListener('keydown', e => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (!d) return;
      e.preventDefault(); i = (i + d + count) % count; setPlaying(false); tabs[i].focus();
    });
    new IntersectionObserver(([e]) => { inView = e.isIntersecting; root.classList.toggle('is-waiting', !inView); schedule(); }, { threshold: .25 }).observe(root);
    setPlaying(playing);
  };

  /* ---------- 03: a whole run ---------- */
  const run = document.querySelector('[data-player="run"]');
  if (run) {
    const scenes = [...run.querySelectorAll('[data-scene]')];
    const tablist = run.querySelector('.player__tabs');
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const status = run.querySelector('[data-status]');
    tabs.forEach((t, k) => { t.id = `run-tab-${k}`; scenes[k].id = `run-scene-${k}`; t.setAttribute('aria-controls', scenes[k].id); scenes[k].setAttribute('aria-labelledby', t.id); });
    stepper({
      root: run, count: scenes.length, ms: +run.dataset.ms || 5200, tablist, lastHold: 1.4,
      playBtn: run.querySelector('[data-play]'), playLabel: run.querySelector('[data-play-label]'),
      stepBtn: run.querySelector('[data-step]'), replayBtn: run.querySelector('[data-replay]'),
      onRender: (i, playing) => {
        scenes.forEach((s, k) => { s.hidden = k !== i; });
        status.textContent = `${playing ? 'Playing' : 'Paused'} · stage ${i + 1} of ${scenes.length}`;
      }
    });
  }

  /* ---------- 04: one checkpoint, stage by stage ---------- */
  const sim = document.querySelector('[data-sim]');
  if (sim) {
    const STAGE_MS = 2800;
    const NAMES = ['Plan tests', 'Write tests', 'Red', 'Build', 'Green', 'Looks right', 'Review', 'Lock'];
    const TEXT = [
      'The test planner writes the test cases first, before any code. They cover the normal path, edge cases, who is allowed to do what, and errors.',
      'The test writer turns each case into a runnable test, tagged with its case ID. There is no feature code yet.',
      'Ratchet runs the tests. At least one has to fail its check. A test that crashes or is skipped does not count. From here on, the planned tests are frozen.',
      'The implementer writes code for this checkpoint only, until the tests pass. It cannot edit the tests.',
      'All planned tests must pass. Ratchet reads each result and checks that no test was deleted.',
      'Two reviewers compare screenshots of the app and the prototype, in the same state. One checks the look, the other checks the behavior. Any fixable difference blocks.',
      'Reviewers try to break the code. The fixer answers each finding, then the next round starts. Rounds repeat until no blocking findings remain.',
      'The checkpoint becomes one git commit, and the next checkpoint starts. You approve the whole feature at the end.'
    ];
    const boxes = [...sim.querySelectorAll('[data-stage]')];
    const text = sim.querySelector('[data-sim-text]');
    const status = sim.querySelector('[data-sim-status]');
    const cp3 = sim.querySelector('[data-sim-cp="3"]'), cp4 = sim.querySelector('[data-sim-cp="4"]');
    sim.style.setProperty('--stage-ms', STAGE_MS + 'ms');
    stepper({
      root: sim, count: boxes.length, ms: STAGE_MS, tablist: sim.querySelector('.sim__tabs'), lastHold: 1.6,
      playBtn: sim.querySelector('[data-sim-play]'), playLabel: sim.querySelector('[data-sim-play-label]'),
      stepBtn: sim.querySelector('[data-sim-step]'), replayBtn: sim.querySelector('[data-sim-replay]'),
      onRender: (i, playing) => {
        boxes.forEach((b, k) => { b.classList.toggle('is-done', k < i); b.classList.toggle('is-now', k === i); });
        text.textContent = TEXT[i];
        const done = i === boxes.length - 1;
        cp3.classList.toggle('is-done', done); cp3.classList.toggle('is-now', !done);
        cp3.querySelector('[data-sim-cp-state]').textContent = done ? 'Locked' : 'Current';
        cp4.querySelector('[data-sim-cp-state]').textContent = done ? 'Starting' : 'Up next';
        status.textContent = `${playing ? 'Playing' : 'Paused'} · ${NAMES[i]}`;
        // restart the progress bar on the current box
        const now = boxes[i].querySelector('.flow__s'); now.style.animation = 'none'; requestAnimationFrame(() => { now.style.animation = ''; });
      }
    });
  }

  /* ---------- 08/09: card grids that open a detail panel ---------- */
  document.querySelectorAll('[data-tabs]').forEach(group => {
    const cards = [...group.querySelectorAll('[data-tab-card]')];
    const select = (c, focus) => {
      cards.forEach(x => {
        const on = x === c; x.setAttribute('aria-selected', String(on)); x.tabIndex = on ? 0 : -1;
        document.getElementById(x.getAttribute('aria-controls')).hidden = !on;
      });
      if (focus) c.focus();
    };
    cards.forEach(c => c.addEventListener('click', () => {
      select(c);
      // on small screens the panel sits below the whole grid, so bring it into view
      const panel = document.getElementById(c.getAttribute('aria-controls'));
      if (innerWidth < 760 && panel.getBoundingClientRect().top > innerHeight * .6) panel.scrollIntoView({ behavior: reduce.matches ? 'auto' : 'smooth', block: 'start' });
    }));
    group.querySelector('[role="tablist"]').addEventListener('keydown', e => {
      const k = cards.indexOf(document.activeElement); if (k < 0) return;
      const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (d) { e.preventDefault(); select(cards[(k + d + cards.length) % cards.length], true); }
      else if (e.key === 'Home') { e.preventDefault(); select(cards[0], true); }
      else if (e.key === 'End') { e.preventDefault(); select(cards[cards.length - 1], true); }
    });
  });

  /* ---------- the viewer screenshot: Checkpoints / Activity ---------- */
  document.querySelectorAll('[data-shot]').forEach(shot => {
    const tabs = [...shot.querySelectorAll('[data-shot-tab]')];
    const pick = (t, focus) => {
      tabs.forEach(x => {
        const on = x === t; x.setAttribute('aria-selected', String(on)); x.tabIndex = on ? 0 : -1;
        document.getElementById(x.getAttribute('aria-controls')).hidden = !on;
      });
      shot.querySelector('.shot__frame').scrollTop = 0;
      if (focus) t.focus();
    };
    tabs.forEach(t => t.addEventListener('click', () => pick(t)));
    shot.querySelector('.shot__tabs').addEventListener('keydown', e => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (!d) return;
      e.preventDefault(); pick(tabs[(tabs.indexOf(document.activeElement) + d + tabs.length) % tabs.length], true);
    });
  });

  /* ---------- 10: does this count as a yes? (same rule Ratchet's prompt hook applies) ---------- */
  const yes = document.querySelector('[data-yes]');
  if (yes) {
    const input = yes.querySelector('[data-yes-input]');
    const out = yes.querySelector('[data-yes-out]');
    const ACCEPT = ['yes', 'yes, go ahead', 'go ahead', 'approved'];
    const judge = () => {
      const raw = input.value.trim();
      const core = raw.replace(/[.!]$/, '').toLowerCase();
      out.classList.remove('is-yes', 'is-no');
      if (!raw) { out.textContent = 'Type a reply to see how Ratchet reads it.'; return; }
      if (ACCEPT.includes(core)) { out.classList.add('is-yes'); out.textContent = `✓ Counts as a yes. Ratchet moves on.`; return; }
      out.classList.add('is-no');
      const starts = ACCEPT.find(a => core.startsWith(a));
      out.textContent = starts
        ? '✕ Not a yes. There is more after it, so nothing is approved. The agent replies and asks again.'
        : '✕ Not a yes. Nothing is approved. The agent replies or makes your change, then asks again.';
    };
    input.addEventListener('input', judge);
    yes.querySelectorAll('[data-yes-try]').forEach(b => b.addEventListener('click', () => { input.value = b.dataset.yesTry; judge(); input.focus(); }));
    judge();
  }

  /* ---------- hero demo: the run plays line by line, step 3's review catches a bug ---------- */
  const demo = document.querySelector('[data-demo]');
  if (demo && !reduce.matches) {
    const rows = [...demo.querySelectorAll('.demo__body > li')];
    const catchRow = demo.querySelector('.d-catch');
    let k = 0, t = 0, inView = false;
    demo.classList.add('is-playing');
    const reset = () => { rows.forEach(r => r.classList.remove('is-on')); catchRow.classList.remove('is-bug'); k = 0; };
    const next = () => {
      clearTimeout(t); if (!inView) return;
      if (k >= rows.length) { t = setTimeout(() => { reset(); next(); }, 5000); return; }
      const row = rows[k++]; row.classList.add('is-on');
      if (row === catchRow) {
        row.classList.add('is-bug');
        t = setTimeout(() => { row.classList.remove('is-bug'); t = setTimeout(next, 900); }, 1800);
        return;
      }
      t = setTimeout(next, row.classList.contains('d-step') ? 850 : 1000);
    };
    new IntersectionObserver(([e]) => { inView = e.isIntersecting; if (inView) next(); else clearTimeout(t); }, { threshold: .2 }).observe(demo);
  }

  /* ---------- the loop diagram: a token walks the ring, the ratchet turns one tooth per commit ---------- */
  const loop = document.querySelector('[data-loop]');
  if (loop) {
    const C = 380, R = 255, CIRC = 2 * Math.PI * R;
    const STAGES = [
      ['Test planner', 'Plan tests', 'Writes the test cases before any code exists: the normal path, edge cases, permissions and errors.'],
      ['Test writer', 'Write tests', 'Turns each case into a real test. There is still no feature code.'],
      ['Gate 1 · test runner', 'Tests fail', 'Runs the new tests. At least one has to fail, which proves the tests can catch a missing feature. From now on, the tests are frozen.'],
      ['Implementer', 'Build', 'Writes the code for this step only. It isn\'t allowed to edit the tests.'],
      ['Gate 1 · test runner', 'Tests pass', 'Every planned test has to pass. Ratchet reads each result itself and checks that no test was deleted.'],
      ['Gate 2 · screen reviewers', 'Looks right', 'Screenshots of the app are compared with the prototype, in the same state. Any difference that can be fixed sends the step back.'],
      ['Gate 3 · a different AI', 'Review', 'A reviewer from another company tries to break the code. Each finding has to say which users it harms.'],
      ['Ratchet', 'Lock', 'The step becomes one git commit, and the ratchet turns one tooth.']
    ];
    const FAIL = ['Gate 3 · a different AI', 'Review, round 1', 'The reviewer finds a bug: a rejection with an empty reason goes through. The step can\'t move on.'];
    const FIX = ['Fixer', 'Fix', 'Makes the reason required and adds a test that proves it. Then the review runs again.'];
    const PASS2 = ['Gate 3 · a different AI', 'Review, round 2', 'Nothing serious left. The step can move on.'];
    const FINAL = ['Gate 4 · you', 'Your OK', 'All four steps are locked. Now you try the feature and approve it. Only a clear yes counts.'];
    const HASH = ['3f9a1c2', '81be04d', 'c7d8e9f', '0a1b2c3'];
    const seq = [];
    for (let cp = 0; cp < 4; cp++) for (let n = 0; n < 8; n++) {
      if (cp === 2 && n === 6) seq.push({ cp, n, kind: 'fail' }, { cp, n: 'fix' }, { cp, n: 6, kind: 'pass2' });
      else seq.push({ cp, n });
    }
    seq.push({ cp: 3, n: 7, kind: 'final' });

    const $ = sel => loop.querySelector(sel);
    const nodes = [...loop.querySelectorAll('[data-node]')];
    const ring = nodes.filter(g => g.dataset.node !== 'fix'), fixNode = $('[data-node="fix"]');
    const token = $('[data-loop-token]'), prog = $('[data-loop-prog]'), wheel = $('[data-loop-wheel]');
    const go = $('#loop-go'), back = $('#loop-back');
    const cpText = $('[data-loop-cp]'), cpSub = $('[data-loop-cpsub]');
    const who = $('[data-loop-who]'), title = $('[data-loop-title]'), text = $('[data-loop-text]');
    const log = [...loop.querySelectorAll('[data-loop-log] li')];
    const playBtn = $('[data-loop-play]');
    let i = 0, angle = -90, onFix = false, turns = 0, locked = 0, playing = !reduce.matches, inView = false, timer = 0, busy = false;

    const put = (x, y) => token.setAttribute('transform', `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
    const atAngle = a => { const r = a * Math.PI / 180; put(C + R * Math.cos(r), C + R * Math.sin(r)); prog.style.strokeDashoffset = CIRC * (1 - Math.min(1, (a + 90) / 360)); };
    const ease = t => t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    const tween = (ms, f, done) => {
      if (reduce.matches) { f(1); return done(); }
      const t0 = performance.now();
      const tick = now => { const t = Math.min(1, (now - t0) / ms); f(ease(t)); t < 1 ? requestAnimationFrame(tick) : done(); };
      requestAnimationFrame(tick);
    };
    const moveTo = (s, done) => {
      if (s.n === 'fix') { onFix = true; const L = go.getTotalLength(); return tween(650, t => { const p = go.getPointAtLength(L * t); put(p.x, p.y); }, done); }
      if (onFix) { onFix = false; const L = back.getTotalLength(); return tween(650, t => { const p = back.getPointAtLength(L * t); put(p.x, p.y); }, () => { angle = 180; atAngle(angle); done(); }); }
      let a1 = -90 + 45 * s.n; while (a1 < angle - .01) a1 += 360;
      if (a1 === angle) { atAngle(angle); return done(); }
      const a0 = angle;
      tween(Math.min(900, 240 + (a1 - a0) * 9), t => atAngle(a0 + (a1 - a0) * t), () => { angle = a1 >= 270 ? a1 - 360 : a1; atAngle(angle); done(); });
    };
    const card = c => { who.textContent = c[0]; title.textContent = c[1]; text.textContent = c[2]; };
    const render = s => {
      const kind = s.kind || (s.n === 'fix' ? 'fix' : '');
      loop.classList.toggle('is-fixing', kind === 'fail' || kind === 'fix');
      loop.classList.toggle('is-final', kind === 'final');
      ring.forEach((g, k) => {
        g.classList.toggle('is-done', kind === 'final' || (typeof s.n === 'number' ? k < s.n : k < 6));
        g.classList.toggle('is-now', kind !== 'final' && k === s.n && kind !== 'fail');
        g.classList.toggle('is-fail', (kind === 'fail' || kind === 'fix') && k === 6);
        g.classList.remove('is-picked');
      });
      fixNode.classList.toggle('is-now', kind === 'fix');
      fixNode.classList.toggle('is-done', s.cp === 2 && (kind === 'pass2' || (typeof s.n === 'number' && s.n === 7)));
      card(kind === 'final' ? FINAL : kind === 'fail' ? FAIL : kind === 'fix' ? FIX : kind === 'pass2' ? PASS2 : STAGES[s.n]);
      if (s.n === 7 && kind !== 'final' && locked === s.cp) {
        locked = s.cp + 1; turns++;
        wheel.style.transform = `rotate(${turns * 15}deg)`;
      }
      log.forEach((li, k) => {
        li.classList.toggle('is-locked', k < locked); li.classList.toggle('is-now', k === s.cp && k >= locked);
        li.querySelector('em').innerHTML = k < locked ? `Locked<span class="loop__hash"> · ${HASH[k]}</span>` : k === s.cp ? 'Building' : 'Waiting';
      });
      cpText.textContent = kind === 'final' ? 'Done' : `CP${s.cp + 1}`;
      cpSub.textContent = `${locked} of 4 locked`;
      if (kind === 'final') prog.style.strokeDashoffset = 0;
    };
    const reset = () => { locked = 0; angle = -90; onFix = false; atAngle(angle); fixNode.classList.remove('is-done'); };
    const dwell = s => s.kind === 'final' ? 4200 : s.kind === 'fail' ? 2000 : s.n === 7 ? 1500 : 1100;
    const schedule = () => { clearTimeout(timer); if (playing && inView && !busy) timer = setTimeout(advance, dwell(seq[i])); };
    const advance = () => {
      i = (i + 1) % seq.length;
      if (i === 0) reset();
      busy = true;
      moveTo(seq[i], () => { busy = false; render(seq[i]); schedule(); });
    };
    const setPlaying = on => {
      playing = on; loop.classList.toggle('is-paused', !on);
      const w = on ? 'Pause' : 'Play'; playBtn.setAttribute('aria-label', w); playBtn.title = w;
      schedule();
    };
    playBtn.addEventListener('click', () => { if (!playing && loop.classList.contains('is-picking')) { loop.classList.remove('is-picking'); render(seq[i]); } setPlaying(!playing); });
    $('[data-loop-step]').addEventListener('click', () => { setPlaying(false); loop.classList.remove('is-picking'); if (!busy) advance(); });
    $('[data-loop-replay]').addEventListener('click', () => { loop.classList.remove('is-picking'); clearTimeout(timer); i = 0; reset(); render(seq[0]); setPlaying(true); });
    nodes.forEach(g => {
      const pick = () => {
        setPlaying(false); loop.classList.add('is-picking');
        nodes.forEach(x => x.classList.toggle('is-picked', x === g));
        card(g.dataset.node === 'fix' ? FIX : STAGES[+g.dataset.node]);
      };
      g.addEventListener('click', pick);
      g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
    });
    new IntersectionObserver(([e]) => { inView = e.isIntersecting; schedule(); }, { threshold: .3 }).observe(loop);
    atAngle(angle); render(seq[0]); setPlaying(playing);
  }

  /* ---------- the same step built twice: plays once in view, Replay restarts it ---------- */
  document.querySelectorAll('[data-case]').forEach(c => {
    const play = () => { c.classList.remove('is-play'); void c.offsetWidth; c.classList.add('is-play'); };
    new IntersectionObserver(([e], o) => { if (e.isIntersecting) { play(); o.disconnect(); } }, { threshold: .35 }).observe(c);
    c.querySelector('[data-case-replay]').addEventListener('click', play);
  });

  /* ---------- cards: a soft light that follows the pointer ---------- */
  if (matchMedia('(hover: hover) and (pointer: fine)').matches) {
    document.querySelectorAll('.feat, .why__card, .rule, .fact, .tcard, .pnext__link').forEach(c => {
      c.addEventListener('pointermove', e => {
        const r = c.getBoundingClientRect();
        c.style.setProperty('--mx', `${e.clientX - r.left}px`); c.style.setProperty('--my', `${e.clientY - r.top}px`);
      }, { passive: true });
    });
  }

  /* ---------- copy buttons ---------- */
  const toast = document.querySelector('[data-toast]');
  let tt;
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-copy]'); if (!b) return;
    const text = b.dataset.copy;
    try { await navigator.clipboard.writeText(text); }
    catch { const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); }
    const label = b.querySelector('[data-copy-label]'); const was = label.textContent;
    b.classList.add('is-copied'); label.textContent = 'Copied';
    toast.textContent = 'Copied. Paste it into your agent.'; toast.classList.add('is-on');
    clearTimeout(tt); tt = setTimeout(() => { b.classList.remove('is-copied'); label.textContent = was; toast.classList.remove('is-on'); }, 1800);
  });
})();
