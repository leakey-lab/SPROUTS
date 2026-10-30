"""PV-06 / AR-15: the dashboards must render fully offline.

Every stylesheet, font and script the served page references must come from the
local Dash server: no CDN (jsDelivr) and no Google Fonts, neither in the index
HTML nor hidden inside a served stylesheet (``@import`` / ``url(...)``).
"""

from __future__ import annotations

import re

import pytest

from tests.viz_dash_helpers import build_area_app, build_length_app

_REMOTE = re.compile(r"""https?://|//[a-z0-9.-]+\.[a-z]{2,}/""", re.IGNORECASE)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""")
_CSS_IMPORT = re.compile(r"""@import\s+(?:url\()?\s*['"]?([^'");\s]+)""")


@pytest.fixture(params=["length", "area"])
def dashboard(request, tmp_path):
    builder = build_length_app if request.param == "length" else build_area_app
    return builder(tmp_path)


def _index_html(dash) -> str:
    resp = dash.server.test_client().get("/")
    assert resp.status_code == 200
    return resp.data.decode("utf-8")


def _stylesheet_urls(html: str) -> list[str]:
    urls = []
    for tag in re.findall(r"<link[^>]*>", html):
        if 'rel="stylesheet"' in tag:
            urls.append(re.search(r'href="([^"]+)"', tag).group(1))
    return urls


def test_index_references_no_remote_host(dashboard):
    html = _index_html(dashboard.app)
    remote_refs = [
        ref for ref in re.findall(r"""(?:href|src)="([^"]+)\"""", html) if _REMOTE.match(ref)
    ]
    assert remote_refs == []


def _local_css(client, url: str) -> str:
    resp = client.get(url)
    assert resp.status_code == 200 and resp.mimetype == "text/css", url
    return resp.data.decode("utf-8")


def test_served_stylesheets_pull_nothing_remote(dashboard):
    client = dashboard.app.server.test_client()
    css_urls = [u for u in _stylesheet_urls(_index_html(dashboard.app)) if not _REMOTE.match(u)]
    assert css_urls, "expected the page to link local stylesheets"
    for url in css_urls:
        css = _CSS_COMMENT.sub("", _local_css(client, url))
        imports = _CSS_IMPORT.findall(css)
        remote = [u for u in _CSS_URL.findall(css) + imports if _REMOTE.match(u.strip())]
        assert remote == [], f"{url} pulls remote resources: {remote[:3]}"


def test_bootstrap_rules_used_by_the_layout_are_served_locally(dashboard):
    """The view selector (btn-check / btn-group) and the control strip (collapse)
    rely on Bootstrap rules; offline they must still be defined."""
    client = dashboard.app.server.test_client()
    css = "".join(
        _local_css(client, u)
        for u in _stylesheet_urls(_index_html(dashboard.app))
        if not _REMOTE.match(u)
    )
    for selector in (".btn-check", ".btn-group", ".collapse:not(.show)"):
        assert selector in css, f"no local rule for {selector}"


def test_font_faces_resolve_to_served_woff2(dashboard):
    client = dashboard.app.server.test_client()
    families = set()
    for url in _stylesheet_urls(_index_html(dashboard.app)):
        if _REMOTE.match(url):
            continue
        css = _CSS_COMMENT.sub("", _local_css(client, url))
        for face in re.findall(r"@font-face\s*{([^}]*)}", css):
            families.add(re.search(r"font-family:\s*['\"]?([^;'\"]+)", face).group(1))
            for src in _CSS_URL.findall(face):
                base = url.split("?")[0].rsplit("/", 1)[0]
                resp = client.get(f"{base}/{src}")
                assert resp.status_code == 200, f"{src} -> {resp.status_code}"
                assert resp.data[:4] == b"wOF2", f"{src} is not a woff2 font"
    assert {"IBM Plex Sans", "IBM Plex Mono"} <= families
