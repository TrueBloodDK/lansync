"""Structured transfer events, throttled independently of terminal rendering."""
from time import monotonic


class TransferProgress:
    def __init__(self, callback, direction, path, total):
        self.callback=callback
        self.direction=direction
        self.path=path
        self.total=total
        self.bytes=0
        self.last=0
        self.emit('started')

    def emit(self, phase):
        if self.callback:
            self.callback({'phase':phase,'direction':self.direction,'path':self.path,
                           'bytes':self.bytes,'total':self.total})
        self.last=monotonic()

    def advance(self, count):
        self.bytes+=count
        if monotonic()-self.last>=.25:
            self.emit('transferring')

    def complete(self):
        self.emit('completed')
