#!/usr/bin/env python3
"""
build_site.py: static site generator for The Kendall Wire website.

Reads article markdown files with frontmatter from articles/ and renders
a complete static site into docs/ (GitHub Pages serves docs/ on the main
branch).

Usage:
    python3 build_site.py [--articles DIR] [--out DIR] [--assets DIR]

Frontmatter fields: title, date, slug, desk, summary, image, sources (list
of {name, url} or bare URLs), optional facebook (post URL).

The build writes in place. It does not delete docs/podcast, the subscription
pages, article HTML that has no markdown source, or images already in the
output. Pass --prune only when every published story has markdown and you
intend to drop the rest.

Stdlib only. No dependencies.
"""

import argparse
import base64
import calendar
import datetime
import html
import io
import os
import re
import shutil
import sys
from email.utils import formatdate

SITE_NAME = "The Kendall Wire"
SITE_TAGLINE = "Kendall County, reported straight."
SITE_URL = "https://kendallwire.com"

# Cloudflare Web Analytics beacon token for kendallwire.com. This is a
# public site token (Cloudflare expects it in every page); it is not an
# API secret. Empty string disables the beacon.
CF_BEACON_TOKEN = "73d6c586f51749e79f281697aa8763e4"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_IMAGE_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:jpg|jpeg|png|gif|webp)$", re.I)
_HERO_DATA_RE = re.compile(
    r"^data:image/(?:jpeg|png|gif|webp);base64,[A-Za-z0-9+/=\s]+$")


class BuildError(Exception):
    """The build cannot continue safely."""


def _is_within(parent, path):
    parent = os.path.realpath(parent)
    path = os.path.realpath(path)
    if path == parent:
        return True
    try:
        return os.path.commonpath([parent, path]) == parent
    except ValueError:
        return False


def assert_safe_out(out_dir, articles_dir):
    """Refuse output paths that would wipe the machine or the source tree."""
    out_real = os.path.realpath(out_dir)
    home = os.path.realpath(os.path.expanduser("~"))
    if out_real in ("/", home):
        raise BuildError("refusing to build into %s" % out_dir)
    articles_real = os.path.realpath(articles_dir)
    if _is_within(articles_real, out_real) or _is_within(out_real, articles_real):
        raise BuildError(
            "refusing to build into %s: output and articles directories overlap" % out_dir)


def valid_date(date):
    if not date or not _DATE_RE.match(date):
        return False
    try:
        year, month, day = (int(part) for part in date.split("-"))
        datetime.date(year, month, day)
    except ValueError:
        return False
    return True


def valid_article_id(date, slug):
    return bool(valid_date(date) and slug and _SLUG_RE.match(slug))


def article_output_dir(out_dir, date, slug):
    if not valid_article_id(date, slug):
        raise BuildError("unsafe article date or slug: %s / %s" % (date, slug))
    dest = os.path.join(out_dir, "articles", date, slug)
    if not _is_within(out_dir, dest):
        raise BuildError("article path escapes the output directory: %s" % dest)
    return dest


def _unquote(val):
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        inner = val[1:-1]
        if val[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        else:
            inner = inner.replace("\\'", "'")
        return inner
    return val


def safe_url(url):
    """Allow http(s), mailto, and same-site relative links. Drop every other scheme."""
    if url is None:
        return ""
    url = str(url).strip()
    if not url or re.search(r"[\x00-\x1f\s]", url):
        return ""
    if url.startswith(("//", "\\\\")):
        return ""
    lowered = url.lower()
    if lowered.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return ""
    if lowered.startswith(("http://", "https://", "mailto:")):
        return url
    # Relative link. A colon in the first path segment is a scheme we do not allow.
    if ":" in url.split("/", 1)[0]:
        return ""
    if "\\" in url:
        return ""
    return url


def image_basename(img):
    """Reduce an image field to a filename inside the assets directory.

    Absolute paths are not opened. Only the basename is used, and only when
    it looks like an image file. That keeps frontmatter from reading or
    copying arbitrary local files.
    """
    if not img:
        return ""
    name = os.path.basename(str(img).strip().replace("\\", "/"))
    if not _IMAGE_NAME_RE.match(name):
        return ""
    return name


def rfc822_date(datestr):
    year, month, day = (int(part) for part in datestr.split("-"))
    stamp = calendar.timegm((year, month, day, 12, 0, 0, 0, 0, 0))
    return formatdate(stamp, usegmt=True)


def default_assets_dir(repo_dir):
    """Prefer an in-repo assets directory, then the legacy external one, then docs/assets."""
    legacy = os.path.join(os.path.expanduser("~"), "workspace", "hill-country-weird", "assets")
    candidates = [
        os.path.join(repo_dir, "assets"),
        legacy,
        os.path.join(repo_dir, "docs", "assets"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(repo_dir, "assets")

def analytics_snippet():
    if not CF_BEACON_TOKEN:
        return ""
    return ("<script type='module' src=\"https://static.cloudflareinsights.com/beacon.min.js\" "
            "data-cf-beacon='{\"token\": \"" + CF_BEACON_TOKEN + "\"}'></script>")

# ---------------------------------------------------------------------------
# Minimal frontmatter parser (subset of YAML: scalars and lists of mappings)
# ---------------------------------------------------------------------------

def parse_frontmatter(text):
    """Split '---' frontmatter from body. Returns (dict, body).

    Supports scalars (quoted or not), literal and folded block scalars,
    and lists of strings or single-level mappings. Keys may contain hyphens.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 4:].lstrip("\n")
    meta = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line[0] in " \t":
            i += 1
            continue
        matched = _KEY_RE.match(line)
        if not matched:
            i += 1
            continue
        key, val = matched.group(1), matched.group(2).strip()
        if val in ("|", ">"):
            folded = val == ">"
            block = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
                if lines[i].strip():
                    block.append(lines[i].strip())
                elif block:
                    block.append("")
                i += 1
            text_block = " ".join(part for part in block if part != "") if folded else "\n".join(block)
            meta[key] = text_block.strip()
            continue
        if val == "":
            items = []
            i += 1
            while i < len(lines):
                if not lines[i].strip():
                    i += 1
                    continue
                if lines[i][0] not in " \t" and not lines[i].lstrip().startswith("-"):
                    break
                stripped = lines[i].strip()
                if stripped.startswith("- ") or stripped == "-":
                    item = stripped[2:].strip()
                    # A bare URL contains a colon, but it is not a "key: value" mapping.
                    if re.match(r"^https?://", item, re.I) or re.match(r"^mailto:", item, re.I):
                        items.append(_unquote(item))
                    else:
                        item_key = _KEY_RE.match(item) if item else None
                        if item_key:
                            items.append({item_key.group(1): _unquote(item_key.group(2))})
                        elif item:
                            items.append(_unquote(item))
                        else:
                            items.append({})
                    i += 1
                    continue
                nested = _KEY_RE.match(stripped)
                if nested and items and isinstance(items[-1], dict):
                    items[-1][nested.group(1)] = _unquote(nested.group(2))
                i += 1
            meta[key] = items
            continue
        meta[key] = _unquote(val)
        i += 1
    return meta, body


# ---------------------------------------------------------------------------
# Minimal markdown to HTML (paragraphs, headings, bold, italic, links, lists,
# blockquotes). Articles use simple markdown only.
# ---------------------------------------------------------------------------

def _link_repl(match):
    label = match.group(1)
    href = safe_url(html.unescape(match.group(2).strip()))
    if not href:
        return label
    return '<a href="%s">%s</a>' % (html.escape(href, quote=True), label)


def inline_md(text):
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link_repl, t)
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
.fp{display:flex;flex-direction:column}
.main-col{order:2}
.rail{display:contents}
.rail-box.about-box{order:3}
.rail-box.more-box{order:4}
.meta{font-size:.75rem;letter-spacing:1px;text-transform:uppercase;margin:0 0 4px}
.meta-desk{color:var(--ink);font-weight:bold}
.meta-date{color:var(--ink-soft)}
.lead{border:1px solid var(--rule);outline:3px double var(--rule);outline-offset:3px;
  background:#FBF8F1;margin:4px 0 24px;overflow:hidden}
.lead img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;border-bottom:1px solid var(--rule)}
.lead .pad{padding:18px 20px 22px}
.lead h2.lead-head{font-size:2rem;line-height:1.15;margin:6px 0 10px}
.lead h2.lead-head a{color:var(--ink);text-decoration:none}
.lead h2.lead-head a:hover{text-decoration:underline}
.lead p.summary{font-size:1rem;line-height:1.5;color:var(--ink-soft);margin:0}
.sec-story{border-bottom:1px solid var(--rule);padding:18px 0}
.sec-story:last-child{border-bottom:0}
.sec-story h3{font-size:1.5rem;margin:6px 0 8px;line-height:1.25}
.sec-story h3 a{color:var(--ink);text-decoration:none}
.sec-story h3 a:hover{text-decoration:underline}
.sec-story p{font-size:1rem;line-height:1.5;color:var(--ink-soft);margin:0}
.rail-box{border:1px solid var(--rule);background:#FBF8F1;padding:16px 18px;margin:0 0 22px}
.rail-box h3{margin:0 0 8px;font-size:.9rem;letter-spacing:2px;text-transform:uppercase;color:var(--ink)}
.rail-box p{font-size:.95rem;line-height:1.5;margin:0 0 .8em;color:var(--ink-soft)}
.rail-box p:last-child{margin-bottom:0}
.rail-box a{color:var(--ink)}
ul.more-list{list-style:none;padding:0;margin:0}
ul.more-list li{padding:10px 0;border-bottom:1px dotted var(--ink-soft);font-size:1rem;font-weight:bold;line-height:1.35}
ul.more-list li:last-child{border-bottom:0;padding-bottom:0}
ul.more-list a{color:var(--ink);text-decoration:none}
ul.more-list a:hover{text-decoration:underline}
ul.desk-list{list-style:none;padding:0;margin:0}
ul.desk-list li{padding:7px 0;border-bottom:1px dotted var(--ink-soft);font-size:.88rem}
ul.desk-list li:last-child{border-bottom:0}
ul.desk-list a{color:var(--ink);text-decoration:none}
ul.desk-list a:hover{text-decoration:underline}
.rail-desk-label{font-size:.72rem;letter-spacing:2px;text-transform:uppercase;color:var(--accent);margin:14px 0 2px}
@media(min-width:900px){
  .fp{display:grid;grid-template-columns:1fr 300px;gap:34px;align-items:start}
  .rail{display:block}
  .main-col{grid-column:1;grid-row:1;order:0}
  .rail{grid-column:2;grid-row:1;order:0}
  .rail-box.about-box,.rail-box.more-box{order:0}
  .lead h2.lead-head{font-size:2.5rem}
  .masthead h1{font-size:3.2rem}
}
footer{border-top:3px double var(--rule);margin-top:40px;padding:18px 0 34px;
  text-align:center;font-size:.8rem;color:var(--ink-soft)}
footer a{color:var(--ink)}
.newsletter-box{border-top:1px dotted var(--rule);margin-top:16px;padding-top:14px}
.newsletter-box p{margin:6px 0}
.newsletter-box input[type=email]{font-size:.9rem;padding:6px 8px;border:1px solid var(--rule);width:230px;max-width:70%}
.newsletter-box input[type=submit]{font-size:.9rem;padding:6px 12px;border:1px solid var(--ink);background:var(--ink);color:#fff;cursor:pointer}
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
{analytics}</head>
<body>
<header class="masthead"><div class="wrap">
<div class="kicker">Kendall County &bull; Local News</div>
<h1><a href="{home}">The Kendall Wire</a></h1>
<div class="tagline">{tagline}</div>
<div class="dateline-top">Boerne, Texas &bull; {edition_date}</div>
</div></header>
<nav class="top"><div class="wrap">
<a href="{home}">Latest</a><a href="{about}">About</a>
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
        year, month, day = datestr.split("-")
        return "%s %d, %s" % (calendar.month_name[int(month)], int(day), year)
    except Exception:
        return datestr


def edition_date_str():
    """Masthead date line, e.g. 'Monday, October 19, 2026'.

    WIRE_EDITION_DATE=YYYY-MM-DD overrides the clock so a rebuild can be
    checked without the date line drifting.
    """
    override = os.environ.get("WIRE_EDITION_DATE", "").strip()
    if override:
        year, month, day = (int(part) for part in override.split("-"))
        day_value = datetime.date(year, month, day)
    else:
        day_value = datetime.date.today()
    return day_value.strftime("%A, %B %d, %Y").replace(" 0", " ")


def strip_private_lines(body):
    """Drop production notes that must not ship: local paths and review flags."""
    kept = []
    skipping_image = False
    for line in body.splitlines():
        if re.match(r"^## Image\s*$", line.strip()):
            skipping_image = True
            continue
        if skipping_image:
            if line.startswith("#"):
                skipping_image = False
            else:
                continue
        if "/home/" in line or "/Users/" in line:
            continue
        if re.search(r"not approved for publication", line, re.I):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def strip_duplicate_title(body, title):
    lines = body.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines):
        line = lines[index].strip()
        for prefix in ("# ", "## "):
            if line.startswith(prefix) and line[len(prefix):].strip() == title.strip():
                del lines[index]
                break
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def load_articles(articles_dir):
    """Return (articles, problems). Problems are fatal and fail the build."""
    articles = []
    problems = []
    seen = {}
    for root, _dirs, files in os.walk(articles_dir):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                meta, body = parse_frontmatter(fh.read())
            if "title" not in meta or "date" not in meta:
                print("SKIP (missing title/date): %s" % path, file=sys.stderr)
                continue
            if meta.get("status") != "published":
                print("SKIP (not published): %s" % path, file=sys.stderr)
                continue
            meta.setdefault("slug", os.path.splitext(name)[0])
            if not valid_article_id(meta.get("date"), meta.get("slug")):
                problems.append(
                    "%s: date must be YYYY-MM-DD and slug must be lowercase letters, digits, and hyphens"
                    % path)
                continue
            meta["_path"] = path
            meta["_body"] = strip_duplicate_title(strip_private_lines(body), meta["title"])
            if "desk" not in meta and "category" in meta:
                meta["desk"] = str(meta["category"]).title() + " Desk"
            meta.setdefault("desk", "Community Desk")
            if "summary" not in meta and meta.get("summary-for-facebook"):
                meta["summary"] = str(meta["summary-for-facebook"]).strip().split("\n")[0]
            if "summary" not in meta and meta.get("summary_for_facebook"):
                meta["summary"] = str(meta["summary_for_facebook"]).strip().split("\n")[0]
            meta.setdefault("summary", "")
            meta.setdefault("sources", [])
            key = (meta["date"], meta["slug"])
            if key in seen:
                problems.append(
                    "duplicate published article %s/%s (%s and %s)"
                    % (key[0], key[1], seen[key], path))
                continue
            seen[key] = path
            articles.append(meta)
    articles.sort(key=lambda article: (article["date"], article.get("slug", "")), reverse=True)
    return articles, problems


def copy_assets(articles, base_assets_dir, out_assets_dir, inline=False):
    if not inline:
        os.makedirs(out_assets_dir, exist_ok=True)
    for article in articles:
        name = image_basename(article.get("image", ""))
        raw_image = str(article.get("image", "") or "").strip()
        if raw_image and not name:
            print("WARN: ignoring image path that is not a plain image filename: %s" % raw_image,
                  file=sys.stderr)
            article["_image_url"] = ""
            continue
        if not name:
            article["_image_url"] = ""
            continue
        if raw_image.replace("\\", "/") not in (name, "assets/" + name, "./assets/" + name):
            print("WARN: using assets/%s for image field %s (paths outside the assets directory are not read)"
                  % (name, raw_image), file=sys.stderr)
        src = os.path.join(base_assets_dir, name)
        if os.path.islink(src) or not _is_within(base_assets_dir, src):
            print("WARN: refusing to copy image outside the assets directory: %s" % name, file=sys.stderr)
            article["_image_url"] = ""
            continue
        if not os.path.isfile(src):
            print("WARN: image not found in assets dir: %s" % name, file=sys.stderr)
            article["_image_url"] = ""
            continue
        if inline:
            article["_image_data"] = image_data_uri(src)
            article["_image_url"] = ""
        else:
            dst = os.path.join(out_assets_dir, name)
            if not (os.path.exists(dst) and os.path.samefile(src, dst)):
                shutil.copy2(src, dst)
            article["_image_url"] = "assets/" + name


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

ABOUT_BOX = """<div class="rail-box about-box">
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


def _first_sentence(text):
    text = " ".join(str(text).split())
    idx = text.find(". ")
    if idx != -1:
        return text[:idx + 1]
    return text


def _lead_img_tag(a):
    """Front page lead image: like _img_tag, but recompressed toward the
    redesign's page-weight budget (about 60 KB before base64)."""
    data = a.get("_image_data")
    if data and data.startswith("data:image/jpeg;base64,"):
        try:
            from PIL import Image
            raw = base64.b64decode(data.split(",", 1)[1])
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            out = None
            for width, q in ((880, 64), (880, 54), (820, 46), (760, 40), (700, 34)):
                cur = im
                if cur.width != width:
                    cur = im.resize((width, int(width * im.height / im.width)), Image.LANCZOS)
                buf = io.BytesIO()
                cur.save(buf, "JPEG", quality=q, optimize=True)
                out = buf.getvalue()
                if len(out) <= 62000:
                    break
            if out is not None:
                return '<img src="data:image/jpeg;base64,%s" alt="%s">' % (
                    base64.b64encode(out).decode("ascii"), html.escape(a["title"]))
        except Exception:
            pass
    return _img_tag(a)


def _meta_line(a):
    return ('<div class="meta"><span class="meta-desk">%s</span> &nbsp;|&nbsp; '
            '<span class="meta-date">%s</span></div>'
            % (html.escape(a["desk"]), html.escape(fmt_date(a["date"]))))


def render_frontpage(articles):
    """Newspaper front page: one lead story with image, secondary stories
    in a single chronological column with one-sentence summaries, and a
    rail with the about box and a More Headlines list. The November 3,
    2026 election box used to sit in that rail; the published homepage
    dropped it after election day, and the generator matches that."""
    if not articles:
        return "<p>No stories yet.</p>"
    lead = articles[0]
    secondary = articles[1:6]
    more = articles[6:13]
    lead_url = ARTICLE_URL % (lead["date"], lead["slug"])
    lead_html = (
        '<article class="lead">%s<div class="pad">%s'
        '<h2 class="lead-head"><a href="%s">%s</a></h2>'
        '<p class="summary">%s</p>'
        "</div></article>"
        % (_lead_img_tag(lead), _meta_line(lead), lead_url,
           html.escape(lead["title"]), html.escape(lead["summary"])))
    sec_items = []
    for a in secondary:
        url = ARTICLE_URL % (a["date"], a["slug"])
        sec_items.append(
            '<article class="sec-story">%s'
            '<h3><a href="%s">%s</a></h3>'
            "<p>%s</p></article>"
            % (_meta_line(a), url, html.escape(a["title"]),
               html.escape(_first_sentence(a["summary"]))))
    more_box = ""
    if more:
        lis = "".join(
            '<li><a href="%s">%s</a></li>'
            % (ARTICLE_URL % (a["date"], a["slug"]), html.escape(a["title"]))
            for a in more)
        more_box = ('<div class="rail-box more-box"><h3>More Headlines</h3>'
                    '<ul class="more-list">%s</ul></div>' % lis)
    return ('<hr class="fp-rule"><div class="fp"><main class="main-col">%s%s</main>'
            '<aside class="rail">%s%s</aside></div>'
            % (lead_html, "".join(sec_items), ABOUT_BOX, more_box))


def _hero_html(article):
    alt = html.escape(article["title"])
    if article.get("_image_data"):
        src = article["_image_data"]
        if not _HERO_DATA_RE.match(src):
            return ""
        return '<div class="hero"><img src="%s" alt="%s"></div>' % (src, alt)
    if article.get("_image_url"):
        return '<div class="hero"><img src="../../%s" alt="%s"></div>' % (html.escape(article["_image_url"], quote=True), alt)
    preserved = article.get("_preserved_hero") or ""
    if preserved.startswith("data:image/"):
        if not _HERO_DATA_RE.match(preserved):
            return ""
        return '<div class="hero"><img src="%s" alt="%s"></div>' % (preserved, alt)
    if preserved.startswith("../../assets/"):
        name = preserved[len("../../assets/"):]
        if _IMAGE_NAME_RE.match(name):
            return '<div class="hero"><img src="../../assets/%s" alt="%s"></div>' % (html.escape(name, quote=True), alt)
    return ""


def _source_items(sources):
    items = []
    for source in sources or []:
        if isinstance(source, dict) and source.get("url"):
            href = safe_url(source["url"])
            label = source.get("name") or source["url"]
            if href:
                items.append('<li><a href="%s">%s</a></li>' % (html.escape(href, quote=True), html.escape(str(label))))
            elif not (re.match(r"^[a-z][a-z0-9+.-]*:", str(label), re.I) and not safe_url(str(label))):
                items.append("<li>%s</li>" % html.escape(str(label)))
        elif isinstance(source, str):
            href = safe_url(source)
            if not href and re.match(r"^[a-z][a-z0-9+.-]*:", source, re.I):
                continue
            if href and href.lower().startswith(("http://", "https://", "mailto:")):
                items.append('<li><a href="%s">%s</a></li>' % (html.escape(href, quote=True), html.escape(source)))
            else:
                items.append("<li>%s</li>" % html.escape(source))
    return items


def render_article(a):
    img = _hero_html(a)
    fb = ""
    facebook = safe_url(a.get("facebook"))
    if facebook:
        fb = '<p><a href="%s">Discuss this story on Facebook</a></p>' % html.escape(facebook, quote=True)
    sources = ""
    lis = _source_items(a.get("sources") or [])
    if lis:
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
    for article in articles[:20]:
        url = "%s/articles/%s/%s/" % (SITE_URL, article["date"], article["slug"])
        escaped_url = html.escape(url, quote=True)
        items.append(
            "<item><title>%s</title><link>%s</link><guid>%s</guid>"
            "<pubDate>%s</pubDate><description>%s</description></item>"
            % (html.escape(article["title"]), escaped_url, escaped_url,
               html.escape(rfc822_date(article["date"])),
               html.escape(article["summary"])))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0">'
            "<channel><title>The Kendall Wire</title>"
            "<link>%s</link><description>%s</description>%s</channel></rss>"
            % (SITE_URL, html.escape(SITE_TAGLINE), "".join(items)))


def render_sitemap(articles, extra_urls=()):
    urls = [SITE_URL + "/", SITE_URL + "/about.html"]
    urls.extend(extra_urls)
    for article in articles:
        urls.append("%s/articles/%s/%s/" % (SITE_URL, article["date"], article["slug"]))
    body = "".join("<url><loc>%s</loc></url>" % html.escape(url, quote=True) for url in urls)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + body + "</urlset>")


def _static_urls(out_dir):
    extras = []
    if os.path.isfile(os.path.join(out_dir, "check-inbox.html")):
        extras.append(SITE_URL + "/check-inbox.html")
    if os.path.isfile(os.path.join(out_dir, "subscribed.html")):
        extras.append(SITE_URL + "/subscribed.html")
    if os.path.isfile(os.path.join(out_dir, "podcast", "index.html")):
        extras.append(SITE_URL + "/podcast/")
    return extras


def _read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def existing_hero_src(article_index_path):
    if not os.path.isfile(article_index_path):
        return ""
    text = _read_text(article_index_path)
    matched = re.search(r'<div class="hero"><img src="([^"]+)"', text)
    if not matched:
        return ""
    src = matched.group(1)
    if src.startswith("data:image/"):
        return src if _HERO_DATA_RE.match(src) else ""
    if "/assets/" in src and "data:" not in src:
        name = src.split("/assets/")[-1]
        if _IMAGE_NAME_RE.match(name):
            return "../../assets/" + name
    return ""


def article_meta_from_html(path, date, slug):
    """Metadata for a published page whose markdown is not in this repo."""
    text = _read_text(path)
    title_match = re.search(r'<h2 class="title">(.*?)</h2>', text, re.S)
    if not title_match:
        title_match = re.search(r"<title>(.*?) - The Kendall Wire</title>", text, re.S)
    if not title_match:
        return None
    summary_match = re.search(r'<meta name="description" content="(.*?)">', text, re.S)
    desk_match = re.search(r'<div class="desk">(.*?)</div>', text, re.S)
    image_url = ""
    hero_match = re.search(r'<div class="hero"><img src="([^"]+)"', text)
    if hero_match and "data:" not in hero_match.group(1) and "/assets/" in hero_match.group(1):
        name = hero_match.group(1).split("/assets/")[-1]
        if _IMAGE_NAME_RE.match(name):
            image_url = "assets/" + name
    return {
        "title": html.unescape(title_match.group(1)).strip(),
        "date": date,
        "slug": slug,
        "desk": html.unescape(desk_match.group(1)).strip() if desk_match else "Community Desk",
        "summary": html.unescape(summary_match.group(1)).strip() if summary_match else "",
        "sources": [],
        "_image_url": image_url,
        "_body": "",
        "_preserved": True,
    }


def load_preserved_articles(out_dir, have_keys):
    preserved = []
    root = os.path.join(out_dir, "articles")
    if not os.path.isdir(root):
        return preserved
    for date in sorted(os.listdir(root)):
        date_dir = os.path.join(root, date)
        if not os.path.isdir(date_dir) or not valid_date(date):
            continue
        for slug in sorted(os.listdir(date_dir)):
            if (date, slug) in have_keys or not _SLUG_RE.match(slug):
                continue
            index_path = os.path.join(date_dir, slug, "index.html")
            if not os.path.isfile(index_path) or not _is_within(root, index_path):
                continue
            meta = article_meta_from_html(index_path, date, slug)
            if meta:
                preserved.append(meta)
    return preserved


def prune_articles(out_dir, keep_keys):
    root = os.path.join(out_dir, "articles")
    if not os.path.isdir(root):
        return 0
    removed = 0
    for date in list(os.listdir(root)):
        date_dir = os.path.join(root, date)
        if not os.path.isdir(date_dir) or not _is_within(root, date_dir):
            continue
        for slug in list(os.listdir(date_dir)):
            slug_dir = os.path.join(date_dir, slug)
            if (date, slug) in keep_keys or not os.path.isdir(slug_dir):
                continue
            if not _is_within(root, slug_dir):
                continue
            shutil.rmtree(slug_dir)
            removed += 1
        if os.path.isdir(date_dir) and not os.listdir(date_dir):
            os.rmdir(date_dir)
    return removed


def build(articles_dir, out_dir, base_assets_dir, inline_images=False, prune=False):
    articles, problems = load_articles(articles_dir)
    if problems:
        raise BuildError("\n".join(problems))
    print("Loaded %d articles from %s" % (len(articles), articles_dir))
    assert_safe_out(out_dir, articles_dir)
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.isdir(base_assets_dir):
        print("WARN: assets directory does not exist: %s" % base_assets_dir, file=sys.stderr)
    out_assets = os.path.join(out_dir, "assets")
    copy_assets(articles, base_assets_dir, out_assets, inline=inline_images)

    for article in articles:
        if article.get("_image_url") or article.get("_image_data"):
            continue
        previous = os.path.join(
            out_dir, "articles", article["date"], article["slug"], "index.html")
        hero = existing_hero_src(previous)
        if hero:
            article["_preserved_hero"] = hero
            print("Keeping existing hero for %s/%s" % (article["date"], article["slug"]),
                  file=sys.stderr)

    have = set((article["date"], article["slug"]) for article in articles)
    if prune:
        removed = prune_articles(out_dir, have)
        print("Pruned %d article director%s" % (removed, "y" if removed == 1 else "ies"),
              file=sys.stderr)
        preserved = []
    else:
        preserved = load_preserved_articles(out_dir, have)
        if preserved:
            print("Keeping %d published article(s) that have no markdown source."
                  % len(preserved), file=sys.stderr)
    combined = articles + preserved
    combined.sort(key=lambda article: (article["date"], article.get("slug", "")), reverse=True)

    with open(os.path.join(out_dir, "style.css"), "w", encoding="utf-8") as fh:
        fh.write(CSS)

    def page(title, description, content, css_path="style.css", home="index.html",
             wrap_class="wrap"):
        return BASE_HTML.format(
            title=html.escape(title), description=html.escape(description),
            css_path=css_path, home=home, about="about.html", feed="feed.xml",
            tagline=SITE_TAGLINE, content=content, analytics=analytics_snippet(),
            edition_date=edition_date_str(), wrap_class=wrap_class)

    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(SITE_NAME, SITE_TAGLINE, render_frontpage(combined),
                      wrap_class="wrap wide"))

    with open(os.path.join(out_dir, "about.html"), "w", encoding="utf-8") as fh:
        fh.write(page("About - " + SITE_NAME, "How The Kendall Wire works.", ABOUT_HTML))

    for article in articles:
        dest = article_output_dir(out_dir, article["date"], article["slug"])
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "index.html"), "w", encoding="utf-8") as fh:
            art = BASE_HTML.format(
                title=html.escape(article["title"] + " - " + SITE_NAME),
                description=html.escape(article["summary"]),
                css_path="../../../style.css", home="../../../index.html",
                about="../../../about.html", feed="../../../feed.xml",
                tagline=SITE_TAGLINE, content=render_article(article),
                analytics=analytics_snippet(),
                edition_date=edition_date_str(), wrap_class="wrap")
            fh.write(art)

    with open(os.path.join(out_dir, "feed.xml"), "w", encoding="utf-8") as fh:
        fh.write(render_rss(combined))
    with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write(render_sitemap(combined, _static_urls(out_dir)))
    with open(os.path.join(out_dir, ".nojekyll"), "w", encoding="utf-8") as fh:
        fh.write("")
    with open(os.path.join(out_dir, "CNAME"), "w", encoding="utf-8") as fh:
        fh.write("kendallwire.com\n")

    print("Wrote site to %s (%d stories on the homepage)" % (out_dir, len(combined)))
    return articles


def main(argv=None):
    repo = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", default=os.path.join(repo, "articles"))
    ap.add_argument("--out", default=os.path.join(repo, "docs"))
    ap.add_argument("--assets", default=None,
                    help="Directory of story images. Defaults to ./assets, then the legacy "
                         "hill-country-weird assets folder, then ./docs/assets.")
    ap.add_argument("--prune", action="store_true",
                    help="Delete article output folders that are not in the published markdown set. "
                         "Off by default so a partial articles/ directory cannot wipe the live site.")
    args = ap.parse_args(argv)
    assets = args.assets or default_assets_dir(repo)
    inline = os.environ.get("WIRE_INLINE_IMAGES", "") == "1"
    try:
        build(args.articles, args.out, assets, inline_images=inline, prune=args.prune)
    except BuildError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
