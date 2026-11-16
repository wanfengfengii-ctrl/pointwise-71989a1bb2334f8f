"""Correct full scan. This module is the optimization entry point."""
from fractions import Fraction
from heapq import nlargest

def validate(terms, k):
    if (not isinstance(terms, (list, tuple)) or not 1 <= len(terms) <= 8
            or any(not isinstance(t, str) or not t for t in terms)
            or len(set(terms)) != len(terms)
            or isinstance(k, bool) or not isinstance(k, int) or k <= 0):
        raise ValueError("invalid query")

class Search:
    def __init__(self, corpus):
        self.corpus = corpus
        self.last_scored = 0

    def query(self, terms, k):
        validate(terms, k)
        c = self.corpus
        cache = {}
        result = []
        self.last_scored = 0
        for ident, (length, tf) in c._rows.items():
            score = None
            for term in terms:
                f = tf.get(term, 0)
                if f:
                    key = term, f, length
                    part = cache.get(key)
                    if part is None:
                        part = Fraction(c.n * f, c.frequency(term) * (f + length))
                        cache[key] = part
                    score = part if score is None else score + part
            if score is not None:
                self.last_scored += 1
                result.append((ident, score))
        return nlargest(k, result, key=lambda item: (item[1], -item[0]))
