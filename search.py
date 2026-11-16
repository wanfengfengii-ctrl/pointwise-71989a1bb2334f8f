"""Exact top-K relevance search over the current corpus.

Scoring is the reference model, unchanged: for each matching query term a
document contributes

    N / df(term) * tf / (tf + L)        (an exact Fraction)

and the sum is ranked by score descending, then ID ascending.

Index and local handoff
-----------------------
Every term owns its posting list ``id -> (tf, length)`` and a valid upper bound
on ``tf/(tf+L)``.  Weights ``N/df`` are read from the live corpus at query time,
so they always reflect the current ``N`` and ``df``.  ``Search`` registers as a
listener on the corpus: each committed ``replace``/``delete`` hands the index
exactly the affected id plus the old and new ``(length, tf)`` rows.  The index
therefore never diffs or scans the corpus to discover a change, and it is never
rebuilt in bulk after construction.  Deleting the last supporter removes the
term's posting list entirely, so deletions leave no residue.

Query: progressive coverage with a current safety bound
-------------------------------------------------------
Let ``R`` be the query terms whose lists have already been read.  A document
that was not met in any read list can only be matched through a term outside
``R``, so its score is at most

    total = sum(weight(t) * max_ratio(t)  for t not in R)

where ``max_ratio(t)`` is the exact maximum of ``tf/(tf+L)`` over the term's
*current* postings.  It is raised on insertion; if the document attaining the
maximum is deleted or replaced, only that term's bound is recomputed locally
(``_Term.tighten``).  It therefore tracks the current corpus and never
underestimates.

Terms are read in ascending df order.  Every document met in a read list is
scored exactly against *all* query terms via posting lookups, so candidates
carry complete scores.  Reading stops as soon as the k-th best exact candidate
strictly exceeds ``total``; every unread document is then provably ranked
below it and the exact answer is known.  If fewer than k candidates exist, or
the bound cannot yet exclude the unread lists, the next list is read: fallback
is driven by the current bound, never by a fixed full scan, and
``last_degraded`` records the trigger (``None`` means the bound proved the
answer, or every list was exhausted so nothing remained unread);
``last_lists_read`` records how many lists were read.  ``last_scored`` is the
true number of documents whose complete score was computed.
"""
from fractions import Fraction
from heapq import nlargest


def validate(terms, k):
    if (not isinstance(terms, (list, tuple)) or not 1 <= len(terms) <= 8
            or any(not isinstance(t, str) or not t for t in terms)
            or len(set(terms)) != len(terms)
            or isinstance(k, bool) or not isinstance(k, int) or k <= 0):
        raise ValueError("invalid query")


class _Term:
    """Current postings and global state of one term."""

    __slots__ = ("postings", "df", "holder", "ratio_num", "ratio_den")

    def __init__(self):
        self.postings = {}   # id -> (tf, length)
        self.df = 0          # current number of documents containing the term
        self.holder = None   # id that currently attains max tf/(tf+L)
        self.ratio_num = 0   # bound of tf/(tf+L) as ratio_num / ratio_den
        self.ratio_den = 1

    def observe(self, ident, f, length):
        """Raise the bound to cover f/(f+L) of a just-inserted posting."""
        if f * self.ratio_den > self.ratio_num * (f + length):
            self.ratio_num = f
            self.ratio_den = f + length
            self.holder = ident

    def tighten(self):
        """Recompute the bound from the current postings (holder vanished).

        Called only when the document that attained the maximum is removed,
        so it is a local repair of one term, never a corpus-wide rescale.
        """
        best_num, best_den, holder = 0, 1, None
        for ident, (f, length) in self.postings.items():
            if f * best_den > best_num * (f + length):
                best_num, best_den, holder = f, f + length, ident
        self.ratio_num, self.ratio_den, self.holder = best_num, best_den, holder


class Search:
    def __init__(self, corpus):
        self.corpus = corpus
        self.last_scored = 0
        self.last_degraded = None
        self.last_lists_read = 0
        self._terms = {}
        corpus._attach(self)

    # ------------------------------------------------------------------
    # index maintenance: one snapshot, then local handoff per edit
    # ------------------------------------------------------------------
    def _rebuild(self, corpus):
        terms = {}
        for ident, (length, tf) in corpus._rows.items():
            for term, f in tf.items():
                term_index = terms.get(term)
                if term_index is None:
                    term_index = terms[term] = _Term()
                term_index.postings[ident] = (f, length)
                term_index.df += 1
                term_index.observe(ident, f, length)
        self._terms = terms

    def _apply(self, ident, old, new):
        """Local handoff of one committed edit; old/new are rows or None."""
        changed = []
        if old is not None:
            for term in old[1]:
                term_index = self._terms[term]
                del term_index.postings[ident]
                term_index.df -= 1
                if term_index.holder == ident:
                    # The max-ratio witness left: repair this term locally.
                    term_index.tighten()
                changed.append(term)
        if new is not None:
            length, tf = new
            for term, f in tf.items():
                term_index = self._terms.get(term)
                if term_index is None:
                    term_index = self._terms[term] = _Term()
                term_index.postings[ident] = (f, length)
                term_index.df += 1
                term_index.observe(ident, f, length)
                changed.append(term)
        for term in changed:
            if not self._terms[term].df:
                del self._terms[term]   # last supporter gone: no residue

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------
    @staticmethod
    def _exact(weights, ident):
        score = Fraction(0)
        for term_index, weight in weights:
            cell = term_index.postings.get(ident)
            if cell is not None:
                f, length = cell
                score += weight * Fraction(f, f + length)
        return score

    def query(self, terms, k):
        validate(terms, k)
        self.last_scored = 0
        self.last_degraded = None
        self.last_lists_read = 0
        live = []
        for term in terms:
            term_index = self._terms.get(term)
            if term_index is not None:
                live.append(term_index)
        if not live:
            return []
        live.sort(key=lambda term_index: term_index.df)

        # Current weights and the still-open envelope, from the live corpus.
        n = self.corpus.n
        weights = [(term_index, Fraction(n, term_index.df)) for term_index in live]
        bound = [weight * Fraction(term_index.ratio_num, term_index.ratio_den)
                 for term_index, weight in weights]
        remaining = sum(bound, Fraction(0))

        candidates = {}
        # Why a list beyond the shortest was read: "fewer_than_k_candidates" if
        # the exact candidate set was too small, "bound_inconclusive" if the
        # open envelope could not exclude the unread lists.  None means the
        # safety bound proved the answer (or every list was read, leaving no
        # unread document at all).
        reason = None
        lists_read = 0
        for position, (term_index, weight) in enumerate(weights):
            for ident in term_index.postings:
                if ident not in candidates:
                    candidates[ident] = self._exact(weights, ident)
            lists_read += 1
            remaining -= bound[position]
            if len(candidates) >= k and nlargest(k, candidates.values())[-1] > remaining:
                break                       # bound proves the answer
            if position + 1 == len(weights):
                break                       # nothing unread: exact as well
            if len(candidates) < k:
                reason = "fewer_than_k_candidates"
            else:
                reason = "bound_inconclusive"

        self.last_scored = len(candidates)
        self.last_degraded = reason
        self.last_lists_read = lists_read
        ranked = [(-score, ident) for ident, score in candidates.items()]
        ranked.sort()
        return [(ident, -neg) for neg, ident in ranked[:k]]
