"""Private, bounded diagnostic metadata. No exception bodies or request payloads."""
import json
import os
from pathlib import Path
import re
import stat
import threading

MAX_LOG_BYTES = 1024 * 1024
MAX_EVENT_BYTES = 16 * 1024
_EXCEPTION_TYPES = frozenset({
    'AssertionError', 'RuntimeError', 'ValueError', 'TypeError', 'KeyError', 'IndexError',
    'AttributeError', 'NameError', 'UnboundLocalError', 'OSError', 'FileNotFoundError',
    'PermissionError', 'ConnectionError', 'ConnectionRefusedError', 'ConnectionResetError',
    'BrokenPipeError', 'TimeoutError', 'NotImplementedError', 'OverflowError', 'MemoryError',
    'ImportError', 'ModuleNotFoundError', 'UnicodeError', 'UnicodeDecodeError', 'UnicodeEncodeError',
    'JSONDecodeError', 'InvalidStateError', 'HTTPError', 'ConnectError', 'ReadError', 'WriteError',
    'RemoteProtocolError', 'LocalProtocolError', 'ReadTimeout', 'WriteTimeout', 'ConnectTimeout',
    'PoolTimeout', 'HTTPStatusError', 'SSLError', 'ProcessLookupError', 'ChildProcessError',
    'RuntimeWarning', 'UnexpectedError',
})
_FILE = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}\.py\Z')
_FUNCTION = re.compile(r'(?:[A-Za-z_][A-Za-z0-9_]{0,63}|<module>|<lambda>)\Z')


class DiagnosticsUnavailable(Exception):
    """Unsafe or unavailable diagnostic storage; never contains an input path."""


def sanitize_event(value):
    if not isinstance(value, dict) or value.get('event') != 'unexpected_backend_exception':
        return None
    identifier = value.get('diagnostic_id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
        return None
    exception = value.get('exception_type')
    if not isinstance(exception, str) or exception not in _EXCEPTION_TYPES:
        exception = 'UnexpectedError'
    frames = []
    for frame in (value.get('stack') if isinstance(value.get('stack'), list) else [])[-8:]:
        if not isinstance(frame, dict):
            continue
        file, function, line = frame.get('file'), frame.get('function'), frame.get('line')
        if (isinstance(file, str) and _FILE.fullmatch(file)
                and isinstance(function, str) and _FUNCTION.fullmatch(function)
                and isinstance(line, int) and not isinstance(line, bool) and 0 < line <= 10_000_000):
            frames.append({'file': file, 'line': line, 'function': function})
    return {'event': 'unexpected_backend_exception', 'diagnostic_id': identifier,
            'exception_type': exception, 'stack': frames}


def _private_directory(path, *, create):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise DiagnosticsUnavailable()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise DiagnosticsUnavailable()
        if create:
            os.fchmod(descriptor, 0o700)
        elif stat.S_IMODE(info.st_mode) != 0o700:
            raise DiagnosticsUnavailable()
        result, descriptor = descriptor, None
        return result
    except OSError as exc:
        raise DiagnosticsUnavailable() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _regular(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)


class MetadataDiagnostics:
    def __init__(self, state_dir, *, max_bytes=MAX_LOG_BYTES):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 256 <= max_bytes <= MAX_LOG_BYTES:
            raise ValueError('Invalid diagnostic size limit')
        state = Path(state_dir).absolute()
        if any(part.lower().endswith('.app') for part in state.parts):
            raise DiagnosticsUnavailable()
        descriptor = _private_directory(state, create=True)
        os.close(descriptor)
        self.directory = state / 'logs'
        descriptor = _private_directory(self.directory, create=True)
        os.close(descriptor)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def append(self, value):
        event = sanitize_event(value)
        if event is None:
            return False
        data = (json.dumps(event, separators=(',', ':'), ensure_ascii=True) + '\n').encode()
        if len(data) > min(self.max_bytes, MAX_EVENT_BYTES):
            return False
        with self._lock:
            directory = descriptor = None
            try:
                directory = _private_directory(self.directory, create=False)
                existing = {}
                for name in ('diagnostics.jsonl', 'diagnostics.jsonl.1', 'diagnostics.jsonl.2'):
                    try:
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if not _regular(info):
                        raise DiagnosticsUnavailable()
                    if info.st_size > self.max_bytes:
                        repair = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                        try:
                            if not _regular(os.fstat(repair)):
                                raise DiagnosticsUnavailable()
                            os.ftruncate(repair, 0)
                        finally:
                            os.close(repair)
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    existing[name] = info
                current = existing.get('diagnostics.jsonl')
                if current and current.st_size + len(data) > self.max_bytes:
                    if 'diagnostics.jsonl.1' in existing:
                        os.replace('diagnostics.jsonl.1', 'diagnostics.jsonl.2', src_dir_fd=directory, dst_dir_fd=directory)
                    os.replace('diagnostics.jsonl', 'diagnostics.jsonl.1', src_dir_fd=directory, dst_dir_fd=directory)
                descriptor = os.open('diagnostics.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     0o600, dir_fd=directory)
                if not _regular(os.fstat(descriptor)):
                    raise DiagnosticsUnavailable()
                view = memoryview(data)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise DiagnosticsUnavailable()
                    view = view[written:]
                return True
            except (OSError, DiagnosticsUnavailable):
                return False
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if directory is not None:
                    os.close(directory)
