"""Drain both subprocess pipes without mixing fd readiness with text buffering."""

import codecs
import io
import os
import select
from queue import Full, Queue
from threading import Event, Lock, Thread


class ProcessOutputError(RuntimeError):
    """A pipe could not be read; never includes raw provider content."""


class ProcessOutput:
    """Bound queued stdout and retained stderr while continuously draining both."""

    _STDERR_LIMIT = 1_048_576

    def __init__(self, process):
        self._streams = (process.stdout, process.stderr)
        self._stop = Event()
        self._stdout = Queue(maxsize=16)
        self._lock = Lock()
        self._stderr_prefix = ""
        self._stderr_tail = ""
        self.stderr_bytes = 0
        self.stderr_read_failed = False
        self.stderr_complete = Event()
        self.first_stderr_ready = Event()
        self.first_stderr_line = ""
        self._threads = [
            Thread(target=self._read_stdout, args=(process.stdout,), daemon=True),
            Thread(target=self._read_stderr, args=(process.stderr,), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _chunks(self, stream):
        if isinstance(stream, io.TextIOWrapper) and os.name != "nt":
            # Read the fd directly: the TextIOWrapper must not prefetch data that
            # a later select(fd) would incorrectly report as unavailable.
            fd = stream.fileno()
            decoder = io.IncrementalNewlineDecoder(
                codecs.getincrementaldecoder(stream.encoding)(stream.errors), True
            )
            while not self._stop.is_set():
                if not select.select([fd], [], [], 0.1)[0]:
                    continue
                data = os.read(fd, 8192)
                text = decoder.decode(data, final=not data)
                if text:
                    yield text
                if not data:
                    return
        elif isinstance(stream, io.TextIOBase):
            while not self._stop.is_set():
                text = stream.read(8192)
                if not text:
                    return
                yield text
        else:
            # Non-buffered file-like adapters retain their read-all contract.
            text = stream.read()
            if text:
                yield text

    def _put_stdout(self, item):
        while not self._stop.is_set():
            try:
                self._stdout.put(item, timeout=0.1)
                return True
            except Full:
                pass
        return False

    def _read_stdout(self, stream):
        try:
            if isinstance(stream, io.TextIOBase):
                pending = ""
                for chunk in self._chunks(stream):
                    pending += chunk
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        if not self._put_stdout(line + "\n"):
                            return
                if pending:
                    self._put_stdout(pending)
            elif stream is not None:
                while not self._stop.is_set():
                    line = stream.readline()
                    if not line:
                        break
                    if not self._put_stdout(line):
                        return
        except BaseException:
            self._put_stdout(ProcessOutputError("Unable to read provider stdout."))
        finally:
            self._put_stdout(None)

    def _read_stderr(self, stream):
        try:
            if stream is None:
                return
            for chunk in self._chunks(stream):
                with self._lock:
                    self.stderr_bytes += len(chunk.encode("utf-8", errors="replace"))
                    if not self.first_stderr_ready.is_set():
                        self.first_stderr_line += chunk
                        if "\n" in self.first_stderr_line:
                            self.first_stderr_line = self.first_stderr_line.split("\n", 1)[0]
                            self.first_stderr_ready.set()
                        self.first_stderr_line = self.first_stderr_line[:8192]
                    half = self._STDERR_LIMIT // 2
                    remaining = max(0, half - len(self._stderr_prefix))
                    self._stderr_prefix += chunk[:remaining]
                    self._stderr_tail = (self._stderr_tail + chunk[remaining:])[-half:]
        except (OSError, UnicodeError):
            # Keep raw read errors out of persisted diagnostics.
            self.stderr_read_failed = True
            self._put_stdout(ProcessOutputError("Unable to read provider stderr."))
        finally:
            self.first_stderr_ready.set()
            self.stderr_complete.set()

    def readline(self, timeout):
        item = self._stdout.get(timeout=timeout)
        if isinstance(item, BaseException):
            raise item
        return item

    def stderr_text(self):
        self.stderr_complete.wait(timeout=2)
        with self._lock:
            return self._stderr_prefix + self._stderr_tail

    def diagnostics(self):
        with self._lock:
            return {
                "stderr_bytes": self.stderr_bytes,
                "stderr_complete": self.stderr_complete.is_set(),
                "stderr_read_failed": self.stderr_read_failed,
            }

    def close(self):
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=0.5)
        if not any(thread.is_alive() for thread in self._threads):
            for stream in self._streams:
                if isinstance(stream, io.TextIOBase):
                    stream.close()
