"""The Ironbridge Teller web app: one FastAPI instance per tenant, with all state in memory."""

import asyncio
import copy
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from mock_bank.config import ShareKind, Tenant
from mock_bank.faults import Faults, FaultSpecError
from mock_bank.seed import BUSINESS_DATE, DIVIDEND_FREQUENCIES, Member, Share, generate

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
IDLE_TIMEOUT_S = 15 * 60
NICKNAME_MAX = 20

# Paths that are never faulted or counted: the harness's admin API and static images.
UNFAULTED = ("/__admin", "/img/")


def money(value: Decimal) -> str:
    """Legacy style: a negative balance carries a trailing minus, e.g. $12.34-."""
    return f"${value:,.2f}" if value >= 0 else f"${-value:,.2f}-"


def amount(value: Decimal) -> str:
    """Legacy ledger style: debits carry a trailing minus, e.g. 45.00-."""
    return f"{value:,.2f}" if value >= 0 else f"{-value:,.2f}-"


TEMPLATES.env.filters["money"] = money
TEMPLATES.env.filters["amount"] = amount
TEMPLATES.env.filters["mdy"] = lambda d: d.strftime("%m/%d/%Y")


@dataclass
class Draft:
    product: str
    nickname: str
    frequency: str


@dataclass
class Posted:
    txn: str
    member: str
    share: Share


@dataclass
class Session:
    token: str
    username: str
    role: str
    last_seen: float
    branch: str | None = None
    member: str | None = None
    draft: Draft | None = None
    posted: Posted | None = None


@dataclass
class Bank:
    """All mutable state for one tenant. `reset()` restores the seeded starting point."""

    tenant: Tenant
    password: str
    pristine: dict[str, Member] = field(init=False)
    members: dict[str, Member] = field(init=False)
    sessions: dict[str, Session] = field(init=False)
    faults: Faults = field(init=False)
    next_txn: int = field(init=False)
    next_error_ref: int = field(init=False)

    def __post_init__(self) -> None:
        self.pristine = generate(self.tenant)
        self.reset()

    def reset(self) -> None:
        self.members = copy.deepcopy(self.pristine)
        self.sessions = {}
        self.faults = Faults()
        self.next_txn = self.tenant.txn_base
        self.next_error_ref = 1


class SessionExpired(Exception):
    pass


class BranchRequired(Exception):
    pass


async def read_form(request: Request) -> dict[str, str]:
    # Parsed by hand: browsers post application/x-www-form-urlencoded, and this avoids a dependency.
    parsed = parse_qs((await request.body()).decode(), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


def create_app(tenant: Tenant, password: str) -> FastAPI:
    bank = Bank(tenant, password)
    app = FastAPI(title=f"Ironbridge Teller ({tenant.slug})", docs_url=None, redoc_url=None)
    app.state.bank = bank

    def render(request: Request, template: str, status: int = 200, **ctx: Any) -> HTMLResponse:
        context = {
            "t": tenant,
            "modal": bank.faults.modal_for(request.url.path),
            "business_date": BUSINESS_DATE,
            **ctx,
        }
        return TEMPLATES.TemplateResponse(request, template, context, status_code=status)

    def session(request: Request, *, need_branch: bool = True) -> Session:
        """The caller's session, or SessionExpired. Each call is one authenticated request."""
        s = bank.sessions.get(request.cookies.get(tenant.cookie_name, ""))
        if s is None:
            raise SessionExpired
        now = time.monotonic()
        if now - s.last_seen > IDLE_TIMEOUT_S or bank.faults.take_expiry():
            del bank.sessions[s.token]
            raise SessionExpired
        s.last_seen = now
        if need_branch and tenant.select_branch and s.branch is None:
            raise BranchRequired
        return s

    def role(s: Session) -> str:
        return bank.faults.role or s.role

    def denied(request: Request, function: str) -> HTMLResponse:
        return render(request, "denied.html", function=function)

    def current_member(s: Session) -> Member | None:
        return bank.members.get(s.member) if s.member else None

    @app.middleware("http")
    async def inject_faults(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path.startswith(UNFAULTED):
            return await call_next(request)
        if ms := bank.faults.slow_ms(path):
            await asyncio.sleep(ms / 1000)
        if bank.faults.maintenance:
            return render(request, "maintenance.html", status=503)
        if bank.faults.take_error500(path):
            ref = f"IBT-0500-{bank.next_error_ref:05d}"
            bank.next_error_ref += 1
            return render(request, "error.html", status=500, ref=ref)
        return await call_next(request)

    @app.exception_handler(SessionExpired)
    async def on_expired(request: Request, exc: SessionExpired) -> HTMLResponse:
        return render(request, "expired.html")

    @app.exception_handler(BranchRequired)
    async def on_branch(request: Request, exc: BranchRequired) -> RedirectResponse:
        return RedirectResponse("/branch", status_code=303)

    # ---- sign-on ---------------------------------------------------------------------------

    @app.get("/")
    async def root(request: Request) -> RedirectResponse:
        signed_on = request.cookies.get(tenant.cookie_name, "") in bank.sessions
        return RedirectResponse("/frames" if signed_on else "/signon", status_code=303)

    @app.get("/signon")
    async def signon_page(request: Request) -> HTMLResponse:
        return render(request, "signon.html", error=None)

    @app.post("/signon", response_model=None)
    async def signon(request: Request) -> Response:
        form = await read_form(request)
        user, pwd = form.get("f1", ""), form.get("f2", "")
        ok = secrets.compare_digest(user, tenant.username) and secrets.compare_digest(
            pwd.encode(), bank.password.encode()
        )
        if not ok:
            return render(request, "signon.html", error="INVALID OPERATOR ID OR PASSWORD")
        token = secrets.token_hex(16)
        bank.sessions[token] = Session(token, user, "teller", time.monotonic())
        target = "/branch" if tenant.select_branch else "/frames"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(tenant.cookie_name, token, httponly=True, samesite="lax")
        return response

    @app.get("/branch")
    async def branch_page(request: Request) -> HTMLResponse:
        session(request, need_branch=False)
        return render(request, "branch.html", error=None)

    @app.post("/branch", response_model=None)
    async def branch(request: Request) -> Response:
        s = session(request, need_branch=False)
        choice = (await read_form(request)).get("br", "")
        if choice not in tenant.branches:
            return render(request, "branch.html", error="IBT-0120 SELECT A BRANCH")
        s.branch = choice
        return RedirectResponse("/frames", status_code=303)

    @app.get("/signoff")
    async def signoff(request: Request) -> RedirectResponse:
        bank.sessions.pop(request.cookies.get(tenant.cookie_name, ""), None)
        response = RedirectResponse("/signon", status_code=303)
        response.delete_cookie(tenant.cookie_name)
        return response

    # ---- frameset --------------------------------------------------------------------------

    @app.get("/frames")
    async def frames(request: Request) -> HTMLResponse:
        session(request)
        return render(request, "frames.html")

    @app.get("/banner")
    async def banner(request: Request) -> HTMLResponse:
        s = session(request)
        branch = s.branch or tenant.branches[0]
        return render(request, "banner.html", s=s, branch=branch, idle_s=IDLE_TIMEOUT_S)

    @app.get("/nav")
    async def nav(request: Request) -> HTMLResponse:
        session(request)
        return render(request, "nav.html")

    @app.get("/home")
    async def home(request: Request) -> HTMLResponse:
        s = session(request)
        return render(request, "home.html", s=s)

    @app.get("/na")
    async def not_available(request: Request, f: str = "") -> HTMLResponse:
        session(request)
        return render(request, "na.html", function=f[:8].upper())

    # ---- member inquiry --------------------------------------------------------------------

    @app.get("/inq")
    async def inquiry_page(request: Request) -> HTMLResponse:
        session(request)
        return render(request, "inquiry.html", error=None, txt_a="", txt_b="")

    @app.post("/inq", response_model=None)
    async def inquiry(request: Request) -> Response:
        s = session(request)
        form = await read_form(request)
        number, last = form.get("txtA", "").strip(), form.get("txtB", "").strip()

        def again(error: str) -> HTMLResponse:
            return render(request, "inquiry.html", error=error, txt_a=number, txt_b=last)

        if not number and last:
            return again("IBT-1003 NAME SEARCH IS NOT ENABLED IN THIS REGION")
        if not number:
            return again("IBT-1001 ENTER A MEMBER NUMBER")
        if not number.isdigit():
            return again("IBT-1002 INVALID MEMBER NUMBER")
        if number not in bank.members:
            return again(f"IBT-1004 NO MEMBER FOUND FOR NUMBER {number}")
        s.member = number
        return RedirectResponse("/member", status_code=303)

    @app.get("/member", response_model=None)
    async def member_detail(request: Request) -> Response:
        s = session(request)
        m = current_member(s)
        if m is None:
            return RedirectResponse("/inq", status_code=303)
        return render(request, "member.html", m=m, activity=m.txns[:10])

    # ---- open share ------------------------------------------------------------------------

    def validate(m: Member, form: dict[str, str]) -> list[str]:
        errors: list[str] = []
        kind = tenant.products.get(form.get("f3", ""))
        if kind is None:
            errors.append("IBT-2201 SHARE TYPE IS REQUIRED")
        elif kind == "club" and any(sh.kind == "club" for sh in m.shares):
            club = tenant.share_types["club"].description.upper()
            errors.append(f"IBT-2210 MEMBER ALREADY HAS A {club} SHARE")
        elif next_suffix(tenant, m, kind) is None:
            errors.append("IBT-2215 NO SUFFIX AVAILABLE FOR THIS SHARE TYPE")
        if len(form.get("f4", "")) > NICKNAME_MAX:
            errors.append(f"IBT-2214 NICKNAME EXCEEDS {NICKNAME_MAX} CHARACTERS")
        if form.get("f5", "") not in DIVIDEND_FREQUENCIES:
            errors.append("IBT-2203 DIVIDEND FREQUENCY IS REQUIRED")
        return errors

    def open_form(request: Request, m: Member, form: dict[str, str], errors: list[str]):
        return render(
            request,
            "open.html",
            m=m,
            form=form,
            errors=errors,
            products={
                code: tenant.share_types[k].description for code, k in tenant.products.items()
            },
            frequencies=DIVIDEND_FREQUENCIES,
        )

    @app.get("/share/open", response_model=None)
    async def open_page(request: Request) -> Response:
        s = session(request)
        if role(s) != "teller":
            return denied(request, "SHROPN")
        m = current_member(s)
        if m is None:
            return RedirectResponse("/inq", status_code=303)
        return open_form(request, m, {"f5": "Monthly"}, [])

    @app.post("/share/open", response_model=None)
    async def open_validate(request: Request) -> Response:
        s = session(request)
        if role(s) != "teller":
            return denied(request, "SHROPN")
        m = current_member(s)
        if m is None:
            return RedirectResponse("/inq", status_code=303)
        form = await read_form(request)
        if errors := validate(m, form):
            return open_form(request, m, form, errors)
        s.draft = Draft(form["f3"], form.get("f4", "").strip(), form["f5"])
        return RedirectResponse("/share/review", status_code=303)

    @app.get("/share/review", response_model=None)
    async def review(request: Request) -> Response:
        s = session(request)
        m = current_member(s)
        if m is None or s.draft is None:
            return RedirectResponse("/inq", status_code=303)
        kind = tenant.products[s.draft.product]
        product = tenant.share_types[kind].description
        suffix = next_suffix(tenant, m, kind)
        return render(request, "review.html", m=m, d=s.draft, product=product, suffix=suffix)

    @app.post("/share/post", response_model=None)
    async def post(request: Request) -> Response:
        # Irreversible. Deliberately like many legacy cores: the review form carries the whole
        # transaction in hidden fields and there is no duplicate-submission token, so posting
        # twice opens two shares. Only the client can prevent a double post.
        s = session(request)
        if role(s) != "teller":
            return denied(request, "SHRPST")
        m = current_member(s)
        form = await read_form(request)
        kind = tenant.products.get(form.get("f3", ""))
        suffix = next_suffix(tenant, m, kind) if m is not None and kind is not None else None
        if m is None or kind is None or suffix is None:
            ref = f"IBT-0500-{bank.next_error_ref:05d}"
            bank.next_error_ref += 1
            return render(request, "error.html", status=500, ref=ref)
        share = Share(
            kind=kind,
            suffix=suffix,
            description=tenant.share_types[kind].description,
            balance=Decimal("0.00"),
            available=Decimal("0.00"),
            opened=BUSINESS_DATE,
            nickname=form.get("f4", "")[:NICKNAME_MAX],
            dividend_frequency=form.get("f5", "Monthly"),
        )
        m.shares.append(share)
        m.shares.sort(key=lambda sh: sh.suffix)
        s.posted = Posted(str(bank.next_txn), m.number, share)
        bank.next_txn += 1
        return RedirectResponse("/share/posted", status_code=303)

    @app.get("/share/posted", response_model=None)
    async def posted(request: Request) -> Response:
        s = session(request)
        if s.posted is None:
            return RedirectResponse("/inq", status_code=303)
        m = bank.members[s.posted.member]
        return render(request, "posted.html", m=m, p=s.posted)

    # ---- images ----------------------------------------------------------------------------

    @app.get("/img/go.svg")
    async def go_image() -> Response:
        return Response(GO_SVG, media_type="image/svg+xml")

    @app.get("/img/logo.svg")
    async def logo_image() -> Response:
        theme = tenant.theme
        svg = LOGO_SVG.format(bg=theme.banner_bg, fg=theme.banner_fg, name=tenant.short_name)
        return Response(svg, media_type="image/svg+xml")

    # ---- harness admin (never linked from the UI; Eagleview's policy denies it) ------------

    @app.get("/__admin/faults")
    async def get_faults() -> dict[str, dict[str, str]]:
        return {"faults": bank.faults.describe()}

    @app.post("/__admin/faults", response_model=None)
    async def set_faults(request: Request) -> Response:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        try:
            bank.faults.apply({str(k): str(v) for k, v in body.items()})  # type: ignore[union-attr]
        except FaultSpecError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"faults": bank.faults.describe()})

    @app.delete("/__admin/faults")
    async def clear_faults() -> dict[str, dict[str, str]]:
        bank.faults = Faults()
        return {"faults": {}}

    @app.post("/__admin/reset")
    async def reset() -> dict[str, str]:
        bank.reset()
        return {"status": "reset"}

    return app


def next_suffix(tenant: Tenant, m: Member, kind: ShareKind) -> str | None:
    share_type = tenant.share_types[kind]
    taken = {sh.suffix for sh in m.shares}
    free = (tenant.suffix(n) for n in range(share_type.low, share_type.high + 1))
    return next((sfx for sfx in free if sfx not in taken), None)


GO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20">
<rect x="0.5" y="0.5" width="35" height="19" fill="#2E7D32" stroke="#1B5E20"/>
<text x="18" y="14" font-family="Arial" font-size="11" font-weight="bold" fill="#FFFFFF"
 text-anchor="middle">GO</text></svg>"""

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="190" height="40">
<rect width="190" height="40" fill="{bg}"/>
<circle cx="20" cy="20" r="13" fill="none" stroke="{fg}" stroke-width="3"/>
<path d="M13 25 L20 11 L27 25 Z" fill="{fg}"/>
<text x="40" y="25" font-family="Georgia" font-size="13" font-weight="bold"
 fill="{fg}">{name}</text>
</svg>"""
