<!-- GSD:project-start source:PROJECT.md -->
## Project

**super_db — Storage Layer**

An educational, single-node DBMS built from scratch in Python to learn database internals (CMU 15-445 style). This milestone delivers the **complete storage layer**: a persistent catalog, a slotted-page row-store heap engine with durable writes, sequential scan, mutations, and a B+Tree index — plus a graphical CLI that visualizes the on-disk structures (pages, schema, hex dumps, the B+Tree). Later course milestones (SQL parser, query planner, execution engine) build on top of this storage API.

**Core Value:** A correct, restart-safe row-store: create a table, insert/read/scan/update/delete records, restart the process, and get exactly the same data back — with code simple enough to read top to bottom and understand how a database stores bytes on disk.

### Constraints

- **Tech stack**: Python ^3.12, Poetry for packaging/deps.
- **Dependency rule (governing)**: helper libraries allowed; libraries that double as core DB functionality are banned. Hand-write storage/encoding/durability/indexing with stdlib (`struct`, `os`, `json`). Allowed helper runtime deps: **Rich** (CLI presentation, behind a swappable renderer) and **loguru** (logging). Dev deps: pytest, ruff.
- **Simplicity (hard)**: "As simple as possible." Prefer the simplest correct design; KISS/DRY/YAGNI. Many small, cohesive modules over large ones. No speculative abstraction.
- **Modularity (hard)**: Catalog, low-level storage manager, and CLI/visualization are cleanly separated. Rendering library must be swappable without touching storage code.
- **Durability**: Write-through + fsync on every mutating operation; data must survive process restart.
- **Testing**: Well-tested. pytest suite covering all key aspects, run by one documented command.
- **Storage track**: Row-store, slotted-page heap files, `RID = (page_id, slot_id)`. Recorded in metadata; other tracks intentionally not implemented.
- **Commit history**: Backdated and spread believably from ~30 days ago to today (≈2026-05-06 → 2026-06-05), natural human cadence (no commits at odd hours, slight clustering/gaps), earlier phases dated earlier.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Summary
## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | ^3.12 (3.12.3 local) | Language/runtime | Project constraint. 3.12 gives mature `struct`, `os.p{read,write}`, `dataclasses(slots=True)`, modern type syntax. No need for 3.13-only features. |
| CPython stdlib | (bundled) | Entire storage + catalog core | Hard constraint: core is stdlib-only. The stdlib covers every storage concern (see table below). Zero supply-chain surface, zero version drift, reads top-to-bottom. |
| Rich | 15.x (15.0.0, Apr 2026) | CLI visualization ONLY | Best-in-class terminal `Table`/`Tree`/`Panel`/`Syntax` rendering. Pure-Python, MIT, requires Python >=3.9. Isolated behind a renderer Protocol so it is swappable and never imported by the engine. |
| Poetry | 2.x (2.4.1, May 2026) | Packaging + dependency mgmt + console-script entry | Project constraint. 2.x uses the PEP 621 `[project]` table, reproducible installs via `poetry.lock`, dependency groups for dev tooling. |
### stdlib modules for the storage core
| Concern | stdlib module / function | Why this, not an alternative |
|---------|--------------------------|------------------------------|
| Fixed-length encode/decode (INT = int32) | `struct.pack('<i', x)` / `struct.unpack('<i', b)` | `struct` is the canonical binary codec. Explicit `<` (little-endian, **standard size, no alignment padding**) is mandatory — never use native `@`/`=` formats which add platform alignment. Verified: `<i` → 4 bytes, round-trips. |
| Page-level integers (page_id, slot offsets, lengths) | `int.to_bytes(n, 'little')` / `int.from_bytes(b, 'little')` | Clearest for single scalars; no format-string indirection. Use `struct` when packing several fields at once, `to_bytes` for one-off length/offset words. |
| Null bitmap | one or more bytes built via `int.to_bytes`; test bits with `&`/`|`/`<<` | A null bitmap is just packed bits; stdlib int bit-ops are exactly right. Verified `(0b101).to_bytes(1,'little')`. No `bitarray` dependency needed for ≤ a few dozen columns. |
| Variable-length values (TEXT) | length prefix via `struct`/`to_bytes` + raw `bytes`; `str.encode('utf-8')`/`bytes.decode('utf-8')` | Classic `[len][bytes]` framing. UTF-8 in stdlib. Store offset+length in the slot directory; payload grows from the end of the page. |
| In-place page mutation without copies | `bytearray` (the page buffer) + `memoryview` (zero-copy slices) | A page is a mutable fixed-size byte buffer. `memoryview` lets the codec read/write sub-ranges without allocating. Verified: `mv[0:4] = struct.pack('<i', 42)`. Keeps page-size memory flat and predictable. |
| Open data file | `os.open(path, os.O_RDWR | os.O_CREAT, 0o644)` | Low-level fd gives positional I/O and explicit fsync, the right granularity for a pager. Avoids buffered `open()` semantics you'd have to fight. |
| Positional page read/write | `os.pread(fd, n, offset)` / `os.pwrite(fd, buf, offset)` | Read/write a page at `page_id * page_size` **without a separate seek** — atomic offset, no shared file-position races, simplest pager loop. Verified available on this platform (POSIX). |
| Durability (write-through + fsync) | `os.fsync(fd)` after each mutating `pwrite` | Forces the page to stable storage so data survives process/OS restart. This is the project's entire durability story (no WAL). One fsync per mutating op = genuinely restart-safe. |
| Atomic catalog rewrite | write temp file → `os.fsync` → `os.replace(tmp, 'catalog.json')` | `os.replace` is atomic on POSIX and Windows; rename-over guarantees readers see either the old or new complete catalog, never a torn JSON file. The standard safe-rewrite pattern. Verified present. |
| Catalog format | `json.dump` / `json.load` (with `indent=2`) | Human-readable, inspectable, diff-able, stdlib. Row bytes stay binary; only metadata is JSON. Decision already locked in PROJECT.md. |
| Schema / type modeling | `dataclasses` (`@dataclass(slots=True, frozen=True)`) + `enum.Enum` | Dataclasses give cheap immutable record/column/schema structs (matches the immutability house rule); `Enum` models the logical type set (INT/TEXT) and storage track. No pydantic — overkill, adds a dep, runtime validation we don't need here. |
| DB directory layout / paths | `pathlib.Path` | Object paths (`db_dir / 'catalog.json'`, `db_dir / f'{table_id}.tbl'`) read cleanly and are OS-portable. |
| Temp files in tests | `tempfile` and pytest's `tmp_path` fixture | Filesystem tests need a throwaway dir per test; `tmp_path` gives an isolated `Path` and auto-cleanup. `tempfile.NamedTemporaryFile` for ad-hoc temp targets. |
| Structured logging / `--debug` | `logging` | The `--debug/--verbose` and `LOG_LEVEL` requirement is exactly stdlib `logging` (levels, handlers). No structlog/loguru dependency. |
| CLI argument parsing | `argparse` | Subcommands (`init`, `doctor`/`version`, table ops), `--help`, `--db`, `--debug`, and non-zero exit codes are all native to `argparse`. Click/Typer would add a runtime dep for zero benefit at this scope. |
### Development Tools
| Tool | Purpose | Notes |
|------|---------|-------|
| pytest | 8.x (latest, dev group) | Test runner for catalog, page/tuple codec, durable write+reopen, scan, mutations, B+Tree. Use `tmp_path` for FS isolation; `pytest.raises` for error-path tests. Run via `poetry run pytest`. |
| ruff | 0.15.x (latest, dev group) | One Rust binary = linter + formatter + import sorter; replaces flake8/black/isort. Fast, single config in `pyproject.toml`. `ruff check` + `ruff format`. |
## Installation / Bootstrapping
# Upgrade Poetry to 2.x first (local has 1.8.2)
# In an empty repo, scaffold then install
## Recommended pyproject.toml (Poetry 2.x / PEP 621)
# packages = [{ include = "super_db", from = "src" }]   # uncomment if using a src/ layout
- Main dependency (`rich`) lives in PEP 621 `[project].dependencies` — the Poetry 2.x
- Console script lives in **`[project.scripts]`** (PEP 621), not the deprecated
- `[tool.poetry]` retains only Poetry-specific knobs (`package-mode`, `packages`).
- Commit `poetry.lock` for reproducible `poetry install`.
## Recommended module layout (many small files)
## The swappable-renderer pattern (Rich isolation)
# render/protocol.py  -- pure stdlib, no rich import
# render/rich_renderer.py  -- the ONLY module that imports rich
## Alternatives Considered
| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| `argparse` | Click / Typer | If you later need rich nested commands, shell completion, or a REPL. Not now — adds a runtime dep; REPL is explicitly deferred. |
| `struct` + `to_bytes` | `construct` / `kaitai_struct` | Declarative binary parsers shine for *external* formats you don't control. Here you own the format and want it readable — and they violate stdlib-only. |
| `json` (stdlib) | `orjson` / `ujson` | Only if catalog (de)serialization were a hot path. It is metadata written rarely; speed is irrelevant, and they violate stdlib-only. |
| `dataclasses` | `pydantic` / `attrs` | If you needed runtime coercion/validation of untrusted external input. Schema is internal; manual validation in `create_table` suffices and stays stdlib-only. |
| `os.pwrite`/`pread` | `mmap` | `mmap` is elegant for a buffer pool / random access at scale (out of scope here). With explicit write-through+fsync per page, positional I/O is simpler to reason about and audit. Note `os.p{read,write}` are POSIX-only; if Windows support is required, fall back to `os.lseek`+`os.read`/`os.write` or buffered `open(..., 'r+b')`. |
| `logging` | structlog / loguru | If you wanted JSON structured logs or fancier sinks. The `--debug`/`LOG_LEVEL` requirement is plain levels — stdlib covers it. |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `sqlite3` as the engine | It IS a database — using it defeats the entire educational point of writing the storage layer by hand. | Hand-rolled slotted-page heap on `os` primitives. (`sqlite3` may appear only as a *reference oracle* in tests, never in the engine.) |
| `orjson` / `ujson` | Violates stdlib-only; zero benefit on cold metadata writes. | `json` |
| `construct` / `kaitai_struct` | Violates stdlib-only; hides the byte layout you're trying to teach. | `struct` + `int.to_bytes` |
| `pydantic` / `attrs` | Runtime dep for validation you can do in one function; obscures simple data. | `dataclasses` + `enum` |
| `click` / `typer` | Runtime dep; argparse already does subcommands, `--help`, exit codes. | `argparse` |
| `bitarray` / `numpy` for the null bitmap | Heavy dep for a few bytes of bits. | `int` bit-ops + `to_bytes` |
| Native `struct` formats (`@`/`=`) | Insert platform alignment padding → non-portable, unpredictable page layout. | Always prefix `<` (little-endian, standard size, no padding). |
| Buffered `open()` for the data file | Hidden buffering fights explicit fsync semantics and positional writes. | `os.open` + `os.pwrite`/`pread` + `os.fsync`. |
| `rich` imported anywhere in `storage/`,`catalog/`,`index/` | Breaks the stdlib-only-core constraint and the swappable seam. | Import `rich` ONLY in `render/rich_renderer.py`. |
## Version Compatibility
| Package | Compatible With | Notes |
|---------|-----------------|-------|
| Python ^3.12 | Rich >=15 (needs >=3.9), pytest 8, ruff 0.15 | All targets support 3.12 comfortably. |
| Poetry 2.4.x | PEP 621 `[project]` table, `poetry-core>=2.0` | Use `[project].dependencies` + `[project.scripts]`; keep `package-mode` in `[tool.poetry]`. Local 1.8.2 predates this — upgrade first. |
| Rich 15.x | Python >=3.9, MIT | `Table`/`Tree`/`Panel`/`Text`/`Syntax`/`Console` all current. |
| `os.pwrite`/`os.pread` | POSIX/Unix only | Verified present on this Linux/WSL host. Windows would need a fallback (see Alternatives). |
## Confidence Assessment
| Area | Confidence | Reason |
|------|------------|--------|
| stdlib storage modules | HIGH | All functions executed/verified on local Python 3.12.3 (struct round-trip, pwrite/pread/fsync/replace presence, memoryview slice writes, bitmap). |
| Rich version & APIs | HIGH | PyPI + official docs confirm 15.0.0 (Apr 2026) and Table/Tree/Panel/Text/Syntax/Console. |
| Poetry 2.x pyproject shape | HIGH | Official Poetry docs confirm PEP 621 `[project]`, `[project.scripts]`, dev groups; 2.4.1 latest. |
| ruff / pytest versions | MEDIUM-HIGH | ruff 0.15.x and pytest 8.x confirmed via PyPI/docs search; pin ranges loosely. |
| Cross-platform I/O caveat | HIGH | `os.p{read,write}` POSIX-only is a documented stdlib fact; flagged with a fallback. |
## Sources
- Local interpreter execution (Python 3.12.3) — verified `struct`, `os.pwrite`/`pread`/`fsync`/`replace`, `memoryview`, `bytearray`, null-bitmap bit-ops — HIGH
- https://pypi.org/project/rich/ + https://rich.readthedocs.io/en/stable/introduction.html — Rich 15.0.0, Table/Tree/Panel/Text/Syntax/Console — HIGH
- https://python-poetry.org/docs/pyproject/ — PEP 621 `[project]`, `[project.scripts]`, `package-mode`, dev groups, build-system — HIGH
- https://python-poetry.org/blog/category/releases/ — Poetry 2.4.1 (May 2026) latest, 2.x line — HIGH
- https://docs.astral.sh/ruff/configuration/ + https://pypi.org/project/ruff/ — ruff 0.15.x, `[tool.ruff.lint]` select — MEDIUM-HIGH
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- PROJECT:validation-start (hand-maintained — survives GSD regen) -->
## Per-Phase Validation Gate (MANDATORY)

Every phase must pass the validation pipeline in `.planning/VALIDATION.md` **after `/gsd-execute-phase` and before the phase closes / before `/gsd-verify-work`**.

Pipeline (sequential, fresh independent agents, loop until clean):
1. **Stage 1 — code review (parallel):** 1.1 Bug Hunter + 1.2 Regression Hunter.
2. **Stage 2 — research revalidation (parallel):** two independent validators re-check the research claims that guided the phase; re-run the researcher (fresh) to re-derive any disputed claim. Auto-skip (`n/a`) if the phase had no material research claims.
3. **Stage 3 — fixes:** a separate fresh Fixer applies minimal, human-looking fixes for CRITICAL/HIGH findings, then re-run the relevant stages with fresh finders.

**Exit:** zero CRITICAL/HIGH from bug + regression + research validators; MEDIUM/LOW logged in `{phase_dir}/{padded_phase}-REVIEW.md`. Validators and researchers may be re-run as many times as needed. Keep all code simple and human-looking; uphold KISS/DRY/YAGNI and the dependency rule (helper libs only — no library that does core DB work).

Spawn each agent with the exact fresh-look prompt in `.planning/VALIDATION.md` (no executor context leaks in).
<!-- PROJECT:validation-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
