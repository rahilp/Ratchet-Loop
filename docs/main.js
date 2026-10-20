(() => {
  'use strict';
  const doc = document.documentElement;
  doc.classList.add('js');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const saveData = navigator.connection && navigator.connection.saveData;

  // reveal the hero once the display face is in, so the expanded headline never reflows in view
  const fontsIn = new Promise(res => {
    const link = document.querySelector('link[data-fonts]');
    const go = () => Promise.all([document.fonts.load('400 64px Newsreader'), document.fonts.load('italic 400 64px Newsreader'), document.fonts.load('800 17px Archivo')]).then(res, res);
    if (!link || !document.fonts) return res();
    if (link.rel === 'stylesheet' && link.sheet) go(); else link.addEventListener('load', () => setTimeout(go, 0), { once: true });
  });
  Promise.race([fontsIn, new Promise(r => setTimeout(r, 1500))])
    .then(() => requestAnimationFrame(() => doc.classList.add('is-ready')));

  /* ---------- nav ---------- */
  const nav = document.querySelector('[data-nav]');
  const onNav = () => nav.classList.toggle('is-solid', scrollY > 40);
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

  /* ---------- hero video: pick a source that suits the screen ---------- */
  const hero = document.querySelector('.hero');
  const video = document.querySelector('.hero__video');
  if (video && !reduce.matches && !saveData) {
    const small = Math.max(screen.width, screen.height) * Math.min(devicePixelRatio || 1, 2) < 1600 || innerWidth < 760;
    const add = (src, type) => { const s = document.createElement('source'); s.src = src; s.type = type; video.appendChild(s); };
    if (small) add(video.dataset.srcSm, 'video/mp4');
    else { add(video.dataset.srcLgWebm, 'video/webm'); add(video.dataset.srcLg, 'video/mp4'); }
    // poster frame: reuse whichever format the <picture> already fetched
    const still = document.querySelector('.hero__poster img');
    if (still) video.poster = still.currentSrc || still.src;
    video.load();
    video.addEventListener('playing', () => hero.classList.add('is-playing'), { once: true });
    const p = video.play(); if (p && p.catch) p.catch(() => {});
    // pause off-screen to save battery
    new IntersectionObserver(([e]) => { e.isIntersecting ? video.play().catch(() => {}) : video.pause(); }).observe(hero);
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
      const host = l.el.parentElement.getBoundingClientRect();
      const off = (host.top + host.height / 2 - vh / 2);   // 0 when the section is centred
      l.el.style.transform = `translate3d(0, ${(off * l.d * -0.35 * mobileScale).toFixed(1)}px, 0)`;
    }
  };
  const req = () => { if (!ticking) { ticking = true; requestAnimationFrame(paint); } };
  const startParallax = () => {
    layers.forEach(l => io.observe(l.el));
    addEventListener('scroll', req, { passive: true });
    addEventListener('resize', () => { vh = innerHeight; mobileScale = innerWidth < 760 ? 0.55 : 1; req(); }, { passive: true });
    req();
  };
  const stopParallax = () => {
    removeEventListener('scroll', req);
    layers.forEach(l => { io.unobserve(l.el); l.el.style.transform = ''; });
  };
  if (!reduce.matches) startParallax();
  reduce.addEventListener && reduce.addEventListener('change', e => e.matches ? stopParallax() : startParallax());

  /* ---------- reveal ---------- */
  const rev = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('is-in'); rev.unobserve(e.target); }
  }), { rootMargin: '0px 0px -10% 0px' });
  document.querySelectorAll('.reveal, .term').forEach(el => rev.observe(el));
  document.querySelectorAll('.term__body[data-type] > span').forEach((s, i) => s.style.setProperty('--i', i));

  /* ---------- the one-way ratchet ---------- */
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
      g.querySelector('[data-state]').textContent = on ? 'Locked' : 'Open';
    });
    studs.forEach((s, i) => s.classList.toggle('is-hot', i < locked * TEETH_PER_GATE));
    if (locked === gates.length) hint.classList.add('is-on');
  };
  const gio = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) lockTo(+e.target.dataset.gate);
    else if (e.boundingClientRect.top > 0) lockTo(+e.target.dataset.gate - 1);   // scrolled back above it
  }), { rootMargin: '-45% 0px -45% 0px' });
  gates.forEach(g => gio.observe(g));

  /* ---------- 01: equation diagram, plays once in view, Replay restarts it ---------- */
  const eq = document.querySelector('[data-eq]');
  if (eq) {
    new IntersectionObserver(([e], o) => { if (e.isIntersecting) { eq.classList.add('is-in'); o.disconnect(); } }, { threshold: .3 }).observe(eq);
    eq.querySelector('[data-eq-replay]').addEventListener('click', () => {
      eq.classList.remove('is-in'); void eq.offsetWidth; eq.classList.add('is-in');
    });
  }

  /* ---------- 02: one checkpoint, stage by stage ---------- */
  const sim = document.querySelector('[data-sim]');
  if (sim) {
    const STAGE_MS = 2600;
    const NAMES = ['Plan tests', 'Write tests', 'Red', 'Build', 'Green', 'Looks right', 'Review', 'Lock'];
    const TEXT = [
      'The test planner writes ID-tagged test cases before any code exists: the happy path, edge cases, permissions and failure modes.',
      'The test writer turns those cases into real, runnable tests, tagged with their IDs. There is still no production code.',
      'Ratchet runs the tests itself. At least one has to fail on an assertion, and a test that crashed or was skipped doesn\u2019t count. From here the planned tests are frozen.',
      'The implementer writes the code for exactly this checkpoint. It never edits tests.',
      'Every planned test has to pass. Ratchet reads each result one by one and checks that none were deleted.',
      'This step changes a screen, so two reviewers compare screenshots against the prototype in the same state. One checks the look, one the behaviour. Any fixable difference blocks.',
      'Reviewers, from another company when one is installed, try to break the code. The fixer answers every finding, and rounds repeat until nobody has a blocking one.',
      'The checkpoint becomes its own git commit. The ratchet clicks forward and the next checkpoint starts. You sign off on the whole feature at the end.'
    ];
    const boxes = [...sim.querySelectorAll('[data-stage]')];
    const tabs = [...sim.querySelectorAll('[data-sim-tab]')];
    const text = sim.querySelector('[data-sim-text]');
    const status = sim.querySelector('[data-sim-status]');
    const playBtn = sim.querySelector('[data-sim-play]');
    const playLabel = sim.querySelector('[data-sim-play-label]');
    const cp3 = sim.querySelector('[data-sim-cp="3"]'), cp4 = sim.querySelector('[data-sim-cp="4"]');
    let i = 0, playing = !reduce.matches, inView = false, timer = 0;
    sim.style.setProperty('--stage-ms', STAGE_MS + 'ms');

    const render = () => {
      boxes.forEach((b, k) => { b.classList.toggle('is-done', k < i); b.classList.toggle('is-now', k === i); });
      tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === i)); t.classList.toggle('is-past', k < i); t.tabIndex = k === i ? 0 : -1; });
      text.textContent = TEXT[i];
      const locked = i === boxes.length - 1;
      cp3.classList.toggle('is-done', locked); cp3.classList.toggle('is-now', !locked);
      cp3.querySelector('[data-sim-cp-state]').textContent = locked ? 'Locked' : 'Current';
      cp4.querySelector('[data-sim-cp-state]').textContent = locked ? 'Starting' : 'Up next';
      status.textContent = `${playing ? 'Auto-loop' : 'Paused'} \u00b7 checkpoint 3 / 4 \u00b7 ${NAMES[i]}`;
      // restart the progress bar on the current box
      const now = boxes[i].querySelector('.flow__s'); now.style.animation = 'none'; void now.offsetWidth; now.style.animation = '';
    };
    const schedule = () => {
      clearTimeout(timer);
      if (playing && inView) timer = setTimeout(() => { i = (i + 1) % boxes.length; render(); schedule(); }, i === boxes.length - 1 ? STAGE_MS * 1.6 : STAGE_MS);
    };
    const setPlaying = on => {
      playing = on; sim.classList.toggle('is-paused', !on);
      playBtn.setAttribute('aria-pressed', String(on)); playLabel.textContent = on ? 'Pause' : 'Play';
      render(); schedule();
    };
    playBtn.addEventListener('click', () => setPlaying(!playing));
    sim.querySelector('[data-sim-step]').addEventListener('click', () => { i = (i + 1) % boxes.length; setPlaying(false); });
    sim.querySelector('[data-sim-replay]').addEventListener('click', () => { i = 0; setPlaying(true); });
    tabs.forEach((t, k) => t.addEventListener('click', () => { i = k; setPlaying(false); }));
    sim.querySelector('.sim__tabs').addEventListener('keydown', e => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (!d) return;
      e.preventDefault(); i = (i + d + boxes.length) % boxes.length; setPlaying(false); tabs[i].focus();
    });
    new IntersectionObserver(([e]) => { inView = e.isIntersecting; schedule(); }, { threshold: .25 }).observe(sim);
    setPlaying(playing);
  }

  /* ---------- 04: agents, as tabs ---------- */
  const team = document.querySelector('[data-team]');
  if (team) {
    const cards = [...team.querySelectorAll('[data-team-tab]')];
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
    team.querySelector('.team__grid').addEventListener('keydown', e => {
      const k = cards.indexOf(document.activeElement); if (k < 0) return;
      const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (d) { e.preventDefault(); select(cards[(k + d + cards.length) % cards.length], true); }
      else if (e.key === 'Home') { e.preventDefault(); select(cards[0], true); }
      else if (e.key === 'End') { e.preventDefault(); select(cards[cards.length - 1], true); }
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
