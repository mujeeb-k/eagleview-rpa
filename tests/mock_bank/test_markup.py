"""The markup contract. Later phases (locators, app profile, overlays) are written against these
exact strings and selectors, and the app must stay hostile: no ids, no labels, no test hooks."""

import re

import pytest

from tests.support.bank import Bank

PAGES = ["/frames", "/banner", "/nav", "/home", "/inq", "/member", "/share/open"]


def _pages(bank: Bank) -> dict[str, str]:
    with bank.signed_on_client() as c:
        c.post("/inq", data={"txtA": bank.demo, "txtB": ""})
        out = {p: c.get(p).text for p in PAGES}
        out["/signon"] = c.get("/signon").text
        return out


def test_hostile_markup(bank: Bank) -> None:
    for path, html in _pages(bank).items():
        assert not re.search(r"\sid\s*=", html, re.I), path
        assert "data-test" not in html, path
        assert "<label" not in html.lower(), path
        assert "aria-" not in html, path
        if path not in ("/frames",):
            assert "<font" in html, path


def test_frameset_has_three_named_frames(bank: Bank) -> None:
    html = _pages(bank)["/frames"]
    assert re.findall(r'<frame name="(\w+)"', html) == ["banner", "nav", "main"]


def test_footer_carries_the_version(bank: Bank) -> None:
    pages = _pages(bank)
    footer = f"Ironbridge Teller v{bank.tenant.version} — TRAINING REGION — synthetic data"
    assert footer in pages["/signon"]
    assert footer in pages["/inq"]
    assert "WARNING:" in pages["/signon"] and "authorized use only" in pages["/signon"]


def test_lantern_bay_contract(lantern: Bank) -> None:
    pages = _pages(lantern)
    assert '<td class="mnu">' in pages["/nav"]
    assert re.search(r'<a href="/inq" target="main">.*Member Inquiry', pages["/nav"])
    inq = pages["/inq"]
    assert "Member Search" in inq
    # The "Member #" label cell sits directly left of the txtA input cell.
    assert re.search(r"Member #</font></td>\s*<td><input type=\"text\" name=\"txtA\"", inq)
    # The GO button is an image with no alt text and no accessible name.
    go = re.search(r"<img src=\"/img/go.svg\"[^>]*>", inq)
    assert go and "alt=" not in go.group(0)
    member = pages["/member"]
    assert "Name:" in member and "Share Accounts" in member
    header = re.search(r"Share Accounts.*?<tr bgcolor=\"#DDDDDD\">(.*?)</tr>", member, re.S)
    assert header is not None
    cells = re.findall(r"<b>(\w+)</b>", header.group(1))
    assert cells == ["Sfx", "Description", "Nickname", "Balance", "Available"]


def test_copperhill_differs_where_the_overlay_expects(copperhill: Bank) -> None:
    pages = _pages(copperhill)
    assert "Member Lookup" in pages["/nav"] and "Member Inquiry" not in pages["/nav"]
    inq = pages["/inq"]
    assert "Acct Holder No." in inq and "Member #" not in inq
    assert '<input type="submit" value="Search">' in inq and "go.svg" not in inq
    nav_order = re.findall(r"<font color=\"#2F4F2F\">([^<]+)</font>", pages["/nav"])
    assert nav_order[:3] == ["Cash Drawer", "Teller Totals", "Member Lookup"]
    assert "#2F4F2F" in pages["/banner"]


def test_copperhill_branch_interstitial_first_option_is_real(copperhill: Bank) -> None:
    import httpx2 as httpx

    with httpx.Client(base_url=copperhill.url, follow_redirects=True) as c:
        r = c.post("/signon", data={"f1": copperhill.tenant.username, "f2": copperhill.password})
        assert r.url.path == "/branch"
        assert "Select Branch" in r.text
        options = re.findall(r'<option value="([^"]+)">', r.text)
        assert options == copperhill.tenant.branches
        assert 'value="Continue"' in r.text
        # Skipping the interstitial is not possible.
        assert c.get("/inq").url.path == "/branch"


def test_wrong_password_rejected(bank: Bank) -> None:
    import httpx2 as httpx

    with httpx.Client(base_url=bank.url, follow_redirects=True) as c:
        r = c.post("/signon", data={"f1": bank.tenant.username, "f2": "not-the-password"})
        assert "INVALID OPERATOR ID OR PASSWORD" in r.text
        assert "SESSION EXPIRED" in c.get("/home").text


@pytest.mark.parametrize("path", ["/__admin/faults"])
def test_admin_is_not_linked_from_the_ui(lantern: Bank, path: str) -> None:
    assert all(path not in html for html in _pages(lantern).values())


def test_credentials_are_per_tenant(lantern: Bank, copperhill: Bank) -> None:
    import httpx2 as httpx

    assert lantern.password != copperhill.password
    assert lantern.tenant.password_secret != copperhill.tenant.password_secret
    assert lantern.tenant.username != copperhill.tenant.username
    with httpx.Client(base_url=copperhill.url, follow_redirects=True) as c:
        for user, pwd in [
            (lantern.tenant.username, lantern.password),
            (copperhill.tenant.username, lantern.password),
        ]:
            r = c.post("/signon", data={"f1": user, "f2": pwd})
            assert "INVALID OPERATOR ID OR PASSWORD" in r.text
