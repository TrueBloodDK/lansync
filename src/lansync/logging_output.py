"""Rotating transcript of application output, preserving terminal streams."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import logging
from logging.handlers import RotatingFileHandler


class Tee:
    def __init__(self, stream, logger, secrets):
        self.stream, self.logger, self.secrets = stream, logger, secrets
        self.pending = ''

    def write(self, text):
        result = self.stream.write(text)
        self.pending += text
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            self.record(line)
        return result

    def record(self, line):
        for secret in self.secrets:
            line = line.replace(secret, '[REDACTED]')
        self.logger.info(line)

    def flush(self):
        self.stream.flush()
        if self.pending:
            self.record(self.pending)
            self.pending = ''

    def isatty(self):
        return False


@contextmanager
def transcript(path, secrets=()):
    import sys
    # Exclusive creation avoids accidentally appending to user data or credentials.
    with path.open('x', encoding='utf-8'):
        pass
    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger = logging.Logger('lansync.transcript', logging.INFO)
    logger.addHandler(handler)
    stdout, stderr = Tee(sys.stdout, logger, secrets), Tee(sys.stderr, logger, secrets)
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            yield
    finally:
        stdout.flush()
        stderr.flush()
        handler.close()
