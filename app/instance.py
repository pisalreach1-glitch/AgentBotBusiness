from contextlib import contextmanager
from pathlib import Path
import os


@contextmanager
def single_instance(database_path: str):
    """Prevent two polling processes from sharing this database accidentally."""
    path = Path(str(database_path) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("This database is already in use by another bot process.") from exc
        yield
    finally:
        # Closing the handle releases the OS lock, including after an exception.
        handle.close()
