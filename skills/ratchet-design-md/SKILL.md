---
name: ratchet-design-md
description: Creates or updates DESIGN.md — a structured, machine-readable design-system file (tokens for color, type, spacing, radius, elevation, motion; component specs with states; layout and content rules) extracted from an existing codebase or built from a brief. Prototypes, implementers and UI reviewers all build and judge against it. Use when a project has no DESIGN.md, when the user asks for a design system, design tokens or a style guide file, or before prototyping UI in the Ratchet loop.
---

# DESIGN.md

DESIGN.md is the contract between the prototype, the real app, and the UI reviewers. It has to be precise enough that two different agents produce the same button. Vague adjectives ("clean, modern") are useless here, so use numbers.

## Mode 1: Extract from an existing app (preferred when there is UI already)

1. Find the real sources: the Tailwind config / theme file, CSS variables, the component library folder, global styles, and fonts. Read them. Don't guess from memory.
2. Sample 3–5 representative screens' components (page header, table, form, button, modal, empty state, toast) and record what they *actually* use, including inconsistencies.
3. Where the code is inconsistent, pick the majority usage as the token and list the deviations under **Known inconsistencies**, so reviewers don't flag the app for matching its own legacy.
4. If the app is running and screenshots are possible, capture one or two screens to sanity-check the values against what renders.

## Mode 2: Create from a brief

Ask for (or infer from the product) the audience, the tone, a brand color if one exists, and density (data-heavy vs airy). Then commit to specific values. Build the neutrals as a proper ramp, and check body text contrast against WCAG AA (4.5:1).

## Required structure

```markdown
# DESIGN.md — <Product>
> Source of truth for UI. Values are exact. When code and this file disagree, this file wins unless listed under Known inconsistencies.

## 1. Principles            (3–5 lines, each testable: "Primary action is always right-aligned in dialogs")
## 2. Tokens
### Color                  (table: token | light | dark | usage)  — include text, surface, border, accent, success/warn/danger + their soft backgrounds
### Typography             (table: role | font | size/line-height | weight | letter-spacing) — display, h1–h3, body, small, mono, label
### Spacing                (scale, e.g. 4/8/12/16/24/32/48; page gutters; section gaps)
### Radius / Border / Elevation (exact values + where each applies)
### Motion                 (durations, easing, what animates)
## 3. Layout               (breakpoints, max widths, grid, sidebar/topbar dimensions, page header anatomy)
## 4. Components           (for each: anatomy, sizes, variants, and EVERY state — default/hover/focus/active/disabled/loading/error/empty — with token references)
## 5. Patterns             (forms & validation messages, tables, empty states, confirmations, toasts, loading skeletons)
## 6. Content              (voice, capitalization, date/number/currency formats, button verb rules)
## 7. Accessibility        (focus ring spec, min hit area, contrast rules, motion-reduce)
## 8. Implementation map   (token → CSS var / Tailwind class / component path, so implementers reuse instead of reinventing)
## 9. Known inconsistencies
```

## Rules
- Every value must be concrete (`#1F2937`, `14px/20px 500`, `8px`). No "large-ish".
- Components list their states explicitly. Missing states are where UI reviews fail later.
- The implementation map is what stops implementers inventing one-off styles, so fill it in from real file paths.
- Keep it under ~400 lines. Put deep component docs in `design/*.md` and link them from here.
- Updating an existing file: preserve sections, change values surgically, and add a dated line at the bottom under `## Changelog`.

Return ≤8 lines: the path, the mode used, and the 3 decisions most worth a human glance.
