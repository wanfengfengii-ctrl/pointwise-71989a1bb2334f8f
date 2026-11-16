"""Mutable current corpus; validated edits are published with a local handoff.

Public behaviour is unchanged from the frozen reference.  Each successful
``replace``/``delete`` additionally pushes the pair of affected rows to the
attached search index, so the index never has to diff or scan the corpus to
discover what changed.  The push happens after the corpus state (rows, df and
version) has been updated, so a listener always observes the current state.
"""
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
        self._listeners = []
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

    # ---- internal cooperation with the search index --------------------
    def _attach(self, listener):
        """Give a freshly built index the current snapshot, then publish edits."""
        listener._rebuild(self)
        self._listeners.append(listener)

    @staticmethod
    def _drop_df(df, terms):
        for term in terms:
            remaining = df[term] - 1
            if remaining:
                df[term] = remaining
            else:
                del df[term]

    @staticmethod
    def _add_df(df, terms):
        for term in terms:
            df[term] = df.get(term, 0) + 1

    def _publish(self, ident, old, new):
        for listener in self._listeners:
            listener._apply(ident, old, new)

    def replace(self, ident, tokens):
        valid_id(ident)
        new = counts(tokens)
        old = self._rows.get(ident)
        if old is not None:
            self._drop_df(self._df, old[1])
        self._rows[ident] = new
        self._add_df(self._df, new[1])
        self.version += 1
        self._publish(ident, old, new)

    def delete(self, ident):
        valid_id(ident)
        old = self._rows.pop(ident, None)
        if old is None:
            return
        self._drop_df(self._df, old[1])
        self.version += 1
        self._publish(ident, old, None)
