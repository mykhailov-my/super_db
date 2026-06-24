from pathlib import Path

from superdb.render.plain import PlainRenderer
from superdb.render.protocol import Renderer

# The only module permitted to import rich — the renderer firewall seam.
RICH_ALLOWED = {"rich.py"}


def test_rich_not_imported_outside_render():
    repo_root = Path(__file__).parent.parent
    src = repo_root / "src" / "superdb"
    scripts = repo_root / "scripts"

    scan_roots = [src]
    if scripts.exists():
        scan_roots.append(scripts)

    files: list[Path] = []
    for root in scan_roots:
        files.extend(root.rglob("*.py"))

    assert files, f"firewall scan found no files under {scan_roots}"
    leaks = []
    for f in files:
        if f.name in RICH_ALLOWED:
            continue
        for line in f.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("from rich", "import rich")):
                leaks.append(f"{f}: {stripped}")
    assert not leaks, "rich imported outside render/rich.py:\n" + "\n".join(leaks)


def test_plain_renderer_satisfies_protocol():
    r: Renderer = PlainRenderer()
    assert callable(r.render_message)
    assert callable(r.render_error)
