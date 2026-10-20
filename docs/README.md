# Ratchet Loop website

The GitHub Pages site for Ratchet Loop. It is plain static HTML, CSS and JS with no build step at serve time.

## Publish

In the repository settings, open **Pages**, set **Source** to *Deploy from a branch*, then pick the branch and the `/docs` folder. The site appears at `https://rahilp.github.io/Ratchet-Loop/`.

## Edit

- Copy and layout: `src/index.template.html`, then run `python3 docs/src/build.py` to regenerate `index.html`. The build script also draws the ratchet wheel, pawl, logo, favicon and gear art, writes the FAQ and its structured data, and builds the agents grid from `agents/*.md` (so each panel quotes the real agent definition). Re-run it after changing an agent's frontmatter.
- Styles: `styles.css`. Colours and fonts are tokens at the top.
- Behaviour: `main.js` (parallax, the one-way scroll ratchet, the equation replay, the checkpoint simulator, agent tabs, mobile menu, video source choice, copy buttons).

## Files

| File | What it is |
|---|---|
| `assets/hero-1080.webm`, `assets/hero-1080.mp4` | Hero loop, 1920×1080, 8 s, seamless |
| `assets/hero-720.mp4` | Lighter hero loop for phones |
| `assets/hero-poster.avif`, `assets/hero-poster.jpg` | First frame of the loop, shown before the video plays and for reduced motion |
| `assets/macro.*` | Close-up still used behind "Why the gates hold" |
| `assets/og.jpg` | 1200×630 social preview |
| `src/hero/` | Source for the hero video, the close-up still and the social card |
| `assets/blueprint.svg`, `assets/gear-outline.svg` | Line art for the parallax layers |
| `assets/logo-mark.svg`, `assets/favicon.svg`, `assets/*.png` | Logo and icons |
