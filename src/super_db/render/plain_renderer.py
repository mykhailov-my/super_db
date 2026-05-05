import sys


class PlainRenderer:
    def render_message(self, msg: str) -> None:
        print(msg)

    def render_error(self, msg: str) -> None:
        print(f"Error: {msg}", file=sys.stderr)
