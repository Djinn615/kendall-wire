# The Kendall Wire

Static website for The Kendall Wire, an AI-assisted local news page for
Kendall County, Texas. Story markdown lives in `articles/`. The site that
GitHub Pages serves lives in `docs/`.

Live: https://djinn615.github.io/kendall-wire

Custom domain: https://kendallwire.com (`docs/CNAME`)

Python 3 standard library only. No package install.

## What is in this repo

`python3 build_site.py` reads published markdown and writes HTML, `style.css`,
`feed.xml`, and `sitemap.xml` into the output directory.

`docs/` is also the published site, and it is ahead of `articles/`:

- `articles/` has the markdown for a subset of stories (the early ones).
- `docs/articles/` has every story that is live, including later stories whose
  markdown was never committed.
- `docs/podcast/` is the For the Record podcast (episode page, RSS, cover, MP3).
  The generator does not build it.
- `docs/check-inbox.html` and `docs/subscribed.html` are the newsletter
  confirmation pages. The generator does not build those either.

A build updates pages in place. It does not delete the podcast, the newsletter
pages, images already in the output, or article HTML that has no markdown
source. Those published stories stay on disk and are included on the homepage,
in the RSS feed, and in the sitemap. Pass `--prune` only when you mean to
delete every article folder that is not in the published markdown set.

## Local build

```bash
python3 build_site.py
python3 -m unittest discover -s tests -v
```

Defaults:

- `--articles` is `./articles`
- `--out` is `./docs`
- `--assets` is the first directory that exists among `./assets`,
  `~/workspace/hill-country-weird/assets`, and `./docs/assets`

`WIRE_EDITION_DATE=YYYY-MM-DD` pins the masthead date. Without it, the masthead
uses today's date.

`WIRE_INLINE_IMAGES=1` embeds images as JPEG data URIs instead of copying files.
Needs Pillow. Leave it unset for a normal build.

The output directory must not be `/`, your home directory, or the `articles/`
tree. The build exits with an error rather than writing there.

Story images are copied by filename from the assets directory only. An
`image:` value of `/some/other/machine/card.jpg` is not opened. Put the file
in the assets directory and set `image: assets/card.jpg`.

## Adding an article

Create `articles/<date>/<slug>.md`:

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
facebook: https://facebook.com/.../posts/...
---
```

`slug` is lowercase letters, digits, and hyphens. `date` is `YYYY-MM-DD`.
Two published stories cannot share a date and slug.

Full article text in markdown below the frontmatter. The generator handles
paragraphs, headings, bold, italic, links, lists, and blockquotes.

**Editorial gate:** only `status: published` is rendered. `draft`, or no
status, is skipped. Marking a story `published` is the human approval for the
website. This repo does not post to Facebook.

**Desks** are sections, not people: Courts Desk, City Desk, Schools Desk,
Weather Desk, Development Desk, Elections Desk, Water Desk, Sports Desk,
Community Desk. Older stories also use names such as `Sports` or
`Wire Public Safety Desk`. The generator prints the desk it is given.

**Graphics:** `assets/<filename>` with a `.jpg`, `.jpeg`, `.png`, `.gif`, or
`.webp` file. The builder copies that file into `docs/assets/`.

**Links:** `http`, `https`, `mailto`, and relative URLs are kept. `javascript:`,
`data:`, `vbscript:`, and `file:` links are dropped.

Do not put local filesystem paths or production notes (`/home/...`,
"visually verified", "not approved for publication") in the article body.
The generator strips those lines so they cannot ship. House style: no em dashes.

## Checks

GitHub Actions (`.github/workflows/ci.yml`) compiles the generator and runs
the unittest suite on every push and pull request. The tests build into a
temporary directory. They do not modify `docs/`.

## Deploy

Commit and push `docs/` on `main`. GitHub Pages serves that directory.
Nothing in this repo publishes itself; a person commits the built files.

Cloudflare Pages, if you move later: framework preset None, build command
`python3 build_site.py`, output directory `docs`.

## Left for the owner

- Most live stories have HTML in `docs/articles/` and no markdown in
  `articles/`. The build keeps that HTML. Restoring the missing markdown is a
  content migration, not something the generator can invent safely.
- Three October 23 graphics (Bravo, burn ban, Salazar memorial highway) are
  still embedded in the existing article HTML. The image files are not in
  `docs/assets/`. Rebuilding on top of `docs/` keeps those heroes. A brand-new
  output directory will not have them until the files are added to assets.
- `docs/style.css` includes `.newsletter-box` rules. The page templates do not
  render a newsletter form. The confirmation pages are static.
- The November 3, 2026 election rail is not generated. The live homepage had
  already dropped it. Add a new rail in `build_site.py` when the next ballot
  needs one.
- The Cloudflare Web Analytics token in `build_site.py` is the public beacon
  token Cloudflare tells sites to embed. It is not an API secret. Rotate it
  in the Cloudflare dashboard if you want a new one.
