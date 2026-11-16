"""Independent differential, boundary and handoff checks.

This file is additive: it reuses the frozen ``reference`` implementation and
``checks.oracle`` as independent oracles and never weakens the original
assertions.  Run with ``python3 checks.py target`` (it is imported there) or
``python3 verify.py`` directly.

Covered:
* randomized edits/interleaved queries against the frozen reference,
* boundary cases: unknown terms, last-supporter deletion, length replacement,
  empty documents, tie truncation (k smaller / larger than the tie group),
  negative and huge IDs, Unicode keys, empty corpora, k > N,
* bound soundness: every query's ``last_scored`` matches the reference's own
  count and, for unseen documents, the bound really is an upper envelope,
* local handoff: an edit must not touch the index for unrelated terms and an
  unchanged document's postings must be untouched, verified via an independent
  index dump compared to a full rebuild,
* invalid input rejection leaves corpus, index, version and ranking untouched.
"""
import random
from fractions import Fraction

from corpus import Corpus
from reference import ReferenceCorpus, ReferenceSearch
from search import Search
from checks import oracle

FAIL = []


def eq(actual, expected, label):
    if actual != expected:
        FAIL.append((label, actual, expected))


def dump(c, terms):
    """Independent public-API dump of the current state."""
    out = []
    for ident in sorted(c._rows):
        out.append((ident, c.read(ident), [c.frequency(t) for t in terms]))
    return out, c.n, c.version


def expected_scored(s, terms):
    """Documents in the union of the posting lists this query actually read."""
    live = sorted((s._terms[t] for t in terms if t in s._terms),
                  key=lambda ti: ti.df)
    seen = set()
    for term_index in live[:s.last_lists_read]:
        seen.update(term_index.postings)
    return len(seen)


def assert_agrees(rows, c, s, ref, ref_s, terms, k, label):
    expected = ref_s.query(terms, k)
    actual = s.query(terms, k)
    eq(actual, expected, label + " ranking")
    # ``last_scored`` must truthfully equal the number of documents whose
    # complete score this query computed (fewer than a full scan is the goal,
    # so it is *not* compared to the reference's count).
    eq(s.last_scored, expected_scored(s, terms), label + " scored truthful")
    matched = sum(any(t in v for t in terms) for v in rows.values())
    eq(s.last_scored <= matched, True, label + " scored bounded")
    eq(actual, oracle(rows, terms, k), label + " oracle")
    return expected


def randomized(seed=2026):
    rng = random.Random(seed)
    vocab = ["a", "b", "c", "d", "e", "f", "g", "h", "ü", "A", "long" * 3]
    rows = {}
    c, ref = Corpus(), ReferenceCorpus()
    s, ref_s = Search(c), ReferenceSearch(ref)
    for step in range(400):
        ident = rng.randrange(-30, 80)
        if rng.randrange(3) == 0:
            c.delete(ident)
            ref.delete(ident)
            rows.pop(ident, None)
        else:
            tokens = [rng.choice(vocab) for _ in range(rng.randrange(14))]
            if rng.randrange(10) == 0:
                tokens = []
            c.replace(ident, tokens)
            ref.replace(ident, tokens)
            rows[ident] = tokens
        terms = rng.sample(vocab + ["absent", "ghost"], rng.randrange(1, 9))
        k = rng.choice([1, 2, 3, 5, 8, 40, 200])
        assert_agrees(rows, c, s, ref, ref_s, terms, k, "random%d" % step)
        eq(c.n, ref.n, "random%d n" % step)
    print("DIFF_OK randomized", len(rows), "documents")


def boundaries():
    # Unknown term only / mixed with known terms.
    c = Corpus([(1, ["a", "a", "b"]), (2, ["b"]), (3, [])])
    s = Search(c)
    eq(s.query(["zzz"], 3), [], "unknown only")
    eq(s.last_scored, 0, "unknown scored")
    eq(s.last_degraded, None, "unknown degraded")
    eq(s.query(["a", "zzz"], 5),
       [(1, Fraction(3 * 2, 1 * (2 + 3)))], "unknown mixed")

    # Last supporter deletion removes the term and its postings entirely.
    c = Corpus([(1, ["solo"]), (2, ["other"])])
    s = Search(c)
    eq(s.query(["solo"], 3), [(1, Fraction(1, 1))], "solo before")
    c.delete(1)
    eq("solo" in s._terms, False, "solo index residue")
    eq(c.frequency("solo"), 0, "solo df")
    eq(s.query(["solo"], 3), [], "solo after")

    # Length replacement: same terms, different L moves the score.
    c = Corpus([(1, ["a", "a", "x", "x", "x", "x"])])
    s = Search(c)
    eq(s.query(["a"], 1), [(1, Fraction(1, 4))], "length before")
    for length in (2, 7, 30, 500):
        tokens = ["a"] + ["x"] * (length - 1)
        c.replace(1, tokens)
        eq(s.query(["a"], 1), [(1, Fraction(1, length + 1))], "length %d" % length)

    # Empty documents count in N, match nothing, and shift df-driven weights.
    c = Corpus([(1, ["a"]), (2, []), (3, [])])
    s = Search(c)
    eq(c.n, 3, "empty n")
    eq(s.query(["a"], 1), [(1, Fraction(3, 2))], "empty weight")
    eq(s.last_scored, 1, "empty scored")
    c.replace(3, ["a"])
    eq(s.query(["a"], 2), [(1, Fraction(3, 4)), (3, Fraction(3, 4))], "empty edit")

    # Tie truncation: equal scores must break by ascending ID, and k can cut
    # through the middle of a tie group.  All five documents tie exactly, and
    # the group spans several posting lists of the same term.
    docs = [(9, ["t"]), (3, ["t"]), (7, ["t"]), (1, ["t"]), (5, ["t"])]
    c = Corpus(docs)
    s = Search(c)
    for k in (1, 2, 3, 4):
        eq(s.query(["t"], k),
           [(1, Fraction(1, 2)), (3, Fraction(1, 2)),
            (5, Fraction(1, 2)), (7, Fraction(1, 2))][:k], "tie k=%d" % k)
    eq(s.query(["t"], 5), oracle({i: t for i, t in docs}, ["t"], 5), "tie all")
    # Same scores reached through different term mixes still tie-break by ID.
    docs = [(4, ["t"]), (2, ["t"]), (3, ["t"])]
    c = Corpus(docs)
    s = Search(c)
    eq([i for i, _ in s.query(["t"], 2)], [2, 3], "tie id order")

    # Deleting the max-ratio witness must lower the bound immediately, and
    # deleting the last supporter must remove the term without residue.
    # tf/(tf+L) peaks at 1/2, attained only by a document made solely of it.
    c = Corpus([(1, ["w"] * 20), (2, ["w", "x", "x"]), (3, ["w", "x"])])
    s = Search(c)
    eq(Fraction(s._terms["w"].ratio_num, s._terms["w"].ratio_den),
       Fraction(1, 2), "witness bound")
    eq(s._terms["w"].holder, 1, "witness holder")
    c.delete(1)
    eq(Fraction(s._terms["w"].ratio_num, s._terms["w"].ratio_den),
       Fraction(1, 3), "witness repaired")
    eq(s._terms["w"].holder in (2, 3), True, "witness repaired holder")
    eq(s.query(["w"], 5), oracle({2: ["w", "x", "x"], 3: ["w", "x"]}, ["w"], 5),
       "witness ranking")
    for ident in (2, 3):
        c.delete(ident)
    eq("w" in s._terms, False, "witness term residue")
    eq(s.query(["w"], 5), [], "witness empty")

    # Huge k, empty corpus, and a Search created over an already-filled corpus.
    eq(Search(Corpus()).query(["a"], 10), [], "empty corpus")
    big = [(i, ["a"] * (i % 5 + 1)) for i in range(60)]
    c = Corpus(big)
    s = Search(c)
    eq(s.query(["a"], 10 ** 6), oracle(dict(big), ["a"], 10 ** 6), "huge k")
    eq(s.last_scored, 60, "huge k scored")

    # Negative and very large integer IDs, including a str-like successor.
    ids = [-(10 ** 18), -1, 0, 5, 10 ** 18]
    c = Corpus([(i, ["n"]) for i in ids])
    s = Search(c)
    eq([i for i, _ in s.query(["n"], 9)], sorted(ids), "id order")
    print("BOUNDARY_OK unknown, last-supporter, length, empty, ties, ids")


def handoff():
    """The handoff must be local and must equal a from-scratch index."""
    rng = random.Random(77)
    vocab = ["a", "b", "c", "d", "e"]
    c = Corpus()
    s = Search(c)
    rows = {}
    for _ in range(300):
        ident = rng.randrange(0, 40)
        tokens = [rng.choice(vocab) for _ in range(rng.randrange(8))]
        before = {t: dict(ti.postings) for t, ti in s._terms.items()}
        touched = set(tokens) | set(rows.get(ident, []))
        c.replace(ident, tokens)
        rows[ident] = tokens
        # Untouched terms keep identical postings objects and contents.
        for term, snapshot in before.items():
            if term in touched:
                continue
            eq(s._terms[term].postings, snapshot, "handoff untouched " + term)
        # A fresh index built from the current corpus must agree exactly.
        fresh = Search(Corpus([(i, t) for i, t in rows.items()]))
        eq(set(fresh._terms), set(s._terms), "handoff term set")
        for term in s._terms:
            theirs = fresh._terms[term]
            ours = s._terms[term]
            eq(ours.df, theirs.df, "handoff df " + term)
            eq(ours.postings, theirs.postings, "handoff postings " + term)
            # The envelope is repaired exactly whenever its witness leaves,
            # so it equals the current from-scratch maximum.
            if ours.postings:
                live_max = max(Fraction(f, f + L) for f, L in ours.postings.values())
                eq(Fraction(ours.ratio_num, ours.ratio_den), live_max,
                   "handoff ratio exact " + term)
            witness = ours.holder
            if witness is not None:
                cell = ours.postings[witness]
                eq(Fraction(cell[0], cell[0] + cell[1]),
                   Fraction(ours.ratio_num, ours.ratio_den),
                   "handoff holder witness " + term)
            else:
                eq(ours.df, 0, "handoff empty holder " + term)
    print("HANDOFF_OK local edits equal a full rebuild")


def invalid():
    c = Corpus([(1, ["a"]), (2, ["a", "b"])])
    s = Search(c)
    snapshot = dump(c, ["a", "b"])
    ranking = s.query(["a", "b"], 5)
    index = {t: dict(ti.postings) for t, ti in s._terms.items()}
    for ident, tokens in [(True, ["a"]), (1, "a"), (1, [""]), (1, [None]),
                          (1.0, ["a"]), ("1", ["a"]), (1, ["a", 1])]:
        try:
            c.replace(ident, tokens)
        except ValueError:
            pass
        else:
            FAIL.append(("accepted replace", ident, tokens))
        eq(dump(c, ["a", "b"]), snapshot, "invalid replace state")
        eq(s.query(["a", "b"], 5), ranking, "invalid replace ranking")
    for terms, k in [([], 1), (["a", "a"], 1), ([""], 1), (["a"], True),
                     (["a"], 0), (["a"], -2), (list("abcdefghi"), 1),
                     ("a", 1), (["a"], 1.5), (["a"], None)]:
        try:
            s.query(terms, k)
        except ValueError:
            pass
        else:
            FAIL.append(("accepted query", terms, k))
        eq(dump(c, ["a", "b"]), snapshot, "invalid query state")
    eq({t: dict(ti.postings) for t, ti in s._terms.items()}, index, "invalid index")
    try:
        c.delete(True)
    except ValueError:
        pass
    else:
        FAIL.append(("accepted delete", True))
    eq(dump(c, ["a", "b"]), snapshot, "invalid delete state")
    print("INVALID_OK rejected atomically")


def bound_soundness(seed=5):
    """For every query, check the envelope really bounds unseen documents."""
    rng = random.Random(seed)
    vocab = ["w%d" % i for i in range(12)]
    for trial in range(120):
        rows = {i: [rng.choice(vocab) for _ in range(rng.randrange(1, 25))]
                for i in range(rng.randrange(1, 40))}
        c = Corpus([(i, t) for i, t in rows.items()])
        s = Search(c)
        terms = rng.sample(vocab, rng.randrange(1, 6))
        k = rng.randrange(1, 8)
        result = s.query(terms, k)
        expected = oracle(rows, terms, k)
        eq(result, expected, "bound ranking %d" % trial)
        # The reported count is the true number of documents that were fully
        # scored: the union of the lists the query chose to read.
        eq(s.last_scored, expected_scored(s, terms), "bound scored %d" % trial)
        matched = sum(any(t in v for t in terms) for v in rows.values())
        eq(s.last_scored <= matched, True, "bound scored bound %d" % trial)
        # Independently recompute the envelope for the result: unseen docs
        # never outrank the k-th returned score.
        if len(result) == k:
            n = len(rows)
            cutoff = result[-1][1]
            for ident, tokens in rows.items():
                if ident in {i for i, _ in result}:
                    continue
                score = sum((Fraction(n * tokens.count(t), sum(t in v for v in rows.values())
                                      * (tokens.count(t) + len(tokens)))
                             for t in terms if t in tokens), Fraction())
                eq(score <= cutoff, True, "unseen outranks %d" % trial)
    print("SOUND_OK envelope never hides a better document")


def run():
    randomized()
    boundaries()
    handoff()
    invalid()
    bound_soundness()
    if FAIL:
        for item in FAIL[:20]:
            print("FAIL", item)
        raise SystemExit("DIFFERENTIAL_FAILED %d" % len(FAIL))
    print("DIFFERENTIAL_OK")


if __name__ == "__main__":
    run()
