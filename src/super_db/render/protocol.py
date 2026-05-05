from typing import Protocol


class Renderer(Protocol):
    def render_message(self, msg: str) -> None: ...
    def render_error(self, msg: str) -> None: ...
    # Phase 8 extends this with render_schema / render_page / render_hexdump / render_btree.
