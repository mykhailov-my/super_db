# super_db — Storage Layer

## What This Is

An educational, single-node DBMS built from scratch in Python to learn database internals (CMU 15-445 style). This milestone delivers the **complete storage layer**: a persistent catalog, a slotted-page row-store heap engine with durable writes, sequential scan, mutations, and a B+Tree index — plus a graphical CLI that visualizes the on-disk structures (pages, schema, hex dumps, the B+Tree). Later course milestones (SQL parser, query planner, execution engine) build on top of this storage API.

## Core Value

A correct, restart-safe row-store: create a table, insert/read/scan/update/delete records, restart the process, and get exactly the same data back — with code simple enough to read top to bottom and understand how a database stores bytes on disk.

## Requirements

### Validated

(None yet — ship to validate)

### Active

<!-- Stage 0 — Bootstrap & CLI foundation -->
- [ ] Project builds reproducibly from a clean checkout with one documented command (`poetry install`)
- [ ] CLI entry point (`db-cli`) supports `--help`, a diagnostic command (`doctor`/`version`), and non-zero exit codes on error
- [ ] `db-cli init --db <path>` creates an empty database directory with minimal service metadata identifying it as a super_db instance
- [ ] A previously initialized database directory can be re-opened by a diagnostic command
- [ ] `--debug` / `--verbose` (or `LOG_LEVEL`) mode emits useful diagnostics (startup, db path, command, file create/open events, error detail)

<!-- Stage 1 — Persistent Catalog -->
- [ ] Persistent JSON catalog stores, per table: stable `table_id`, name, ordered columns with logical type and nullability, chosen storage track, and physical-layout info
- [ ] Catalog operations work: `create_table`, `open_table`, `list_tables`, `describe_table` (and `drop_table`)
- [ ] `create_table` writes schema to the catalog AND creates the empty physical heap file on disk
- [ ] Schema validation rejects duplicate column names, empty/invalid table or column names, and unsupported types
- [ ] Catalog and table metadata survive a close/reopen cycle and restore the full structure
- [ ] RID semantics are defined and documented: `RID = (page_id, slot_id)`

<!-- Stage 2 — Physical Layout (S2) -->
- [ ] Slotted-page physical layout encodes fixed-length (INT/int32), variable-length (TEXT), and NULL values, with a null bitmap and service page metadata
- [ ] Page size is configurable per table, default 4 KiB; the chosen layout/page size is persisted and used on reopen

<!-- Stage 2 — Durable Writes & Lookup (S3) -->
- [ ] `insert(table, record)` appends a record, returns its RID, and write-through+fsyncs the affected page to disk
- [ ] `get(table, rid)` returns the record for a RID, and returns the same records after process restart

<!-- Stage 2 — Sequential Scan (S4) -->
- [ ] `scan(table)` returns all live records in a clear format, skips tombstoned/invalid records, and works after restart

<!-- Stage 2 — Mutation Model (S5) -->
- [ ] `update(table, rid, record)` updates in place when it fits, else relocates (delete + reinsert), and reflects new values on read/scan
- [ ] `delete(table, rid)` tombstones the slot so the record no longer appears in scan; RIDs of other records stay stable
- [ ] update/delete on a non-existent RID is handled with a clear error; update/delete results survive restart

<!-- Stage 2 — Optimization (S6) -->
- [ ] A B+Tree index over a table key supports build, key insert on new records, point lookup by key, and persists across restart

<!-- Graphical CLI / tooling -->
- [ ] Graphical CLI (Rich, behind a swappable renderer interface) visualizes: slotted-page byte layout, catalog/schema tables, annotated hex dump of a record's bytes, and the B+Tree structure
- [ ] Automated pytest suite covers catalog, page/tuple codec, durable write+reopen, scan correctness, mutations, and B+Tree — run by one documented command (`poetry run pytest`)
- [ ] Short README / design doc covers build, CLI usage, test command, project layout, chosen storage track + rationale, catalog format, table metadata format, and RID scheme

### Out of Scope

- SQL parser, AST, lexer — later course milestone (Stage 3); storage exposes a Python API + CLI, not SQL
- Query planner and execution engine (TableScan/Filter/Projection/OrderBy operators) — later milestones (Stages 4–5)
- JOIN, aggregation, mini-optimizer, query-level features — later milestone (Stage 6)
- Write-ahead log (WAL) and crash recovery — durability is write-through+fsync; WAL is beyond "as simple as possible"
- Buffer pool with LRU eviction — write-through avoids it; can be revisited if performance matters later
- Page compaction / segment compaction — tombstones are sufficient for this milestone; compaction deferred
- Column-store and hybrid (PAX) tracks — row-store chosen; the catalog records the track to leave the door open
- Secondary indexes beyond the single B+Tree, compression, multiple namespaces — not required this milestone
- Interactive REPL (`db>` loop) — deferred; one-shot CLI subcommands only for v1
- Concurrency / transactions / multi-process access — single-process, single-threaded this milestone

## Context

- **Educational project.** Course builds one cohesive minimal DBMS across stages (specs in `.hw_docs/`). This task is the storage layer only (Stages 0–2 / S2–S6); reviewer evaluates correctness, code structure, tests, logging/debug, and a short design doc.
- **Clean slate.** The repo had a partial slotted-page scaffold (`src/data/pages.py`, `paged_file.py`, `heap_file.py`) and `INTERNALS.md`. Per decision, all of it was wiped to start fresh. Kept: `.hw_docs/`, `.env.example`, `.gitignore`, `.vscode/`.
- **Author background.** Comfortable with Python and DBMS internals concepts (CMU 15-445, Petrov's *Database Internals*). Wants the code legible and modular, not clever.
- **"LLM work invisible."** Generated code, comments, and docs must read as ordinary human authorship — no AI fingerprints, no boilerplate filler, no over-commenting. Commit history is backdated to look like ~a month of natural incremental work.

## Constraints

- **Tech stack**: Python ^3.12, Poetry for packaging/deps. Storage + catalog **core is stdlib-only** (no 3rd-party libs). The single allowed runtime dependency is **Rich**, confined to the CLI presentation layer behind a swappable renderer interface. Dev deps: pytest, ruff.
- **Simplicity (hard)**: "As simple as possible." Prefer the simplest correct design; KISS/DRY/YAGNI. Many small, cohesive modules over large ones. No speculative abstraction.
- **Modularity (hard)**: Catalog, low-level storage manager, and CLI/visualization are cleanly separated. Rendering library must be swappable without touching storage code.
- **Durability**: Write-through + fsync on every mutating operation; data must survive process restart.
- **Testing**: Well-tested. pytest suite covering all key aspects, run by one documented command.
- **Storage track**: Row-store, slotted-page heap files, `RID = (page_id, slot_id)`. Recorded in metadata; other tracks intentionally not implemented.
- **Commit history**: Backdated and spread believably from ~30 days ago to today (≈2026-05-06 → 2026-06-05), natural human cadence (no commits at odd hours, slight clustering/gaps), earlier phases dated earlier.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Row-store, slotted-page heap files | Simplest classic OLTP layout; clean `(page_id, slot_id)` RID; easy to test point ops | — Pending |
| JSON catalog (`catalog.json` at db root) | Human-readable, inspectable, stdlib `json`, dead-simple code; row bytes stay binary | — Pending |
| Configurable page size, default 4 KiB | Small enough to see multi-page behavior in tests; per-table override for flexibility | — Pending |
| Write-through + fsync durability | Genuinely restart-safe without WAL/buffer-pool/recovery complexity | — Pending |
| Tombstone deletes; update in-place-or-relocate | Keeps scan correct and RIDs stable; compaction deferred | — Pending |
| Types: INT (int32), TEXT, NULL (null bitmap) | Spec floor: one fixed + one variable-length type; smallest correct surface | — Pending |
| B+Tree for S6 optimization | Classic key-lookup structure; pairs naturally with row-store point lookups | — Pending |
| Rich for CLI visuals, behind swappable renderer | Rich draws tables/trees/hex/panels well; renderer seam keeps lib swappable, core stdlib-only | — Pending |
| Wipe existing scaffold, start fresh | User directive; old partial code shouldn't constrain a clean simple design | — Pending |
| Defer REPL | REPL is a parser-stage concern; one-shot subcommands keep v1 scope tight | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-05 after initialization*
