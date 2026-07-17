import sys
import builtins
from datetime import datetime, timezone

def _timestamp():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

def print(*args, **kwargs):
    builtins.print(*args, flush=True, **kwargs)

def print_stderr(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def _format_log_message(message, level=None):
    prefix = f"[{_timestamp()}] "
    if level is not None:
        prefix += f"[{level}] "
    indent = " " * len(prefix)

    message = str(message)
    message = message.replace("\r\n", "\n").replace("\r", "\n")
    message = message.replace("\n", "\n" + indent)

    return prefix + message

def log(message, **kwargs):
    print(_format_log_message(message), **kwargs)

def log_warn(message, **kwargs):
    print_stderr(_format_log_message(message, "WARN"), **kwargs)

def log_error(message, **kwargs):
    print_stderr(_format_log_message(message, "ERROR"), **kwargs)