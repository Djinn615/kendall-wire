#!/usr/bin/env python3
"""
build_site.py: static site generator for The Kendall Wire website.

Reads article markdown files with frontmatter from articles/ and renders
a complete static site into docs/ (GitHub Pages serves docs/ on the main
branch).

Usage:
    python3 build_site.py [--articles DIR] [--out DIR]

Frontmatter fields: title, date, slug, desk, summary, image, sources (list
of {name, url}), optional facebook (post URL).

Stdlib only. No dependencies.
"""

import argparse
import html
import os
import re
import shutil
import sys

SITE_NAME = "The Kendall Wire"
SITE_TAGLINE = "Kendall County, reported straight."
SITE_URL = "https://djinn615.github.io/kendall-wire"

# ---------------------------------------------------------------------------
# Minimal frontmatter parser (subset of YAML: scalars and lists of mappings)
# ---------------------------------------------------------------------------

def parse_frontmatter(text):
    """Split '---' frontmatter from body. Returns (dict, body)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 4:].lstrip("\n")
    meta = {}
    current_list = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") or stripped == "-":
            item = stripped[2:].strip()
            if current_list is None:
                continue
            dm = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", item)
            if dm and item:
                current_list.append({dm.group(1): dm.group(2).strip().strip('"').strip("'")})
            else:
                current_list.append(item)
            continue
        if line[0] in " \t":
            m2 = re.match(r"^\s+([A-Za-z0-9_]+):\s*(.*)$", line)
            if m2 and current_list and isinstance(current_list[-1], dict):
                current_list[-1][m2.group(1)] = m2.group(2).strip().strip('"').strip("'")
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                meta[key] = []
                current_list = meta[key]
            else:
                meta[key] = val.strip('"').strip("'")
                current_list = None
    return meta, body


# ---------------------------------------------------------------------------
# Minimal markdown to HTML (paragraphs, headings, bold, italic, links, lists,
# blockquotes). Articles use simple markdown only.
# ---------------------------------------------------------------------------

def inline_md(text):
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def md_to_html(md):
    blocks = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith("### "):
            blocks.append("<h3>%s</h3>" % inline_md(line[4:]))
        elif line.startswith("## "):
            blocks.append("<h2>%s</h2>" % inline_md(line[3:]))
        elif line.startswith("# "):
            blocks.append("<h2>%s</h2>" % inline_md(line[2:]))
        elif line.startswith("> "):
            quote = []
            while i < len(lines) and lines[i].startswith("> "):
                quote.append(lines[i][2:])
                i += 1
            blocks.append("<blockquote><p>%s</p></blockquote>" % "</p><p>".join(inline_md(q) for q in quote))
            continue
        elif re.match(r"^[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                items.append(inline_md(re.sub(r"^[-*]\s+", "", lines[i])))
                i += 1
            blocks.append("<ul>\n%s\n</ul>" % "\n".join("<li>%s</li>" % x for x in items))
            continue
        elif re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                items.append(inline_md(re.sub(r"^\d+\.\s+", "", lines[i])))
                i += 1
            blocks.append("<ol>\n%s\n</ol>" % "\n".join("<li>%s</li>" % x for x in items))
            continue
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "> ", "- ", "* ")) and not re.match(r"^\d+\.\s+", lines[i]):
                para.append(lines[i].strip())
                i += 1
            blocks.append("<p>%s</p>" % inline_md(" ".join(para)))
            continue
        i += 1
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

CSS = """/* The Kendall Wire: classic newspaper card, mobile first */
:root{
  --paper:#F5F0E8; --paper-deep:#EDE6D6; --ink:#1A2A4A; --ink-soft:#3A4A6A;
  --rule:#1A2A4A; --accent:#8A6D3B;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:Georgia,'Times New Roman',serif;line-height:1.65;
  -webkit-text-size-adjust:100%}
.wrap{max-width:720px;margin:0 auto;padding:0 18px}
header.masthead{border-bottom:3px double var(--rule);padding:26px 0 16px;text-align:center}
.masthead .kicker{font-size:11px;letter-spacing:4px;text-transform:uppercase;color:var(--ink-soft)}
.masthead h1{font-size:2.2rem;margin:6px 0 4px;font-weight:700;letter-spacing:1px}
.masthead h1 a{color:var(--ink);text-decoration:none}
.masthead .tagline{font-style:italic;color:var(--ink-soft);font-size:.95rem}
nav.top{border-bottom:1px solid var(--rule);padding:8px 0;font-size:.85rem;text-align:center}
nav.top a{color:var(--ink);text-decoration:none;margin:0 12px;letter-spacing:1px;text-transform:uppercase;font-size:.78rem}
nav.top a:hover{text-decoration:underline}
.card{border:1px solid var(--rule);outline:3px double var(--rule);outline-offset:3px;
  background:#FBF8F1;margin:26px 0;padding:0;overflow:hidden}
.card img{width:100%;height:auto;display:block}
.card .pad{padding:18px 20px 20px}
.desk{display:inline-block;font-size:.72rem;letter-spacing:2px;text-transform:uppercase;
  color:var(--accent);border:1px solid var(--accent);padding:2px 10px;margin-bottom:10px}
.card h2{font-size:1.45rem;margin:0 0 6px;line-height:1.3}
.card h2 a{color:var(--ink);text-decoration:none}
.card h2 a:hover{text-decoration:underline}
.dateline{font-size:.82rem;color:var(--ink-soft);margin-bottom:10px;font-style:italic}
.card p.summary{margin:0;color:var(--ink-soft)}
.readmore{display:inline-block;margin-top:12px;font-size:.85rem;color:var(--ink);
  text-transform:uppercase;letter-spacing:1px;text-decoration:none;border-bottom:1px solid var(--ink)}
article.full .hero{margin:24px 0 0;border:1px solid var(--rule);outline:3px double var(--rule);outline-offset:3px}
article.full .hero img{width:100%;height:auto;display:block}
article.full h2.title{font-size:1.9rem;line-height:1.25;margin:22px 0 6px}
.byline{font-size:.85rem;color:var(--ink-soft);font-style:italic;margin-bottom:18px}
article.full p{margin:0 0 1.1em}
article.full blockquote{border-left:3px solid var(--accent);margin:1.2em 0;padding:.4em 0 .4em 1em;
  font-style:italic;color:var(--ink-soft)}
.sources{margin:30px 0;padding-top:14px;border-top:3px double var(--rule)}
.sources h3{font-size:.85rem;letter-spacing:2px;text-transform:uppercase;margin:0 0 10px}
.sources ul{margin:0;padding-left:20px;font-size:.9rem}
.sources a{color:var(--ink)}
.about h2{font-size:1.6rem;margin:26px 0 10px}
.about ul.desks{list-style:none;padding:0}
.about ul.desks li{border-bottom:1px dotted var(--ink-soft);padding:8px 0}
.about ul.desks strong{letter-spacing:1px}
footer{border-top:3px double var(--rule);margin-top:40px;padding:18px 0 34px;
  text-align:center;font-size:.8rem;color:var(--ink-soft)}
footer a{color:var(--ink)}
@media(min-width:640px){.masthead h1{font-size:2.8rem}.card h2{font-size:1.6rem}}
"""

BASE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="stylesheet" href="{css_path}">
</head>
<body>
<header class="masthead"><div class="wrap">
<div class="kicker">Kendall County &bull; Local News</div>
<h1><a href="{home}">The Kendall Wire</a></h1>
<div class="tagline">{tagline}</div>
</div></header>
<nav class="top"><div class="wrap">
<a href="{home}">Latest</a><a href="{about}">About</a><a href="{feed}">RSS</a>
</div></nav>
<div class="wrap">
{content}
</div>
<footer><div class="wrap">
The Kendall Wire &bull; AI-assisted local reporting, reviewed by a human before publishing.<br>
<a href="{about}">How this works</a>
</div></footer>
</body>
</html>
"""


def fmt_date(datestr):
    try:
        y, m, d = datestr.split("-")
        import calendar
        return "%s %d, %s" % (calendar.month_name[int(m)], int(d), y)
    except Exception:
        return datestr


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def load_articles(articles_dir):
    articles = []
    for root, _dirs, files in os.walk(articles_dir):
        for f in files:
            if not f.endswith(".md"):
                continue
            path = os.path.join(root, f)
            with open(path, encoding="utf-8") as fh:
                meta, body = parse_frontmatter(fh.read())
            if "title" not in meta or "date" not in meta:
                print("SKIP (missing title/date): %s" % path, file=sys.stderr)
                continue
            if meta.get("status") != "published":
                print("SKIP (not published): %s" % path, file=sys.stderr)
                continue
            meta["_path"] = path
            meta["_body"] = body
            meta.setdefault("slug", os.path.splitext(f)[0])
            if "desk" not in meta and "category" in meta:
                meta["desk"] = meta["category"].title() + " Desk"
            meta.setdefault("desk", "Community Desk")
            if "summary" not in meta and "summary-for-facebook" in meta:
                meta["summary"] = str(meta["summary-for-facebook"]).strip().split("\n")[0]
            if "summary" not in meta and "summary_for_facebook" in meta:
                meta["summary"] = str(meta["summary_for_facebook"]).strip().split("\n")[0]
            meta.setdefault("summary", "")
            meta.setdefault("sources", [])
            articles.append(meta)
    articles.sort(key=lambda a: (a["date"], a.get("slug", "")), reverse=True)
    return articles


def copy_assets(articles, base_assets_dir, out_assets_dir):
    os.makedirs(out_assets_dir, exist_ok=True)
    for a in articles:
        img = a.get("image", "")
        if not img:
            continue
        src = os.path.join(base_assets_dir, os.path.basename(img))
        dst = os.path.join(out_assets_dir, os.path.basename(img))
        if os.path.exists(src):
            shutil.copy2(src, dst)
            a["_image_url"] = "assets/" + os.path.basename(img)
        else:
            print("WARN: image not found: %s" % src, file=sys.stderr)
            a["_image_url"] = ""


def render_index(articles):
    cards = []
    for a in articles:
        url = "articles/%s/%s/" % (a["date"], a["slug"])
        img = ""
        if a.get("_image_url"):
            img = '<img src="%s" alt="%s">' % (a["_image_url"], html.escape(a["title"]))
        cards.append(
            '<div class="card">%s<div class="pad">'
            '<div class="desk">%s</div>'
            '<h2><a href="%s">%s</a></h2>'
            '<div class="dateline">%s</div>'
            '<p class="summary">%s</p>'
            '<a class="readmore" href="%s">Read the full story</a>'
            "</div></div>"
            % (img, html.escape(a["desk"]), url, html.escape(a["title"]),
               html.escape(fmt_date(a["date"])), html.escape(a["summary"]), url))
    return "\n".join(cards) if cards else "<p>No stories yet.</p>"


def render_article(a):
    img = ""
    if a.get("_image_url"):
        img = '<div class="hero"><img src="../../%s" alt="%s"></div>' % (a["_image_url"], html.escape(a["title"]))
    fb = ""
    if a.get("facebook"):
        fb = '<p><a href="%s">Discuss this story on Facebook</a></p>' % html.escape(a["facebook"])
    sources = ""
    srcs = a.get("sources") or []
    if srcs:
        lis = []
        for s in srcs:
            if isinstance(s, dict) and s.get("url"):
                lis.append('<li><a href="%s">%s</a></li>' % (html.escape(s["url"]), html.escape(s.get("name", s["url"]))))
            elif isinstance(s, str):
                lis.append("<li>%s</li>" % html.escape(s))
        sources = '<div class="sources"><h3>Sources</h3><ul>%s</ul></div>' % "".join(lis)
    return (
        '<article class="full">%s'
        '<div class="desk">%s</div>'
        '<h2 class="title">%s</h2>'
        '<div class="byline">Reported by The Kendall Wire &bull; %s</div>'
        "%s%s%s</article>"
        % (img, html.escape(a["desk"]), html.escape(a["title"]),
           html.escape(fmt_date(a["date"])), md_to_html(a["_body"]), fb, sources))


ABOUT_HTML = """<div class="about">
<h2>How The Kendall Wire works</h2>
<p>The Kendall Wire is an AI-assisted local news page for Kendall County, Texas.
AI systems monitor public sources, court agendas and videos, official announcements,
and local reporting, then draft each story. <strong>Every story is reviewed and
approved by a local human before it is published.</strong> Nothing goes live on
autopilot.</p>
<p>We report the news straight: no spin, no rumors, no outrage bait. When we get
something wrong, we correct it plainly.</p>
<h2>The desks</h2>
<ul class="desks">
<li><strong>Courts Desk</strong> &mdash; Commissioners court, agendas, votes, county government.</li>
<li><strong>Schools Desk</strong> &mdash; Boerne ISD board, bonds, closures, district news.</li>
<li><strong>Weather Desk</strong> &mdash; Alerts, burn bans, flood and storm coverage.</li>
<li><strong>Development Desk</strong> &mdash; Zoning, plats, subdivisions, road projects.</li>
<li><strong>Elections Desk</strong> &mdash; Ballots, turnout, results. Runs on its own track during election season.</li>
<li><strong>Community Desk</strong> &mdash; The life of the county: events, history, neighbors.</li>
</ul>
<p>These are editorial sections, not people. No bylines are invented; every story
carries the Wire's name and the responsibility that comes with it.</p>
<h2>Tips</h2>
<p>See something the Wire should cover? Send a tip through
<a href="https://www.facebook.com/61595332740273/">the Kendall Wire Facebook page</a>.
We verify everything against a real source before reporting it.</p>
</div>"""


def render_rss(articles):
    items = []
    for a in articles[:20]:
        url = "%s/articles/%s/%s/" % (SITE_URL, a["date"], a["slug"])
        items.append(
            "<item><title>%s</title><link>%s</link><guid>%s</guid>"
            "<pubDate>%s</pubDate><description>%s</description></item>"
            % (html.escape(a["title"]), url, url, html.escape(a["date"]),
               html.escape(a["summary"])))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0">'
            "<channel><title>The Kendall Wire</title>"
            "<link>%s</link><description>%s</description>%s</channel></rss>"
            % (SITE_URL, SITE_TAGLINE, "".join(items)))


def render_sitemap(articles):
    urls = [SITE_URL + "/", SITE_URL + "/about.html"]
    for a in articles:
        urls.append("%s/articles/%s/%s/" % (SITE_URL, a["date"], a["slug"]))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join("<url><loc>%s</loc></url>" % u for u in urls) + "</urlset>")


def build(articles_dir, out_dir, base_assets_dir):
    articles = load_articles(articles_dir)
    print("Loaded %d articles" % len(articles))

    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    copy_assets(articles, base_assets_dir, os.path.join(out_dir, "assets"))

    with open(os.path.join(out_dir, "style.css"), "w", encoding="utf-8") as fh:
        fh.write(CSS)

    def page(title, description, content, css_path="style.css", home="index.html"):
        return BASE_HTML.format(
            title=html.escape(title), description=html.escape(description),
            css_path=css_path, home=home, about="about.html", feed="feed.xml",
            tagline=SITE_TAGLINE, content=content)

    # Index
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(SITE_NAME, SITE_TAGLINE, render_index(articles)))

    # About
    with open(os.path.join(out_dir, "about.html"), "w", encoding="utf-8") as fh:
        fh.write(page("About - " + SITE_NAME, "How The Kendall Wire works.", ABOUT_HTML))

    # Articles
    for a in articles:
        d = os.path.join(out_dir, "articles", a["date"], a["slug"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
            art = BASE_HTML.format(
                title=html.escape(a["title"] + " - " + SITE_NAME),
                description=html.escape(a["summary"]),
                css_path="../../../style.css", home="../../../index.html",
                about="../../../about.html", feed="../../../feed.xml",
                tagline=SITE_TAGLINE, content=render_article(a))
            fh.write(art)

    # Feed, sitemap, nojekyll
    with open(os.path.join(out_dir, "feed.xml"), "w", encoding="utf-8") as fh:
        fh.write(render_rss(articles))
    with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write(render_sitemap(articles))
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    print("Wrote site to %s" % out_dir)
    return articles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "articles"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs"))
    ap.add_argument("--assets", default=os.path.join(os.path.expanduser("~"), "workspace", "hill-country-weird", "assets"))
    args = ap.parse_args()
    build(args.articles, args.out, args.assets)


if __name__ == "__main__":
    main()
