(() => {
  'use strict';
  const doc = document.documentElement;
  doc.classList.add('js');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const saveData = navigator.connection && navigator.connection.saveData;

  // reveal the hero once the display face is in, so the headline never reflows in view
  const fontsIn = new Promise(res => {
    const link = document.querySelector('link[data-fonts]');
    const go = () => Promise.all([document.fonts.load('600 64px Inter'), document.fonts.load('400 17px Inter')]).then(res, res);
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
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const render = () => {
      tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === i)); t.classList.toggle('is-past', k < i); t.tabIndex = k === i ? 0 : -1; });
      onRender(i, playing);
    };
    const schedule = () => {
      clearTimeout(timer);
      if (playing && inView) timer = setTimeout(() => { i = (i + 1) % count; render(); schedule(); }, i === count - 1 ? ms * lastHold : ms);
    };
    const setPlaying = on => {
      playing = on; root.classList.toggle('is-paused', !on);
      playBtn.setAttribute('aria-pressed', String(on)); playLabel.textContent = on ? 'Pause' : 'Play';
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
    new IntersectionObserver(([e]) => { inView = e.isIntersecting; schedule(); }, { threshold: .25 }).observe(root);
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
        status.textContent = `${playing ? 'Auto-play' : 'Paused'} · stage ${i + 1} of ${scenes.length} · ${tabs[i].textContent.replace(/^\d+/, '').trim()}`;
      }
    });
  }

  /* ---------- 04: one checkpoint, stage by stage ---------- */
  const sim = document.querySelector('[data-sim]');
  if (sim) {
    const STAGE_MS = 2800;
    const NAMES = ['Plan tests', 'Write tests', 'Red', 'Build', 'Green', 'Looks right', 'Review', 'Lock'];
    const TEXT = [
      'The test planner writes test cases before any code exists. They cover the normal path, edge cases, permissions and errors.',
      'The test writer turns each case into a runnable test, tagged with its case ID. There is no feature code yet.',
      'Ratchet runs the tests. At least one must fail on an assertion. A test that crashes or is skipped does not count. From here on, the planned tests are frozen.',
      'The implementer writes code for this checkpoint only, until the tests pass. It cannot edit the tests.',
      'All planned tests must pass. Ratchet reads each result and checks that no test was deleted.',
      'Two reviewers compare screenshots of the app and the prototype, in the same state. One checks the look, the other checks the behaviour. Any fixable difference blocks.',
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
        status.textContent = `${playing ? 'Auto-play' : 'Paused'} · checkpoint 3 of 4 · ${NAMES[i]}`;
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
