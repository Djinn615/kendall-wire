"""Smoke tests for the Kendall Wire static site generator.

Stdlib unittest only. Builds go to temporary directories and never touch docs/.
"""

import html
import os
import tempfile
import unittest

import build_site as site


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class SiteTestCase(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("WIRE_EDITION_DATE")
        os.environ["WIRE_EDITION_DATE"] = "2026-11-16"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("WIRE_EDITION_DATE", None)
        else:
            os.environ["WIRE_EDITION_DATE"] = self._env

    def path(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def write_article(self, articles, date, slug, body, **fields):
        folder = os.path.join(articles, date)
        os.makedirs(folder, exist_ok=True)
        title = fields.get("title", slug.replace("-", " "))
        lines = [
            "---",
            'title: "%s"' % title.replace('"', '\\"'),
            "date: %s" % date,
            "slug: %s" % slug,
            "status: %s" % fields.get("status", "published"),
            'desk: "%s"' % fields.get("desk", "Community Desk"),
            'summary: "%s"' % fields.get("summary", "Summary for %s." % slug),
        ]
        if "image" in fields:
            lines.append("image: %s" % fields["image"])
        if "facebook" in fields:
            lines.append("facebook: %s" % fields["facebook"])
        sources = fields.get("sources")
        if sources:
            lines.append("sources:")
            for source in sources:
                if isinstance(source, str):
                    lines.append("  - %s" % source)
                else:
                    lines.append("  - name: %s" % source["name"])
                    lines.append("    url: %s" % source["url"])
        block = fields.get("summary_block")
        if block:
            lines.append("summary-for-facebook: |")
            for line in block.splitlines():
                lines.append("  %s" % line)
        lines.append("---")
        lines.append(body)
        with open(os.path.join(folder, slug + ".md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


class ParserAndUrlTests(SiteTestCase):
    def test_bare_https_sources_stay_urls(self):
        text = """---
title: "Weekend"
date: 2026-10-19
slug: weekend
status: published
sources:
  - https://business.boerne.org/events
  - https://www.boernestar.com/article/14374,calendar-of-events
summary-for-facebook: |
  Your weekend in Boerne.
  Second line stays in the block.
---

Body.
"""
        meta, body = site.parse_frontmatter(text)
        self.assertEqual(meta["sources"], [
            "https://business.boerne.org/events",
            "https://www.boernestar.com/article/14374,calendar-of-events",
        ])
        self.assertEqual(meta["summary-for-facebook"],
                         "Your weekend in Boerne.\nSecond line stays in the block.")
        self.assertEqual(body, "Body.\n")

    def test_named_sources_and_hyphenated_keys(self):
        text = read(os.path.join(
            ROOT, "articles", "2026-10-19", "water-watch-2026-10-19.md"))
        meta, _body = site.parse_frontmatter(text)
        self.assertGreaterEqual(len(meta["sources"]), 1)
        self.assertTrue(all(item.get("url", "").startswith("https://") for item in meta["sources"]))
        self.assertIn("summary-for-facebook", meta)
        self.assertTrue(meta["summary-for-facebook"])

    def test_safe_url_rejects_scriptable_schemes(self):
        self.assertEqual(site.safe_url("javascript:alert(1)"), "")
        self.assertEqual(site.safe_url("JavaScript:alert(1)"), "")
        self.assertEqual(site.safe_url("data:text/html,hi"), "")
        self.assertEqual(site.safe_url("vbscript:msg"), "")
        self.assertEqual(site.safe_url("file:///etc/passwd"), "")
        self.assertEqual(site.safe_url("//evil.example/phish"), "")
        self.assertEqual(site.safe_url("https://kendallwire.com/a"), "https://kendallwire.com/a")
        self.assertEqual(site.safe_url("mailto:tips@kendallwire.com"), "mailto:tips@kendallwire.com")
        self.assertEqual(site.safe_url("../about.html"), "../about.html")

    def test_image_basename_never_uses_a_directory(self):
        self.assertEqual(
            site.image_basename("/home/hatch/workspace/hill-country-weird/assets/card.jpg"),
            "card.jpg")
        self.assertEqual(site.image_basename("assets/card.jpg"), "card.jpg")
        self.assertEqual(site.image_basename("../../etc/passwd"), "")
        self.assertEqual(site.image_basename("not-an-image.txt"), "")


class BuildSafetyTests(SiteTestCase):
    def test_javascript_links_and_private_notes_do_not_ship(self):
        articles = self.path("articles")
        out = self.path("out")
        assets = self.path("assets")
        os.makedirs(assets)
        secret = self.path("secret.jpg")
        with open(secret, "wb") as fh:
            fh.write(b"SECRETPIXEL")
        os.symlink(secret, os.path.join(assets, "linked.jpg"))
        self.write_article(
            articles, "2026-10-23", "burn-note",
            "\n".join([
                "# Burn note",
                "",
                "The court meets Tuesday.",
                "",
                "See [the agenda](https://www.kendallcountytx.gov/AgendaCenter) and [click](javascript:alert(1)).",
                "",
                "## Image",
                "",
                "/home/hatch/workspace/hill-country-weird/assets/card.jpg",
                "(visually verified 2026-10-23: do not publish this)",
                "",
                "Nothing here is approved for publication until review.",
            ]),
            title="Burn note",
            image="/home/hatch/workspace/hill-country-weird/assets/linked.jpg",
            facebook="javascript:alert(1)",
            sources=[
                "https://www.kendallcountytx.gov/AgendaCenter",
                "javascript:alert(document.domain)",
            ],
        )
        site.build(articles, out, assets)
        page = read(os.path.join(
            out, "articles", "2026-10-23", "burn-note", "index.html"))
        self.assertIn("https://www.kendallcountytx.gov/AgendaCenter", page)
        self.assertIn("The court meets Tuesday.", page)
        self.assertNotIn("javascript:", page.lower())
        self.assertNotIn("/home/", page)
        self.assertNotIn("visually verified", page)
        self.assertNotIn("not approved for publication", page.lower())
        self.assertNotIn("SECRETPIXEL", page)
        self.assertNotIn("<h2>Burn note</h2>", page)
        self.assertEqual(page.count('<h2 class="title">Burn note</h2>'), 1)
        self.assertFalse(os.path.exists(os.path.join(out, "assets", "linked.jpg")))
        index = read(os.path.join(out, "index.html"))
        self.assertNotIn("Election 2026", index)
        self.assertIn("Monday, November 16, 2026", index)
        self.assertIn(".newsletter-box", read(os.path.join(out, "style.css")))

    def test_refuses_to_build_over_source_or_filesystem_root(self):
        articles = self.path("articles")
        os.makedirs(articles)
        with self.assertRaises(site.BuildError):
            site.assert_safe_out("/", articles)
        with self.assertRaises(site.BuildError):
            site.assert_safe_out(articles, articles)
        with self.assertRaises(site.BuildError):
            site.assert_safe_out(ROOT, os.path.join(ROOT, "articles"))

    def test_unsafe_slug_and_duplicate_fail_the_build(self):
        articles = self.path("articles")
        self.write_article(articles, "2026-02-31", "ok-slug", "Nope.", title="Bad date")
        with self.assertRaises(site.BuildError):
            site.build(articles, self.path("out"), self.path("assets"))
        articles = self.path("articles2")
        self.write_article(articles, "2026-10-01", "../escape", "Nope.", title="Bad slug")
        with self.assertRaises(site.BuildError):
            site.build(articles, self.path("out2"), self.path("assets"))
        articles = self.path("articles3")
        self.write_article(articles, "2026-10-01", "same-slug", "One.", title="One")
        # second file, same published identity
        folder = os.path.join(articles, "2026-10-01")
        with open(os.path.join(folder, "other.md"), "w", encoding="utf-8") as fh:
            fh.write(read(os.path.join(folder, "same-slug.md")))
        with self.assertRaises(site.BuildError):
            site.build(articles, self.path("out3"), self.path("assets"))

    def test_preserves_pages_the_generator_does_not_own(self):
        articles = self.path("articles")
        out = self.path("out")
        assets = self.path("assets")
        os.makedirs(assets)
        self.write_article(
            articles, "2026-01-02", "from-markdown",
            "Markdown body.",
            title="From markdown",
            summary="The markdown story.",
        )
        kept_dir = os.path.join(out, "articles", "2026-11-16", "kept-story")
        os.makedirs(kept_dir)
        kept_html = """<!DOCTYPE html>
<html><head>
<title>Kept Story - The Kendall Wire</title>
<meta name="description" content="A story that lives only as HTML.">
</head><body>
<article class="full"><div class="hero"><img src="../../assets/kept.jpg" alt="Kept Story"></div>
<div class="desk">Sports</div>
<h2 class="title">Kept Story</h2>
<p>Body stays.</p>
</article></body></html>
"""
        with open(os.path.join(kept_dir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(kept_html)
        os.makedirs(os.path.join(out, "podcast"))
        with open(os.path.join(out, "podcast", "index.html"), "w", encoding="utf-8") as fh:
            fh.write("PODCAST-KEEP")
        with open(os.path.join(out, "check-inbox.html"), "w", encoding="utf-8") as fh:
            fh.write("CHECK-INBOX")
        with open(os.path.join(out, "subscribed.html"), "w", encoding="utf-8") as fh:
            fh.write("SUBSCRIBED")

        # A previous render of the markdown story has an inline hero and no asset file.
        previous = os.path.join(out, "articles", "2026-01-02", "from-markdown")
        os.makedirs(previous)
        with open(os.path.join(previous, "index.html"), "w", encoding="utf-8") as fh:
            fh.write('<div class="hero"><img src="data:image/jpeg;base64,QUJD" alt="old"></div>')

        site.build(articles, out, assets)
        self.assertEqual(read(os.path.join(out, "podcast", "index.html")), "PODCAST-KEEP")
        self.assertEqual(read(os.path.join(out, "check-inbox.html")), "CHECK-INBOX")
        kept = read(os.path.join(kept_dir, "index.html"))
        self.assertIn("Body stays.", kept)
        index = read(os.path.join(out, "index.html"))
        self.assertIn("Kept Story", index)
        self.assertIn("From markdown", index)
        self.assertIn('src="assets/kept.jpg"', index)
        regenerated = read(os.path.join(previous, "index.html"))
        self.assertIn("data:image/jpeg;base64,QUJD", regenerated)
        self.assertIn("Markdown body.", regenerated)
        sitemap = read(os.path.join(out, "sitemap.xml"))
        self.assertIn("https://kendallwire.com/check-inbox.html", sitemap)
        self.assertIn("https://kendallwire.com/subscribed.html", sitemap)
        self.assertIn("https://kendallwire.com/podcast/", sitemap)
        feed = read(os.path.join(out, "feed.xml"))
        self.assertIn("GMT", feed)
        self.assertNotIn("<pubDate>2026-11-16</pubDate>", feed)

        site.build(articles, out, assets, prune=True)
        self.assertFalse(os.path.exists(kept_dir))
        self.assertTrue(os.path.exists(os.path.join(out, "podcast", "index.html")))
        index = read(os.path.join(out, "index.html"))
        self.assertNotIn("Kept Story", index)

    def test_drafts_are_skipped_and_same_directory_assets_copy(self):
        articles = self.path("articles")
        out = self.path("out")
        os.makedirs(os.path.join(out, "assets"))
        with open(os.path.join(out, "assets", "card.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8\xff")
        self.write_article(articles, "2026-10-02", "draft-story", "Hidden.", status="draft",
                           title="Draft story")
        self.write_article(articles, "2026-10-03", "live-story", "Visible.",
                           title="Live story", image="assets/card.jpg")
        site.build(articles, out, os.path.join(out, "assets"))
        self.assertFalse(os.path.exists(os.path.join(out, "articles", "2026-10-02")))
        page = read(os.path.join(out, "articles", "2026-10-03", "live-story", "index.html"))
        self.assertIn("Visible.", page)
        self.assertIn("../../assets/card.jpg", page)

    def test_summary_block_fills_a_missing_summary(self):
        articles = self.path("articles")
        folder = os.path.join(articles, "2026-10-04")
        os.makedirs(folder)
        with open(os.path.join(folder, "block.md"), "w", encoding="utf-8") as fh:
            fh.write("""---
title: "Block"
date: 2026-10-04
slug: block-story
status: published
summary-for-facebook: |
  First sentence of the blurb. Second sentence.
---

Body.
""")
        loaded, problems = site.load_articles(articles)
        self.assertEqual(problems, [])
        self.assertEqual(loaded[0]["summary"], "First sentence of the blurb. Second sentence.")

    def test_sitemap_escapes_ampersands(self):
        xml = site.render_sitemap(
            [{"date": "2026-01-01", "slug": "a"}],
            ["https://kendallwire.com/check-inbox.html?x=1&y=2"])
        self.assertIn("x=1&amp;y=2", xml)
        self.assertNotIn("x=1&y=2", xml)


class CorpusSmokeTest(SiteTestCase):
    def test_published_corpus_builds_without_leaks(self):
        out = self.path("out")
        articles = os.path.join(ROOT, "articles")
        assets = os.path.join(ROOT, "docs", "assets")
        loaded, problems = site.load_articles(articles)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(loaded), 15)
        built = site.build(articles, out, assets)
        self.assertEqual(len(built), len(loaded))
        for article in built:
            page_path = os.path.join(out, "articles", article["date"], article["slug"], "index.html")
            self.assertTrue(os.path.isfile(page_path), page_path)
            text = read(page_path)
            self.assertNotIn("/home/", text)
            self.assertNotIn("/Users/", text)
            self.assertNotIn("not approved for publication", text.lower())
            self.assertNotIn("javascript:", text.lower())
            self.assertIn(html.escape(article["title"]), text)
        weekend = read(os.path.join(
            out, "articles", "2026-10-19", "weekend-roundup-2026-10-23", "index.html"))
        self.assertIn('href="https://business.boerne.org/events"', weekend)
        burn = read(os.path.join(
            out, "articles", "2026-10-23", "burn-ban-back-on-agenda", "index.html"))
        self.assertNotIn("<h2>Court to weigh 90-day burn ban Tuesday</h2>", burn)
        index = read(os.path.join(out, "index.html"))
        self.assertNotIn("Election 2026", index)
        self.assertIn("Monday, November 16, 2026", index)
        css = read(os.path.join(out, "style.css"))
        self.assertEqual(css, site.CSS)
        self.assertIn(".newsletter-box", css)
        self.assertNotIn("election-box", css)
        feed = read(os.path.join(out, "feed.xml"))
        self.assertIn("GMT", feed)
        self.assertTrue(feed.strip().endswith("</rss>"))
        code = site.main(["--articles", articles, "--out", self.path("out2"), "--assets", assets])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
