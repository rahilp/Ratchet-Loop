# Ratchet Loop website

The GitHub Pages site for Ratchet Loop. It is plain static HTML, CSS and JS with no build step at serve time.

## Publish

The site is meant to live at `https://ratchetloop.github.io/`. That address needs a GitHub organization (or user) named `ratchetloop` with a public repository named `ratchetloop.github.io`. Copy the contents of this `docs/` folder to the root of that repository's `main` branch, then in its **Settings → Pages** choose *Deploy from a branch*, `main`, `/ (root)`.

All asset paths are relative, so the same files also work from this repository's `/docs` folder at `https://rahilp.github.io/Ratchet-Loop/` (the canonical URL and sitemap still point at ratchetloop.github.io).

## Edit

- Copy and layout: the page parts in `src/pages/` (`home.html`, `_hero.html` and one `_<section>.html` per section). The feature list, FAQ, agents and setup files are data in `src/build.py`. Run `python3 docs/src/build.py` to regenerate the home page, the eight feature pages, `install.html`, the sitemap and `styles.min.css`. The build also draws the ratchet wheel, logo and favicon, and quotes each agent's real definition from `agents/*.md`, so re-run it after changing an agent's frontmatter.
- Styles: `styles.css`. Colours, fonts and the type scale (`--fs-*`) are tokens at the top. Headings and body use Geist; Geist Mono is only for code, file names and IDs. Cards use the shadow tokens (`--e1` to `--e3`) instead of borders.
- Behaviour: `main.js` (parallax, the one-way scroll ratchet, the equation replay, the run walkthrough, the checkpoint simulator, the card grids, the yes checker, mobile menu, video source choice, copy buttons).

## Files

| File | What it is |
|---|---|
| `assets/hero-1080.webm`, `assets/hero-1080.mp4` | Hero loop: a blueprint ratchet, 1920×1080, 12 s, seamless |
| `assets/hero-720.mp4` | Lighter hero loop for phones |
| `assets/hero-poster.avif`, `assets/hero-poster.jpg` | First frame of the loop, shown before the video plays and for reduced motion |
| `assets/viewer-plan.*`, `assets/viewer-act.*` | Screenshots of the real run viewer (`skills/ratchet-orchestrator/assets/viewer.html`) with the leave approvals example data |
| `assets/og.jpg` | 1200×630 social preview |
| `src/hero/` | Source for the hero video and the social card |
| `assets/blueprint.svg` | Line art for the hero's parallax layer |
| `assets/logo-mark.svg`, `assets/favicon.svg`, `assets/*.png` | Logo and icons |
