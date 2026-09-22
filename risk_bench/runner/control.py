"""Serialize journal edits and worker controls within the API server process."""

from pathlib import Path
from threading import Lock, RLock
from weakref import WeakValueDictionary

_locks: WeakValueDictionary[Path, RLock] = WeakValueDictionary()
_registry_lock = Lock()


def journal_control_lock(journal: Path) -> RLock:
    """Share a reentrant lock across a game's read/check/write control operations.

    The API owns its workers in one process. Hold this lock through the running check
    and worker registration, so an edit cannot slip between checking and starting a
    worker. Workers themselves do not acquire it; their lifetime is tracked by the
    process manager. Reentrancy lets the context writer also protect direct callers.
    """
    key = journal.resolve()
    with _registry_lock:
        lock = _locks.get(key)
        if lock is None:
            lock = RLock()
            _locks[key] = lock
        return lock
