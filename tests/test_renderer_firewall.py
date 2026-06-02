from pathlib import Path

from super_db.render.plain_renderer import PlainRenderer
from super_db.render.protocol import Renderer


def test_rich_not_imported_outside_render():
    repo_root = Path(__file__).parent.parent
    src = repo_root / "src" / "super_db"
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
        if "render" in f.parts:
            continue
        for line in f.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("from rich", "import rich")):
                leaks.append(f"{f}: {stripped}")
    assert not leaks, "rich imported outside render/:\n" + "\n".join(leaks)


def test_plain_renderer_satisfies_protocol():
    r: Renderer = PlainRenderer()
    assert callable(r.render_message)
    assert callable(r.render_error)
