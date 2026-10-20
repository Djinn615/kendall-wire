# The Kendall Wire (website)

Static website for The Kendall Wire, the AI-assisted local news page for
Kendall County, Texas. Source articles live in `articles/`; the generated
site lives in `docs/` (served by GitHub Pages).

Live: https://djinn615.github.io/kendall-wire

## How it works

1. Articles are markdown files with frontmatter, stored under
   `articles/YYYY-MM-DD/<slug>.md`.
2. `python3 build_site.py` reads them and renders the full site into `docs/`.
3. Commit and push `docs/`; GitHub Pages serves it automatically.

Stdlib only. No build dependencies.

## Adding an article

Create `articles/<date>/<slug>.md` with frontmatter:

```yaml
---
title: "Headline here"
date: 2026-10-20
slug: my-story
status: published
desk: Courts Desk
summary: One-line summary for the index page.
image: assets/my-story-graphic.jpg
sources:
  - name: Boerne Star
    url: https://www.boernestar.com/article/...
facebook: https://facebook.com/.../posts/...   # optional
---
```

Full article text in markdown below the frontmatter.

**Editorial gate:** only articles with `status: published` are rendered.
Pipeline drafts carry `status: draft` (or no status) and are skipped. Marking
an article `published` is the human approval for the website. Nothing here
publishes to Facebook; that flow is separate.

**Desks** (editorial sections, not people): Courts Desk, Schools Desk,
Weather Desk, Development Desk, Elections Desk, Community Desk.

**Graphics:** put the story graphic in the workspace assets dir and reference
it as `assets/<filename>`. The builder copies referenced images into
`docs/assets/`. Never use em dashes anywhere.

## Local build

```bash
python3 build_site.py                       # builds ./docs from ./articles
python3 build_site.py --articles /path/to/articles --out /path/to/docs --assets /path/to/assets
```

## Migrating to Cloudflare Pages (future)

GitHub Pages is fine for now, but Cloudflare Pages is the better long-term
home: unlimited bandwidth, faster global CDN, still free.

Steps:

1. Create a Cloudflare account (free), go to Workers & Pages, "Create",
   "Pages", "Connect to Git", and select this repository.
2. Framework preset: None. Build command: `python3 build_site.py`.
   Build output directory: `docs`.
3. Deploy. The site gets a free `*.pages.dev` subdomain instantly.
4. (Optional) Add a custom domain in the Pages project settings. Register
   `kendallwire.com` via Cloudflare Registrar at wholesale cost
   (~$10.44/year, no markup, free WHOIS privacy) and attach it in one click.

Nothing else changes: same repo, same build script, same `docs/` output.
