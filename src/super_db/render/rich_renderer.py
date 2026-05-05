from rich.console import Console


class RichRenderer:
    def __init__(self) -> None:
        self._console = Console()

    def render_message(self, msg: str) -> None:
        self._console.print(msg)

    def render_error(self, msg: str) -> None:
        self._console.print(f"[red]Error:[/red] {msg}", stderr=True)
