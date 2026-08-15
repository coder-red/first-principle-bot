"""Helpers shared by the browser suites.

run_all.py runs each suite as a script from this directory, so the suites
import this as a plain sibling. conftest.py already tells pytest to ignore
every .py here, so this file is never collected as a test.
"""

GOOGLE_FONTS_CSS = "https://fonts.googleapis.com/**"
GOOGLE_FONTS_FILES = "https://fonts.gstatic.com/**"


def stub_web_fonts(page):
    """Answer the Google Fonts requests locally.

    index.html pulls Inter and JetBrains Mono from Google. The suites count a
    console error as a JS error, and a failed subresource logs one — so an
    intermittent 404 from fonts.gstatic.com failed runs that had nothing wrong
    with them. It fired on roughly one run in ten, on whichever suite happened
    to be holding the page when Google hiccuped, which read as a real
    regression every time.

    Fulfilling the stylesheet empty means no @font-face rule ever exists, so
    no font file is requested at all and the page renders in the fallback
    stack. The gstatic route is a belt to that brace.

    This is the rule the suites already follow everywhere else: nothing
    outside this repo gets to decide whether they pass. They stub every
    endpoint that would cost a model call for the same reason.
    """
    page.route(
        GOOGLE_FONTS_CSS,
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    page.route(
        GOOGLE_FONTS_FILES,
        lambda route: route.fulfill(status=200, content_type="font/woff2", body=""),
    )
