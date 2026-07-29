"""Database Unpacker — monitor ane.db and auto-dump readable text on changes.

Detects writes to the SQLite database file (including WAL commits), waits for
the write to settle, then reads all tables with the synchronous sqlite3 module
and writes a human-readable text dump to data/unpacked/ane_dump.txt.

Uses watchfiles (already a project dependency) for cross-platform file monitoring
with zero polling overhead.
"""

import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────

# Filesystem patterns that trigger a re-dump.  These cover the main database,
# the WAL file (where most writes land first), and the shared-memory file.
_WATCH_PATTERNS = ("*.db", "*.db-wal", "*.db-shm")

# Seconds to wait after the last detected change before performing the dump.
# This debounces rapid consecutive writes into a single dump.
_DEBOUNCE_SECONDS = 0.5

# Subdirectory under data/ for output files
_OUTPUT_SUBDIR = "unpacked"
_OUTPUT_FILENAME = "ane_dump.txt"


class DatabaseUnpacker:
    """Watches the SQLite database file and auto-generates a text dump.

    Lifecycle:
        await unpacker.start()   — begin watching
        await unpacker.stop()    — stop watching
    """

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path).resolve()
        self._target_dir = self._db_path.parent
        self._output_path = self._target_dir / _OUTPUT_SUBDIR / _OUTPUT_FILENAME
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._change_event = asyncio.Event()

    # ── Public API ────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch the file watcher and perform an initial dump."""
        if self._task is not None:
            logger.warning("Unpacker already running")
            return

        logger.info(
            "DatabaseUnpacker starting — watching %s → %s",
            self._target_dir, self._output_path,
        )
        self._stop_event.clear()
        self._change_event.clear()

        # Ensure output directory exists
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        # Perform an initial dump immediately
        await asyncio.to_thread(self._dump_database)

        # Start the watcher task
        self._task = asyncio.create_task(self._run_watcher())
        logger.info("DatabaseUnpacker started")

    async def stop(self) -> None:
        """Stop the file watcher."""
        if self._task is None:
            return
        logger.info("DatabaseUnpacker stopping...")
        self._stop_event.set()
        self._change_event.set()  # unblock any pending sleep
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("DatabaseUnpacker stopped")

    # ── Watcher loop ──────────────────────────────────────────────

    async def _run_watcher(self) -> None:
        """Core loop: watch for file changes, debounce, then dump."""
        try:
            from watchfiles import awatch
        except ImportError:
            logger.error("watchfiles not installed — DatabaseUnpacker disabled")
            return

        # We watch the data directory for changes matching our patterns.
        # awatch yields sets of (change_type, path) tuples.
        async for changes in awatch(
            str(self._target_dir),
            watch_filter=None,  # we filter ourselves for clarity
            debounce=500,       # built-in debounce in ms (coarse)
        ):
            if self._stop_event.is_set():
                break

            # Check whether any change matches our patterns
            relevant = False
            for _change_type, changed_path in changes:
                name = os.path.basename(changed_path)
                if any(name.endswith(p.lstrip("*"))
                       for p in _WATCH_PATTERNS):
                    relevant = True
                    break

            if not relevant:
                continue

            # Additional fine-grained debounce via asyncio.sleep
            await asyncio.sleep(_DEBOUNCE_SECONDS)
            if self._stop_event.is_set():
                break

            logger.debug("Database change detected — dumping...")
            try:
                await asyncio.to_thread(self._dump_database)
            except Exception:
                logger.exception("Database dump failed")

    # ── Dump logic ────────────────────────────────────────────────

    def _dump_database(self) -> None:
        """Read the full database and write a human-readable text dump.

        Runs in a thread (via asyncio.to_thread) to avoid blocking the
        event loop.  Opens the database read-only so it never interferes
        with the running application's writes.
        """
        start = time.monotonic()

        # file: URI with mode=ro for guaranteed read-only access.
        # Also enables WAL checkpoint visibility without our own PRAGMA.
        uri = self._db_path.resolve().as_uri()
        # On Windows path.as_uri() returns file:///C:/..., strip the scheme
        # for sqlite3's URI mode and use ?mode=ro
        conn = sqlite3.connect(
            f"file:{self._db_path}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        conn.row_factory = sqlite3.Row

        try:
            # Get list of user tables (exclude sqlite_* internal tables)
            tables_res = conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
            tables = tables_res.fetchall()

            lines: list[str] = []
            lines.append("=" * 72)
            lines.append("ANE Database Dump")
            lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
            lines.append(f"Source:    {self._db_path}")
            lines.append("=" * 72)

            for table_row in tables:
                table_name = table_row["name"]
                lines.append("")
                lines.append(f"── {table_name} —".ljust(72, "─"))

                # Fetch all rows
                try:
                    rows = conn.execute(
                        f'SELECT * FROM "{table_name}"'
                    ).fetchall()
                except sqlite3.OperationalError as e:
                    lines.append(f"  (read error: {e})")
                    continue

                if not rows:
                    lines.append("  (empty)")
                    continue

                # Column headers
                col_names = [desc[0] for desc in conn.execute(
                    f'SELECT * FROM "{table_name}" LIMIT 0'
                ).description or []]
                if col_names:
                    lines.append("  " + " | ".join(col_names))

                lines.append(f"  ({len(rows)} row{'s' if len(rows) != 1 else ''})")

                # Data rows
                for row in rows:
                    values: list[str] = []
                    for key in col_names:
                        val = row[key]
                        # Format values for readability
                        if val is None:
                            values.append("NULL")
                        elif isinstance(val, (int, float)):
                            values.append(str(val))
                        elif isinstance(val, bytes):
                            values.append(f"<blob {len(val)}B>")
                        else:
                            # Truncate long text fields to keep the dump scannable.
                            s = str(val).replace("\n", "\\n")
                            if len(s) > 200:
                                s = s[:197] + "..."
                            values.append(s)
                    lines.append("  " + " | ".join(values))

            lines.append("")
            lines.append("=" * 72)
            lines.append(f"End of dump — {sum(1 for _ in tables)} tables")
            lines.append("=" * 72)
            lines.append("")

            output = "\n".join(lines)

            # ── Per-user dumps ──
            try:
                user_rows = conn.execute("SELECT id, username, display_name FROM users").fetchall()
            except Exception:
                user_rows = []
            user_output_dir = self._target_dir / _OUTPUT_SUBDIR / "users"
            user_output_dir.mkdir(parents=True, exist_ok=True)
            for user_row in user_rows:
                uid = user_row["id"]
                name = user_row["display_name"] or user_row["username"] or uid
                try:
                    sid_rows = conn.execute(
                        "SELECT id FROM sessions WHERE user_id = ?", (uid,)
                    ).fetchall()
                except Exception:
                    sid_rows = []
                session_ids = {r["id"] for r in sid_rows}
                if not session_ids:
                    continue
                ulines = [f"{'='*72}", f"User: {name}  ({uid})",
                          f"Sessions: {len(session_ids)}", f"{'='*72}", ""]
                for tbl, id_col in [
                    ("sessions", "id"), ("players", "session_id"),
                    ("npcs", "session_id"),
                    ("facts", "session_id"), ("memories", "session_id"),
                    ("event_logs", "session_id"),
                ]:
                    try:
                        rows = conn.execute(f'SELECT * FROM "{tbl}"').fetchall()
                    except Exception:
                        continue
                    if tbl == "sessions":
                        matching = [r for r in rows if r[id_col] in session_ids]
                    else:
                        try:
                            matching = [r for r in rows if r["session_id"] in session_ids]
                        except Exception:
                            matching = []
                    if not matching:
                        continue
                    ulines.append(f"── {tbl} ({len(matching)}) {'─'*max(0,60-len(tbl)-8)}")
                    col_names = [d[0] for d in conn.execute(
                        f'SELECT * FROM "{tbl}" LIMIT 0').description or []]
                    if col_names:
                        ulines.append("  " + " | ".join(col_names))
                    for row in matching:
                        vals = []
                        for key in col_names:
                            v = row[key]
                            if v is None:
                                vals.append("NULL")
                            elif isinstance(v, (int, float)):
                                vals.append(str(v))
                            elif isinstance(v, bytes):
                                vals.append(f"<blob {len(v)}B>")
                            else:
                                s = str(v).replace("\n", "\\n")
                                if len(s) > 200:
                                    s = s[:197] + "..."
                                vals.append(s)
                        ulines.append("  " + " | ".join(vals))
                    ulines.append("")
                safe_name = name.replace("/","_").replace("\\","_").replace(" ","_")[:60]
                u_tmp = user_output_dir / f"{safe_name}.tmp"
                u_tmp.write_text("\n".join(ulines), encoding="utf-8")
                os.replace(u_tmp, user_output_dir / f"{safe_name}.txt")

        finally:
            conn.close()

        elapsed = time.monotonic() - start
        size_kb = len(output.encode("utf-8")) / 1024
        logger.info(
            "Database dump complete — %d tables, %.1f KB, %.2fs → %s",
            len(tables), size_kb, elapsed, self._output_path,
        )


# ── Singleton ────────────────────────────────────────────────────

_unpacker: DatabaseUnpacker | None = None


def get_unpacker(db_path: str | Path | None = None) -> DatabaseUnpacker:
    """Return the module-level unpacker singleton, creating it on first call."""
    global _unpacker
    if _unpacker is None:
        if db_path is None:
            raise ValueError("db_path required for first call to get_unpacker()")
        _unpacker = DatabaseUnpacker(db_path)
    return _unpacker
