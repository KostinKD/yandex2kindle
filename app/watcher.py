"""Background watcher for incoming/ directory with write-stabilization check."""
import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path

from . import main

logger = logging.getLogger("uvicorn.error")


async def scan_incoming_once() -> list[dict]:
    """Helper to scan and import stable files once."""
    incoming = main.INCOMING
    incoming.mkdir(parents=True, exist_ok=True)
    results = []
    for p in sorted(incoming.iterdir()):
        if p.is_file() and p.suffix.lower() in main.SUPPORTED:
            try:
                res = await asyncio.to_thread(main.add_file, p)
                p.unlink(missing_ok=True)
                results.append({"file": p.name, **res})
            except Exception as exc:
                results.append({"file": p.name, "status": "error", "message": str(exc)})
    return results


async def watch_incoming_loop():
    try:
        interval = int(os.environ.get("INCOMING_WATCH_INTERVAL_SECONDS", "5"))
    except (ValueError, TypeError):
        interval = 5

    if interval <= 0:
        logger.info("Incoming folder watcher disabled (INCOMING_WATCH_INTERVAL_SECONDS <= 0)")
        return

    logger.info("Incoming folder watcher started (interval=%ss)", interval)
    file_states: dict[Path, tuple[int, float]] = {}

    while True:
        try:
            incoming = main.INCOMING
            if incoming.is_dir():
                candidates = [p for p in incoming.iterdir() if p.is_file() and p.suffix.lower() in main.SUPPORTED]
                current_paths = set(candidates)

                # Clean deleted files
                for p in list(file_states.keys()):
                    if p not in current_paths:
                        file_states.pop(p, None)

                for p in candidates:
                    try:
                        stat = p.stat()
                        size, mtime = stat.st_size, stat.st_mtime
                    except OSError:
                        continue

                    if size == 0:
                        continue

                    prev = file_states.get(p)
                    # File size and modification time must be unchanged between two intervals
                    if prev and prev[0] == size and prev[1] == mtime:
                        file_states.pop(p, None)
                        try:
                            logger.info("Auto-importing incoming file: %s (%s bytes)", p.name, size)
                            result = await asyncio.to_thread(main.add_file, p)
                            p.unlink(missing_ok=True)
                            logger.info("Auto-imported %s: status=%s id=%s", p.name, result.get("status"), result.get("id"))
                        except Exception as exc:
                            logger.warning("Auto-import failed for %s: %s", p.name, exc)
                    else:
                        file_states[p] = (size, mtime)
        except Exception as exc:
            logger.warning("Incoming watcher error: %s", exc)

        await asyncio.sleep(interval)
