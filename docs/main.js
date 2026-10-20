(() => {
  'use strict';
  const doc = document.documentElement;
  doc.classList.add('js');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const saveData = navigator.connection && navigator.connection.saveData;

  // reveal the hero once the display face is in, so the expanded headline never reflows in view
  const fontsIn = new Promise(res => {
    const link = document.querySelector('link[data-fonts]');
    const go = () => document.fonts.load('800 64px Archivo').then(res, res);
    if (!link || !document.fonts) return res();
    if (link.rel === 'stylesheet' && link.sheet) go(); else link.addEventListener('load', () => setTimeout(go, 0), { once: true });
  });
  Promise.race([fontsIn, new Promise(r => setTimeout(r, 1500))])
    .then(() => requestAnimationFrame(() => doc.classList.add('is-ready')));

  /* ---------- nav ---------- */
  const nav = document.querySelector('[data-nav]');
  const onNav = () => nav.classList.toggle('is-solid', scrollY > 40);
  addEventListener('scroll', onNav, { passive: true }); onNav();

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
