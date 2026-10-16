"""The predicate combinators, shared by every surface. A surface supplies the leaf checks; `all`,
`any` and `not` are evaluated here, the same way everywhere."""

from collections.abc import Awaitable, Callable

from eagleview.schema.predicates import AllOf, AnyOf, AnyPredicate, Not

Leaf = Callable[[AnyPredicate], Awaitable[bool]]


async def evaluate(pred: AnyPredicate, leaf: Leaf) -> bool:
    """Short-circuits left to right, so cheap checks written first spare the expensive ones."""
    match pred:
        case AllOf(all=children):
            for child in children:
                if not await evaluate(child, leaf):
                    return False
            return True
        case AnyOf(any=children):
            for child in children:
                if await evaluate(child, leaf):
                    return True
            return False
        case Not(not_=child):
            return not await evaluate(child, leaf)
        case _:
            return await leaf(pred)


def describe(pred: AnyPredicate) -> str:
    """Human-readable form for failure reports: `text_present(main: 'Share Accounts')`."""
    match pred:
        case AllOf(all=children):
            return "all(" + ", ".join(describe(c) for c in children) + ")"
        case AnyOf(any=children):
            return "any(" + ", ".join(describe(c) for c in children) + ")"
        case Not(not_=child):
            return f"not({describe(child)})"
        case _:
            dumped = pred.model_dump(mode="json", by_alias=True)
            ((kind, args),) = dumped.items()
            return f"{kind}({args!r})"
