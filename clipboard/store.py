"""SQLite-backed clipboard history.

The headline feature is that history outlives a reboot and stays searchable at
several hundred entries, so this is a real indexed table rather than a JSON blob
rewritten on every copy.

Threading: the capture thread writes and the Tk thread reads, so one connection
is shared with check_same_thread off and every public method takes an RLock.
sqlite3 connections are otherwise pinned to their creating thread, and getting
this wrong shows up as an intermittent "objects created in a thread can only be
used in that same thread" crash hours into a session.

Deduplication is by content digest across the whole table, not just against the
previous entry: copying something you copied last week should promote that row
to the top rather than leave two identical rows in the list.
"""

import hashlib
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id      INTEGER PRIMARY KEY,
    kind    TEXT    NOT NULL,              -- 'text' or 'image'
    digest  TEXT    NOT NULL,
    content BLOB    NOT NULL,              -- utf-8 bytes, or PNG bytes
    preview TEXT    NOT NULL,              -- what search matches and the list shows
    thumb   BLOB,                          -- small PNG, images only
    created REAL    NOT NULL,
    used    REAL    NOT NULL,              -- last copied/selected; drives ordering
    pinned  INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS entries_digest ON entries(digest);
CREATE INDEX IF NOT EXISTS entries_order ON entries(pinned DESC, used DESC);
"""


def digest_of(kind, content):
    h = hashlib.sha256()
    h.update(kind.encode())
    h.update(content)
    return h.hexdigest()


def _like(term):
    """Escape LIKE wildcards so searching for '100%' doesn't match everything."""
    for ch in ("\\", "%", "_"):
        term = term.replace(ch, "\\" + ch)
    return f"%{term}%"


class Store:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # WAL keeps the reader (popup) from blocking the writer (capture thread).
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # -- writing ----------------------------------------------------------
    def add(self, kind, content, preview, thumb=None):
        """Insert, or promote the existing identical row. Returns its id."""
        now = time.time()
        key = digest_of(kind, content)
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM entries WHERE digest=?", (key,)
            ).fetchone()
            if row:
                self._db.execute(
                    "UPDATE entries SET used=? WHERE id=?", (now, row["id"])
                )
                self._db.commit()
                return row["id"]
            cur = self._db.execute(
                "INSERT INTO entries (kind, digest, content, preview, thumb,"
                " created, used, pinned) VALUES (?,?,?,?,?,?,?,0)",
                (kind, key, content, preview, thumb, now, now),
            )
            self._db.commit()
            return cur.lastrowid

    def touch(self, entry_id):
        with self._lock:
            self._db.execute(
                "UPDATE entries SET used=? WHERE id=?", (time.time(), entry_id)
            )
            self._db.commit()

    def set_pinned(self, entry_id, pinned):
        with self._lock:
            self._db.execute(
                "UPDATE entries SET pinned=? WHERE id=?", (1 if pinned else 0, entry_id)
            )
            self._db.commit()

    def toggle_pin(self, entry_id):
        with self._lock:
            row = self._db.execute(
                "SELECT pinned FROM entries WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None:
                return None
            new = 0 if row["pinned"] else 1
            self._db.execute("UPDATE entries SET pinned=? WHERE id=?", (new, entry_id))
            self._db.commit()
            return bool(new)

    def delete(self, entry_id):
        with self._lock:
            self._db.execute("DELETE FROM entries WHERE id=?", (entry_id,))
            self._db.commit()

    def clear(self, keep_pinned=True):
        """Returns how many rows went away."""
        sql = "DELETE FROM entries" + (" WHERE pinned=0" if keep_pinned else "")
        with self._lock:
            cur = self._db.execute(sql)
            self._db.commit()
            return cur.rowcount

    def prune(self, max_items=0, max_age_days=0):
        """Drop unpinned entries past the cap. Zero on either limit disables it."""
        removed = 0
        with self._lock:
            if max_age_days and max_age_days > 0:
                cutoff = time.time() - max_age_days * 86400
                cur = self._db.execute(
                    "DELETE FROM entries WHERE pinned=0 AND used<?", (cutoff,)
                )
                removed += cur.rowcount
            if max_items and max_items > 0:
                cur = self._db.execute(
                    "DELETE FROM entries WHERE pinned=0 AND id NOT IN ("
                    " SELECT id FROM entries WHERE pinned=0"
                    " ORDER BY used DESC LIMIT ?)",
                    (max_items,),
                )
                removed += cur.rowcount
            # Commit even when nothing matched. sqlite3 opens a write
            # transaction for a DELETE regardless of how many rows it hits, so
            # committing only `if removed` leaves it dangling — and since this
            # process is long-lived, it would then hold the database's write
            # lock until quit and stop WAL from ever checkpointing.
            self._db.commit()
        return removed

    # -- reading ----------------------------------------------------------
    def search(self, query="", limit=300):
        """Rows for the popup: pinned first, then most-recently-used.

        Multi-word queries AND their terms, so "api key" finds a line holding
        both words in any order.
        """
        where, params = [], []
        for term in str(query).strip().lower().split():
            where.append("lower(preview) LIKE ? ESCAPE '\\'")
            params.append(_like(term))
        sql = (
            "SELECT id, kind, preview, thumb, pinned, used FROM entries"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY pinned DESC, used DESC LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def get(self, entry_id):
        with self._lock:
            return self._db.execute(
                "SELECT * FROM entries WHERE id=?", (entry_id,)
            ).fetchone()

    def count(self):
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(pinned),0) p FROM entries"
            ).fetchone()
            return row["n"], row["p"]
