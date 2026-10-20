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
import base64
import html
import io
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
/* ---- front page: newspaper layout, mobile first ---- */
.wrap.wide{max-width:1040px}
.dateline-top{font-size:.8rem;color:var(--ink-soft);letter-spacing:1px;margin-top:8px}
.fp-rule{border:0;border-top:3px double var(--rule);margin:0 0 4px}
.lead{border:1px solid var(--rule);outline:3px double var(--rule);outline-offset:3px;
  background:#FBF8F1;margin:22px 0;overflow:hidden}
.lead img{width:100%;height:auto;display:block}
.lead .pad{padding:18px 20px 22px}
.lead h2.lead-head{font-size:1.8rem;line-height:1.25;margin:0 0 8px}
.lead h2.lead-head a{color:var(--ink);text-decoration:none}
.lead h2.lead-head a:hover{text-decoration:underline}
.section-head{font-size:.9rem;letter-spacing:3px;text-transform:uppercase;
  border-bottom:3px double var(--rule);padding-bottom:8px;margin:30px 0 4px}
.sec-grid{display:grid;gap:0}
.sec-story{border-bottom:1px dotted var(--ink-soft);padding:14px 0}
.sec-story h3{font-size:1.15rem;margin:0 0 4px;line-height:1.35}
.sec-story h3 a{color:var(--ink);text-decoration:none}
.sec-story h3 a:hover{text-decoration:underline}
.sec-story .dateline{font-size:.78rem;color:var(--ink-soft);font-style:italic;margin-bottom:6px}
.sec-story p{font-size:.92rem;color:var(--ink-soft);margin:0}
.rail-box{border:1px solid var(--rule);outline:3px double var(--rule);outline-offset:3px;
  background:#FBF8F1;padding:16px 18px;margin:22px 0}
.rail-box h3{margin:0 0 8px;font-size:.9rem;letter-spacing:2px;text-transform:uppercase}
.rail-box p{font-size:.9rem;margin:0 0 .8em;color:var(--ink-soft)}
.rail-box p:last-child{margin-bottom:0}
.rail-box a{color:var(--ink)}
ul.desk-list{list-style:none;padding:0;margin:0}
ul.desk-list li{padding:7px 0;border-bottom:1px dotted var(--ink-soft);font-size:.88rem}
ul.desk-list li:last-child{border-bottom:0}
ul.desk-list a{color:var(--ink);text-decoration:none}
ul.desk-list a:hover{text-decoration:underline}
.rail-desk-label{font-size:.72rem;letter-spacing:2px;text-transform:uppercase;color:var(--accent);margin:14px 0 2px}
@media(min-width:900px){
  .fp{display:grid;grid-template-columns:1fr 300px;gap:34px;align-items:start}
  .sec-grid{grid-template-columns:1fr 1fr;gap:0 26px}
  .lead h2.lead-head{font-size:2.2rem}
  .masthead h1{font-size:3.2rem}
}
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
<div class="dateline-top">Boerne, Texas &bull; {edition_date}</div>
</div></header>
<nav class="top"><div class="wrap">
<a href="{home}">Latest</a><a href="{about}">About</a><a href="{feed}">RSS</a>
</div></nav>
<div class="{wrap_class}">
{content}
</div>
<footer><div class="wrap">
The Kendall Wire &bull; AI-assisted local reporting, reviewed by a human before publishing.<br>
<a href="{about}">How this works</a> &bull; <a href="{feed}">RSS</a>
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


def edition_date_str():
    """Today's date line for the masthead, e.g. 'Monday, October 19, 2026'."""
    import datetime
    return datetime.date.today().strftime("%A, %B %d, %Y").replace(" 0", " ")


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


def copy_assets(articles, base_assets_dir, out_assets_dir, inline=False):
    if not inline:
        os.makedirs(out_assets_dir, exist_ok=True)
    for a in articles:
        img = a.get("image", "")
        if not img:
            continue
        src = os.path.join(base_assets_dir, os.path.basename(img))
        if not os.path.exists(src):
            print("WARN: image not found: %s" % src, file=sys.stderr)
            a["_image_url"] = ""
            continue
        if inline:
            a["_image_data"] = image_data_uri(src)
            a["_image_url"] = ""
        else:
            dst = os.path.join(out_assets_dir, os.path.basename(img))
            shutil.copy2(src, dst)
            a["_image_url"] = "assets/" + os.path.basename(img)


def image_data_uri(path):
    """Embed an image as a base64 data URI, compressed for web display.
    Used when WIRE_INLINE_IMAGES=1 (e.g. when the host cannot store binary
    files via API). Falls back to raw bytes if PIL is unavailable."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if im.width > 1000:
            im = im.resize((1000, int(1000 * im.height / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70, optimize=True)
        raw = buf.getvalue()
    except ImportError:
        with open(path, "rb") as fh:
            raw = fh.read()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


ARTICLE_URL = "articles/%s/%s/"

ELECTION_BOX = """<div class="rail-box">
<h3>Election 2026</h3>
<p>Early voting runs October 19 through October 30 at the Courthouse Annex,
221 Fawn Valley Dr., Boerne. Election day is Tuesday, November 3.</p>
<p>On the ballot: Justice of the Peace Precinct 1 (Van Stavern vs Wolfe),
Comfort ISD Trustee District 2, Comal ISD bond measures, State Rep District 19,
and US Rep District 21.</p>
</div>"""

ABOUT_BOX = """<div class="rail-box">
<h3>About the Wire</h3>
<p>The Kendall Wire is AI-assisted local reporting. Software drafts every story
from the public record: court agendas, meeting videos, official posts, election
filings, weather alerts. A local human reads every word and approves each story
before it publishes. Nothing goes out on autopilot.</p>
<p><a href="about.html">How this works</a></p>
</div>"""


def _img_tag(a):
    if a.get("_image_data"):
        return '<img src="%s" alt="%s">' % (a["_image_data"], html.escape(a["title"]))
    if a.get("_image_url"):
        return '<img src="%s" alt="%s">' % (a["_image_url"], html.escape(a["title"]))
    return ""


def render_frontpage(articles):
    """Newspaper front page: lead story, secondary stories in a grid,
    and a sidebar rail (election box, about blurb, archives by desk)."""
    if not articles:
        return "<p>No stories yet.</p>"
    lead = articles[0]
    rest = articles[1:]
    lead_url = ARTICLE_URL % (lead["date"], lead["slug"])
    lead_img = _img_tag(lead)
    lead_html = (
        '<section class="lead">%s<div class="pad">'
        '<div class="desk">%s</div>'
        '<h2 class="lead-head"><a href="%s">%s</a></h2>'
        '<div class="dateline">%s</div>'
        '<p class="summary">%s</p>'
        '<a class="readmore" href="%s">Read the full story</a>'
        "</div></section>"
        % (lead_img, html.escape(lead["desk"]), lead_url,
           html.escape(lead["title"]), html.escape(fmt_date(lead["date"])),
           html.escape(lead["summary"]), lead_url))

    sec_items = []
    for a in rest:
        url = ARTICLE_URL % (a["date"], a["slug"])
        sec_items.append(
            '<div class="sec-story">'
            '<div class="desk">%s</div>'
            '<h3><a href="%s">%s</a></h3>'
            '<div class="dateline">%s</div>'
            '<p>%s</p>'
            "</div>"
            % (html.escape(a["desk"]), url, html.escape(a["title"]),
               html.escape(fmt_date(a["date"])), html.escape(a["summary"])))
    secondary = ""
    if sec_items:
        secondary = ('<section><h3 class="section-head">More from the Wire</h3>'
                     '<div class="sec-grid">%s</div></section>' % "".join(sec_items))

    # Archives: every published article grouped by desk (including the lead)
    by_desk = {}
    desk_order = []
    for a in articles:
        d = a["desk"]
        if d not in by_desk:
            by_desk[d] = []
            desk_order.append(d)
        by_desk[d].append(a)
    arch_parts = []
    for d in desk_order:
        links = "".join(
            '<li><a href="%s">%s</a></li>' % (ARTICLE_URL % (a["date"], a["slug"]), html.escape(a["title"]))
            for a in by_desk[d])
        arch_parts.append('<div class="rail-desk-label">%s</div><ul class="desk-list">%s</ul>'
                          % (html.escape(d), links))
    archives_box = ('<div class="rail-box"><h3>Archives by desk</h3>%s</div>'
                    % "".join(arch_parts))

    return ('<hr class="fp-rule"><div class="fp"><div class="fp-main">%s%s</div>'
            '<aside>%s%s%s</aside></div>'
            % (lead_html, secondary, ELECTION_BOX, ABOUT_BOX, archives_box))


def render_article(a):
    img = ""
    if a.get("_image_data"):
        img = '<div class="hero"><img src="%s" alt="%s"></div>' % (a["_image_data"], html.escape(a["title"]))
    elif a.get("_image_url"):
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
<h2>A note from the founder</h2>
<p>I started The Kendall Wire because Kendall County runs on meetings nobody has
time to attend.</p>
<p>Every week, the commissioners court votes on your tax rate, your roads, and
your water. The city council zones the land next to your neighborhood. The
school board spends your bond money. All of it is public. Almost none of it
gets read, because the agendas are long, the videos are longer, and the local
paper cannot be everywhere.</p>
<p>So I built a Wire that can.</p>
<p>Here is how it works, in the open. Software reads the public record: court
agendas, meeting videos, sheriff and fire marshal posts, election filings,
weather alerts. It drafts the story from those sources. Then I read every word,
check it against the source, and decide whether it meets the standard. Nothing
publishes that I have not approved.</p>
<p>The software is AI, and I do not hide that, because a news source that hides
how it works has already failed its own test. What matters is who answers for
the story. That is a person who lives in this county.</p>
<p>What the Wire will do: cover the meetings, the elections, the water, the
weather, and the Friday night scores. Straight news. Sources linked. No spin
from either direction.</p>
<p>What the Wire will not do: rumors, outrage bait, or naming private people
caught up in an incident. If I get something wrong, I will correct it in the
open.</p>
<p>Kendall County deserves to know what its government is doing. Now it has a
Wire.</p>
<h2>How the stories are made</h2>
<p>The Kendall Wire is an AI-assisted local news page for Kendall County, Texas.
AI systems monitor public sources, court agendas and videos, official
announcements, and local reporting, then draft each story. <strong>Every story
is reviewed and approved by a local human before it is published.</strong>
Nothing goes live on autopilot.</p>
<p>We report the news straight: no spin, no rumors, no outrage bait. When we get
something wrong, we correct it plainly.</p>
<h2>The desks</h2>
<ul class="desks">
<li><strong>Courts Desk</strong>: commissioners court, agendas, votes, county government.</li>
<li><strong>City Desk</strong>: Boerne city council, zoning, annexations, municipal news.</li>
<li><strong>Schools Desk</strong>: Boerne ISD board, bonds, closures, district news.</li>
<li><strong>Weather Desk</strong>: alerts, burn bans, flood and storm coverage.</li>
<li><strong>Development Desk</strong>: plats, subdivisions, road projects, growth.</li>
<li><strong>Elections Desk</strong>: ballots, turnout, results. Runs on its own track during election season.</li>
<li><strong>Water Desk</strong>: groundwater districts, drought stages, lake levels, water policy.</li>
<li><strong>Sports Desk</strong>: Friday night finals and local athletics.</li>
<li><strong>Community Desk</strong>: the life of the county, events, history, neighbors.</li>
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


def build(articles_dir, out_dir, base_assets_dir, inline_images=False):
    articles = load_articles(articles_dir)
    print("Loaded %d articles" % len(articles))

    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    copy_assets(articles, base_assets_dir, os.path.join(out_dir, "assets"), inline=inline_images)

    with open(os.path.join(out_dir, "style.css"), "w", encoding="utf-8") as fh:
        fh.write(CSS)

    def page(title, description, content, css_path="style.css", home="index.html",
             wrap_class="wrap"):
        return BASE_HTML.format(
            title=html.escape(title), description=html.escape(description),
            css_path=css_path, home=home, about="about.html", feed="feed.xml",
            tagline=SITE_TAGLINE, content=content,
            edition_date=edition_date_str(), wrap_class=wrap_class)

    # Index (newspaper front page)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(SITE_NAME, SITE_TAGLINE, render_frontpage(articles),
                      wrap_class="wrap wide"))

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
                tagline=SITE_TAGLINE, content=render_article(a),
                edition_date=edition_date_str(), wrap_class="wrap")
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
    inline = os.environ.get("WIRE_INLINE_IMAGES", "") == "1"
    build(args.articles, args.out, args.assets, inline_images=inline)


if __name__ == "__main__":
    main()
