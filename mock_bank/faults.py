"""Fault injection through `/__admin/faults`. Harness only; Eagleview's policy denies the route.

Each fault is a `key=value` spec, matching PLAN.md:

    session_expire_after_requests=N   serve N more authenticated page requests, then expire the
                                      session that makes the next one (one-shot)
    session_expire_on=<route>         expire the session that next requests <route> (one-shot), so
                                      a test can expire it right after a given screen, whatever
                                      the request count
    system_notice_on=<route>          a known "System Notice" modal on every response for <route>
    unknown_modal_on=<route>          a modal no handler knows, on every response for <route>
    slow_ms=<route>:<ms>              delay every response for <route>
    error500_on=<route>[:once]        a 500 SYSTEM ERROR page for <route>, optionally only once
    role=inquiry_only|teller          override the role of every signed-on operator
    maintenance=on|off                every page returns SYSTEM UNAVAILABLE (503)

A route is an exact URL path such as `/member`, matched for any method.
"""

from dataclasses import dataclass
from typing import Literal

Role = Literal["teller", "inquiry_only"]
ModalKind = Literal["system_notice", "unknown"]


class FaultSpecError(ValueError):
    pass


def _route(value: str) -> str:
    if not value.startswith("/"):
        raise FaultSpecError(f"route must start with '/': {value!r}")
    return value


def _split(value: str) -> tuple[str, str | None]:
    route, sep, rest = value.rpartition(":")
    return (route, rest) if sep else (value, None)


@dataclass
class Faults:
    expire_after: int | None = None
    expire_on: str | None = None
    system_notice_on: str | None = None
    unknown_modal_on: str | None = None
    slow: tuple[str, int] | None = None
    error500: tuple[str, bool] | None = None
    role: Role | None = None
    maintenance: bool = False

    def apply(self, specs: dict[str, str]) -> None:
        """Validate every spec first, then apply them all, so a bad spec changes nothing."""
        staged = Faults(**vars(self))
        for key, value in specs.items():
            staged._set(key, str(value))
        vars(self).update(vars(staged))

    def _set(self, key: str, value: str) -> None:
        match key:
            case "session_expire_after_requests":
                if not value.isdigit():
                    raise FaultSpecError(f"{key} needs a non-negative integer: {value!r}")
                self.expire_after = int(value)
            case "session_expire_on":
                self.expire_on = _route(value)
            case "system_notice_on":
                self.system_notice_on = _route(value)
            case "unknown_modal_on":
                self.unknown_modal_on = _route(value)
            case "slow_ms":
                route, ms = _split(value)
                if ms is None or not ms.isdigit():
                    raise FaultSpecError(f"slow_ms needs <route>:<ms>: {value!r}")
                self.slow = (_route(route), int(ms))
            case "error500_on":
                route, flag = _split(value)
                if flag not in (None, "once"):
                    raise FaultSpecError(f"error500_on suffix must be ':once': {value!r}")
                self.error500 = (_route(route), flag == "once")
            case "role":
                if value not in ("teller", "inquiry_only"):
                    raise FaultSpecError(f"role must be teller or inquiry_only: {value!r}")
                self.role = value
            case "maintenance":
                if value not in ("on", "off"):
                    raise FaultSpecError(f"maintenance must be on or off: {value!r}")
                self.maintenance = value == "on"
            case _:
                raise FaultSpecError(f"unknown fault: {key!r}")

    def describe(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.expire_after is not None:
            out["session_expire_after_requests"] = str(self.expire_after)
        if self.expire_on:
            out["session_expire_on"] = self.expire_on
        if self.system_notice_on:
            out["system_notice_on"] = self.system_notice_on
        if self.unknown_modal_on:
            out["unknown_modal_on"] = self.unknown_modal_on
        if self.slow:
            out["slow_ms"] = f"{self.slow[0]}:{self.slow[1]}"
        if self.error500:
            out["error500_on"] = self.error500[0] + (":once" if self.error500[1] else "")
        if self.role:
            out["role"] = self.role
        if self.maintenance:
            out["maintenance"] = "on"
        return out

    def take_expiry(self) -> bool:
        """Count one authenticated request. True means this request's session must expire."""
        if self.expire_after is None:
            return False
        if self.expire_after == 0:
            self.expire_after = None
            return True
        self.expire_after -= 1
        return False

    def take_expire_on(self, path: str) -> bool:
        """True once: this request for the armed route must find its session expired."""
        if self.expire_on is None or self.expire_on != path:
            return False
        self.expire_on = None
        return True

    def take_error500(self, path: str) -> bool:
        if self.error500 is None or self.error500[0] != path:
            return False
        if self.error500[1]:
            self.error500 = None
        return True

    def slow_ms(self, path: str) -> int:
        return self.slow[1] if self.slow and self.slow[0] == path else 0

    def modal_for(self, path: str) -> ModalKind | None:
        if self.unknown_modal_on == path:
            return "unknown"
        if self.system_notice_on == path:
            return "system_notice"
        return None
