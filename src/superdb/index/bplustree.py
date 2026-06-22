"""B+Tree algorithm: create, insert, search over fixed-size index pages.

Mirrors heap_file.py:
  - open-per-op: each public method opens its own fd, closes in finally
  - __slots__: only _path and _page_size (no cached root_page_id)
  - all node writes go through write_page from durability.py
  - directory fsync on first .idx create

Split rules:
  Leaf split  : copy-up   — separator = first key of right leaf; key STAYS in right leaf
  Internal split: push-up — median key removed from both children, sent up to parent

Durability commit point:
  On root split, the header page (page 0) is rewritten LAST as the commit point.
  A crash before that rewrite leaves the prior valid root intact.

Note: delete/update do not maintain the index this phase.
Phase 8 should add index maintenance to StorageEngine.update/delete.
"""
from __future__ import annotations

import os
from pathlib import Path

from superdb.durability import fsync_dir, write_page
from superdb.errors import DuplicateKeyError, IndexKeyNotFoundError, StorageError
from superdb.index.node_layout import (
    KEY_TYPE_INT,
    NULL_PAGE_ID,
    TEXT_KEY_CAP_DEFAULT,
    Header,
    InternalNode,
    LeafNode,
    compare_int_keys,
    compare_text_keys,
    decode_header,
    decode_node,
    encode_header,
    encode_int_key,
    encode_internal,
    encode_leaf,
    encode_text_key,
    int_internal_max_keys,
    int_leaf_max,
    text_internal_max_keys,
    text_leaf_max,
)
from superdb.storage.rid import RID


class BPlusTree:
    """Persistent B+Tree index over a single column in its own .idx file.

    Each node occupies one fixed-size page. Page 0 is the header (metadata +
    current root_page_id). All node writes go through write_page (full page +
    fsync). Open-per-op: no fd or root_page_id held in instance state.

    Use BPlusTree.create(...) to create a new index file.
    Use BPlusTree(path, page_size) to reopen an existing one.

    Crash-durability scope (no WAL, by milestone design): a node split writes
    the new right page first, then the modified left/parent pages, and a root
    split rewrites the header page last as the commit point. A single insert is
    therefore restart-safe across one fsync'd write. A split that spans multiple
    pages is NOT crash-atomic — a crash partway through a multi-level split can
    strand a newly allocated page that no parent yet points to (its keys become
    unreachable). True multi-page split atomicity needs a write-ahead log, which
    is explicitly out of scope for this milestone (matching the heap's accepted
    fsync-per-op crash windows). The committed-on-disk tree is always a valid
    B+Tree for every fully completed insert.
    """

    __slots__ = ("_path", "_page_size")

    def __init__(self, idx_path: Path, page_size: int) -> None:
        self._path = idx_path
        self._page_size = page_size

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        idx_path: Path,
        page_size: int,
        key_type: int,
        col_name: str,
        text_key_cap: int = TEXT_KEY_CAP_DEFAULT,
    ) -> BPlusTree:
        """Create a new index file and return a BPlusTree pointing at it.

        Writes page 0 = header (root_page_id=1) and page 1 = empty leaf.
        Performs a directory fsync after closing the file.

        Fails with StorageError if the file already exists (O_EXCL) — recreating
        over a populated index would strand its old data pages and corrupt page
        allocation. Callers rebuilding an index must unlink the old file first.
        """
        try:
            fd = os.open(str(idx_path), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as e:
            raise StorageError(f"index file already exists: {idx_path}") from e
        try:
            # Page 0: header — root lives at page 1
            hdr_bytes = encode_header(key_type, text_key_cap, 1, col_name, page_size)
            write_page(fd, 0, hdr_bytes, page_size)
            # Page 1: initial empty leaf
            empty_leaf = LeafNode(entries=(), next_leaf=NULL_PAGE_ID)
            leaf_bytes = encode_leaf(empty_leaf, key_type, text_key_cap, page_size)
            write_page(fd, 1, leaf_bytes, page_size)
        finally:
            os.close(fd)

        # Directory fsync — makes the new directory entry durable
        fsync_dir(idx_path.parent)

        return cls(idx_path, page_size)

    # ------------------------------------------------------------------
    # Internal: header + node I/O helpers (all accept open fd)
    # ------------------------------------------------------------------

    def _read_header(self, fd: int) -> Header:
        """Read and validate the header page. Returns Header."""
        raw = os.pread(fd, self._page_size, 0)
        return decode_header(raw)  # validates magic + version

    def _read_node(self, fd: int, page_id: int, key_type: int, cap: int) -> LeafNode | InternalNode:
        """Read and decode a node page."""
        raw = os.pread(fd, self._page_size, page_id * self._page_size)
        return decode_node(raw, key_type, cap)

    def _write_node(
        self, fd: int, page_id: int, node: LeafNode | InternalNode, key_type: int, cap: int
    ) -> None:
        """Encode and durably write a node page via write_page."""
        page_size = self._page_size
        if isinstance(node, LeafNode):
            page_bytes = encode_leaf(node, key_type, cap, page_size)
        else:
            page_bytes = encode_internal(node, key_type, cap, page_size)
        write_page(fd, page_id, page_bytes, page_size)

    def _alloc_page(self, fd: int) -> int:
        """Return the next available page_id (current filesize // page_size = append)."""
        return os.fstat(fd).st_size // self._page_size

    # ------------------------------------------------------------------
    # Internal: key encoding + comparison dispatch
    # ------------------------------------------------------------------

    def _encode_key(self, key: int | str, key_type: int, cap: int) -> bytes:
        """Encode a key to bytes using the appropriate codec."""
        if key_type == KEY_TYPE_INT:
            return encode_int_key(key)  # type: ignore[arg-type]
        return encode_text_key(key, cap)  # type: ignore[arg-type]

    def _compare(self, a: bytes, b: bytes, key_type: int) -> int:
        """Compare two encoded keys. Returns negative, zero, or positive."""
        if key_type == KEY_TYPE_INT:
            return compare_int_keys(a, b)
        return compare_text_keys(a, b)

    def _leaf_max(self, key_type: int, cap: int) -> int:
        """Max entries in a leaf node."""
        if key_type == KEY_TYPE_INT:
            return int_leaf_max(self._page_size)
        return text_leaf_max(self._page_size, cap)

    def _internal_max(self, key_type: int, cap: int) -> int:
        """Max separator keys in an internal node."""
        if key_type == KEY_TYPE_INT:
            return int_internal_max_keys(self._page_size)
        return text_internal_max_keys(self._page_size, cap)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, key: int | str) -> RID:
        """Return the RID for key, or raise IndexKeyNotFoundError if absent.

        Descends from the root (read from header) to the appropriate leaf.
        Raises StorageError if the index file does not exist.
        """
        try:
            fd = os.open(str(self._path), os.O_RDONLY)
        except FileNotFoundError as exc:
            raise StorageError(f"index file not found: {self._path}") from exc
        try:
            hdr = self._read_header(fd)
            encoded = self._encode_key(key, hdr.key_type, hdr.text_key_cap)
            return self._search_recursive(
                fd, encoded, hdr.root_page_id, hdr.key_type, hdr.text_key_cap
            )
        finally:
            os.close(fd)

    def _search_recursive(
        self,
        fd: int,
        encoded_key: bytes,
        page_id: int,
        key_type: int,
        cap: int,
    ) -> RID:
        """Recursively descend to the leaf holding encoded_key and return its RID.

        Leaf and internal node descent are SEPARATE branches.
        """
        node = self._read_node(fd, page_id, key_type, cap)
        if isinstance(node, LeafNode):
            # Scan leaf entries for exact match
            for k, rid in node.entries:
                if k == encoded_key:
                    return rid
            raise IndexKeyNotFoundError("key not found in index")
        # Internal node: find child via key comparison
        child_idx = len(node.keys)  # default: rightmost child
        for i, k in enumerate(node.keys):
            if self._compare(encoded_key, k, key_type) < 0:
                child_idx = i
                break
        return self._search_recursive(fd, encoded_key, node.children[child_idx], key_type, cap)

    def insert(self, key: int | str, rid: RID) -> None:
        """Insert key -> rid into the index.

        Raises DuplicateKeyError if key already exists.
        Raises StorageError if the index file does not exist.
        On root split, the header page is rewritten LAST as the commit point.

        Note: delete/update do not maintain the index this phase.
        TODO(Phase8): update index on relocations caused by StorageEngine.update.
        """
        if key is None:
            raise StorageError("index key may not be NULL")
        try:
            fd = os.open(str(self._path), os.O_RDWR)
        except FileNotFoundError as exc:
            raise StorageError(f"index file not found: {self._path}") from exc
        try:
            hdr = self._read_header(fd)
            key_type = hdr.key_type
            cap = hdr.text_key_cap
            encoded = self._encode_key(key, key_type, cap)
            result = self._insert_recursive(fd, encoded, rid, hdr.root_page_id, key_type, cap)
            if result is not None:
                # Root split: old root and right child already persisted by recursion.
                sep, right_pid = result
                new_root_id = self._alloc_page(fd)
                new_root = InternalNode(keys=(sep,), children=(hdr.root_page_id, right_pid))
                self._write_node(fd, new_root_id, new_root, key_type, cap)
                # Rewrite header with new root_page_id — this is the commit point
                new_hdr_bytes = encode_header(
                    key_type, cap, new_root_id, hdr.col_name, self._page_size
                )
                write_page(fd, 0, new_hdr_bytes, self._page_size)
        finally:
            os.close(fd)

    def _insert_recursive(
        self,
        fd: int,
        encoded_key: bytes,
        rid: RID,
        page_id: int,
        key_type: int,
        cap: int,
    ) -> tuple[bytes, int] | None:
        """Insert encoded_key+rid into the subtree at page_id.

        Returns (separator_key, right_page_id) if this node split, else None.
        """
        node = self._read_node(fd, page_id, key_type, cap)
        if isinstance(node, LeafNode):
            return self._leaf_insert(fd, node, encoded_key, rid, page_id, key_type, cap)
        # Internal node: find child and recurse
        child_idx = len(node.keys)  # default: rightmost child
        for i, k in enumerate(node.keys):
            if self._compare(encoded_key, k, key_type) < 0:
                child_idx = i
                break
        result = self._insert_recursive(
            fd, encoded_key, rid, node.children[child_idx], key_type, cap
        )
        if result is None:
            return None
        sep, right_pid = result
        return self._internal_insert(fd, node, sep, right_pid, child_idx, page_id, key_type, cap)

    def _leaf_insert(
        self,
        fd: int,
        node: LeafNode,
        encoded_key: bytes,
        rid: RID,
        page_id: int,
        key_type: int,
        cap: int,
    ) -> tuple[bytes, int] | None:
        """Insert encoded_key+rid into a leaf node using copy-up split rule.

        Duplicate key raises DuplicateKeyError.
        Returns (separator_key, right_page_id) on split, else None.
        Separator = first key of the right leaf; key STAYS in right leaf (copy-up).
        """
        # Find insertion position (linear scan over encoded bytes)
        entries = list(node.entries)
        insert_pos = len(entries)
        for i, (k, _) in enumerate(entries):
            cmp = self._compare(encoded_key, k, key_type)
            if cmp == 0:
                raise DuplicateKeyError("key already exists in index")
            if cmp < 0:
                insert_pos = i
                break

        # Build new sorted entry list
        new_entry = (encoded_key, rid)
        entries.insert(insert_pos, new_entry)

        leaf_max = self._leaf_max(key_type, cap)

        if len(entries) <= leaf_max:
            # No split: write updated leaf in place
            updated = LeafNode(entries=tuple(entries), next_leaf=node.next_leaf)
            self._write_node(fd, page_id, updated, key_type, cap)
            return None

        # Leaf split (copy-up rule):
        # mid = split point; separator = entries[mid].key (STAYS in right leaf)
        mid = len(entries) // 2
        right_pid = self._alloc_page(fd)

        left_leaf = LeafNode(entries=tuple(entries[:mid]), next_leaf=right_pid)
        right_leaf = LeafNode(entries=tuple(entries[mid:]), next_leaf=node.next_leaf)
        separator = entries[mid][0]  # first key of right leaf — copy-up

        # Durability order: write right (new page) first, then left (existing page)
        self._write_node(fd, right_pid, right_leaf, key_type, cap)
        self._write_node(fd, page_id, left_leaf, key_type, cap)

        return (separator, right_pid)

    def _internal_insert(
        self,
        fd: int,
        node: InternalNode,
        sep: bytes,
        right_pid: int,
        child_idx: int,
        page_id: int,
        key_type: int,
        cap: int,
    ) -> tuple[bytes, int] | None:
        """Insert sep+right_pid into an internal node using push-up split rule.

        Returns (median_key, right_page_id) on split, else None.
        Median is REMOVED from both children (push-up — not copy-up).
        """
        # Build expanded key and children lists
        keys = list(node.keys)
        children = list(node.children)

        # Insert new separator key at child_idx and right child at child_idx+1
        keys.insert(child_idx, sep)
        children.insert(child_idx + 1, right_pid)

        internal_max = self._internal_max(key_type, cap)

        if len(keys) <= internal_max:
            # No split: write updated internal node in place
            updated = InternalNode(keys=tuple(keys), children=tuple(children))
            self._write_node(fd, page_id, updated, key_type, cap)
            return None

        # Internal split (push-up rule):
        # mid = median index; median is PUSHED UP and removed from both children
        mid = len(keys) // 2
        median = keys[mid]

        right_new_pid = self._alloc_page(fd)

        left_node = InternalNode(
            keys=tuple(keys[:mid]),
            children=tuple(children[:mid + 1]),
        )
        right_node = InternalNode(
            keys=tuple(keys[mid + 1:]),
            children=tuple(children[mid + 1:]),
        )

        # Durability order: write right (new page) first, then left (existing page)
        self._write_node(fd, right_new_pid, right_node, key_type, cap)
        self._write_node(fd, page_id, left_node, key_type, cap)

        return (median, right_new_pid)

    def root_is_internal(self) -> bool:
        """Return True if the current root is an InternalNode (tree height >= 2).

        Used by tests to verify that splits occurred.
        """
        try:
            fd = os.open(str(self._path), os.O_RDONLY)
        except FileNotFoundError as exc:
            raise StorageError(f"index file not found: {self._path}") from exc
        try:
            hdr = self._read_header(fd)
            root = self._read_node(fd, hdr.root_page_id, hdr.key_type, hdr.text_key_cap)
            return isinstance(root, InternalNode)
        finally:
            os.close(fd)
