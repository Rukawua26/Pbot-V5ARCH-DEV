import fcntl
import os
import sys

_single_instance_lock = None


def acquire_single_instance_lock(logger, lock_filename: str = ".sniperai.lock") -> bool:
    global _single_instance_lock

    lock_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), lock_filename)
    lock_file = open(lock_path, "a+", encoding="utf-8")

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.seek(0)
        owner = lock_file.read().strip() or "desconocido"
        msg = (
            f"🚫 Ya existe otra instancia ejecutándose (lock owner: {owner}). "
            "Abortando para proteger API/riesgo operativo."
        )
        logger.error(msg)
        print(msg, file=sys.stderr)
        lock_file.close()
        return False

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    _single_instance_lock = lock_file
    return True
