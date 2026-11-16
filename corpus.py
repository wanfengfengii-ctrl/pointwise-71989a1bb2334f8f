"""Mutable current corpus; edits are serial, validated before publication."""
from collections import Counter

def valid_id(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("document ID must be an integer")

def counts(tokens):
    if not isinstance(tokens, (list, tuple)):
        raise ValueError("tokens must be a list or tuple")
    if any(not isinstance(t, str) or not t for t in tokens):
        raise ValueError("terms must be nonempty strings")
    return len(tokens), dict(Counter(tokens))

class Corpus:
    def __init__(self, documents=()):
        self._rows = {}
        self._df = {}
        self.version = 0
        for ident, tokens in documents:
            self.replace(ident, tokens)

    @property
    def n(self):
        return len(self._rows)

    def frequency(self, term):
        return self._df.get(term, 0)

    def read(self, ident):
        valid_id(ident)
        row = self._rows.get(ident)
        return None if row is None else (row[0], dict(row[1]))

    def replace(self, ident, tokens):
        valid_id(ident)
        new = counts(tokens)
        old = self._rows.get(ident)
        if old is not None:
            for term in old[1]:
                self._df[term] -= 1
                if not self._df[term]:
                    del self._df[term]
        self._rows[ident] = new
        for term in new[1]:
            self._df[term] = self._df.get(term, 0) + 1
        self.version += 1

    def delete(self, ident):
        valid_id(ident)
        old = self._rows.pop(ident, None)
        if old is None:
            return
        for term in old[1]:
            self._df[term] -= 1
            if not self._df[term]:
                del self._df[term]
        self.version += 1
