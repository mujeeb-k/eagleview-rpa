"""Inlining dom.js into evaluate calls. Each call carries the whole library, so nothing is left on
the page (no globals for the page to see or overwrite) and every frame gets the same code."""

from functools import cache
from importlib.resources import files


@cache
def library() -> str:
    return files("eagleview.surfaces.web").joinpath("dom.js").read_text(encoding="utf-8")


def page_fn(body: str) -> str:
    """For `frame.evaluate(...)`: `body` sees `EV` (the library), `doc` and `arg`."""
    return f"(arg) => {{ const EV = {library()}; const doc = document; {body} }}"


def element_fn(body: str) -> str:
    """For `handle.evaluate(...)`: `body` sees `EV`, `el` and `arg`."""
    return f"(el, arg) => {{ const EV = {library()}; {body} }}"


def cdp_fn(body: str) -> str:
    """For CDP `Runtime.callFunctionOn`: `body` sees `EV` and `el` (the object called on)."""
    return f"function(arg) {{ const EV = {library()}; const el = this; {body} }}"
