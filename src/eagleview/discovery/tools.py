"""The tools discovery offers: the computer toolset, minus what the policy denies, plus four
custom tools that make the recording meaningful.

    fill_input(param)              type a declared input at the focus, so the recorder binds it
    mark_output(name, kind, ...)   point at a value or a table the goal asks for
    declare_success(...)           point at what shows the goal is done
    request_human(reason)          ask a person (P5)
"""

from typing import Any

from eagleview.schema.action import Action

TOOLSET_NAME = "computer"
HALT_TEXT = "Not executed: an earlier computer action in this turn failed."

# Members the policy refuses or replay cannot express; the model is not offered them.
DISABLED_MEMBERS = (
    "right_click",
    "middle_click",
    "triple_click",
    "left_click_drag",
    "left_mouse_down",
    "left_mouse_up",
    "hold_key",
    "cursor_position",
)
TOOLSET: dict[str, Any] = {
    "type": "computer_toolset_20260801",
    "configs": {m: {"enabled": False} for m in DISABLED_MEMBERS},
}

# Members that only look. They never count toward the action limit or the progress rule.
LOOKING = frozenset({"screenshot", "zoom", "wait"})
SCROLL_PX = 100  # one wheel click


def custom_tools(input_names: list[str]) -> list[dict[str, Any]]:
    coordinate = {
        "type": "array",
        "items": {"type": "integer"},
        "description": "[x, y] in screenshot pixels",
    }
    fill: dict[str, Any] = {
        "type": "object",
        "properties": {"param": {"type": "string", "enum": input_names or ["(none)"]}},
        "required": ["param"],
        "additionalProperties": False,
    }
    return [
        {
            "name": "fill_input",
            "description": (
                "Type the value of a declared goal input at the current keyboard focus. Click the "
                "field first. Use this, never `type`, for every value the goal supplies."
            ),
            "strict": True,
            "input_schema": fill,
        },
        {
            "name": "mark_output",
            "description": (
                "Mark a value the goal asks you to read. kind=value for one value (point at the "
                "value itself, not its label); kind=table for a table of rows (point at any body "
                "cell). name is snake_case for what the value is, e.g. member_name, share_accounts."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["value", "table"]},
                    "coordinate": coordinate,
                    "description": {"type": "string"},
                },
                "required": ["name", "kind", "coordinate", "description"],
                "additionalProperties": False,
            },
        },
        {
            "name": "declare_success",
            "description": (
                "Call once the goal is complete and every output is marked. Point at on-screen "
                "text that shows the goal is done, such as a heading or a confirmation."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"coordinate": coordinate, "description": {"type": "string"}},
                "required": ["coordinate", "description"],
                "additionalProperties": False,
            },
        },
        {
            "name": "request_human",
            "description": (
                "Ask a person to take over when you are stuck, when the screen shows something "
                "you cannot safely handle, or when the next step needs an approval."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    ]


def _point(value: Any) -> tuple[int, int] | None:
    if isinstance(value, list | tuple) and len(value) == 2:  # pyright: ignore[reportUnknownArgumentType]
        x, y = value  # pyright: ignore[reportUnknownVariableType]
        if isinstance(x, int) and isinstance(y, int) and x >= 0 and y >= 0:
            return x, y
    return None


def member_actions(name: str, data: dict[str, Any]) -> list[Action] | str:
    """The Surface actions for one computer member, or why it cannot run."""
    if data.get("text") and name in ("left_click", "double_click", "scroll"):
        return f"{name} with modifier keys is not supported here"
    match name:
        case "screenshot":
            return [Action(type="screenshot")]
        case "zoom":
            region = data.get("region")
            if not (isinstance(region, list) and len(region) == 4):  # pyright: ignore[reportUnknownArgumentType]
                return "zoom needs region [x0, y0, x1, y1]"
            x0, y0, x1, y1 = (int(v) for v in region)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            return [Action(type="zoom", region=(x0, y0, x1, y1))]
        case "wait":
            seconds = float(data.get("duration", 1))
            return [Action(type="wait", duration_ms=int(max(0.0, seconds) * 1000))]
        case "left_click" | "double_click" | "mouse_move":
            p = _point(data.get("coordinate"))
            if p is None:
                return f"{name} needs a coordinate [x, y]"
            kind = {"left_click": "click", "double_click": "double_click", "mouse_move": "move"}[
                name
            ]
            return [Action(type=kind, x=p[0], y=p[1])]  # type: ignore[arg-type]
        case "scroll":
            p = _point(data.get("coordinate")) or (640, 400)
            amount = int(data.get("scroll_amount", 3)) * SCROLL_PX
            dx, dy = {
                "up": (0, -amount),
                "down": (0, amount),
                "left": (-amount, 0),
                "right": (amount, 0),
            }.get(str(data.get("scroll_direction")), (0, amount))
            return [Action(type="scroll", x=p[0], y=p[1], scroll=(dx, dy))]
        case "type":
            return [Action(type="type", text=str(data.get("text", "")))]
        case "key":
            repeat = max(1, min(int(data.get("repeat", 1)), 100))
            return [Action(type="key", key=str(data.get("text", "")))] * repeat
        case _:
            return f"{name} is not available"
