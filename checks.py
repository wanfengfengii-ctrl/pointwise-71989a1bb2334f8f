import random
import sys
from fractions import Fraction
from corpus import Corpus
from search import Search

def oracle(rows, terms, k):
    n = len(rows)
    df = {t: sum(t in v for v in rows.values()) for t in terms}
    scores = []
    for ident, tokens in rows.items():
        score = sum((Fraction(n * tokens.count(t), df[t] * (tokens.count(t) + len(tokens)))
                     for t in terms if t in tokens), Fraction())
        if any(t in tokens for t in terms):
            scores.append((ident, score))
    return sorted(scores, key=lambda x: (-x[1], x[0]))[:k]

def correctness():
    rows = {1: ["a", "a", "b"], 2: ["b"], 3: []}
    c = Corpus(rows.items())
    s = Search(c)
    assert s.query(["a", "b"], 2) == [(1, Fraction(63, 40)), (2, Fraction(3, 4))]
    assert s.last_scored == 2
    c.replace(1, ["b"])
    rows[1] = ["b"]
    assert s.query(["a", "b"], 5) == oracle(rows, ["a", "b"], 5)
    c.delete(2)
    del rows[2]
    assert s.query(["missing"], 1) == []
    assert c.read(1) == (1, {"b": 1})
    copy = c.read(1)
    copy[1]["b"] = 999
    assert c.read(1) == (1, {"b": 1})
    before = s.query(["b"], 10)
    for ident, tokens in [(True, ["a"]), (4, [""]), (4, "a"), (4, [None])]:
        try:
            c.replace(ident, tokens)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted invalid edit")
        assert s.query(["b"], 10) == before
    for terms, k in [([], 1), (["b", "b"], 1), ([""], 1), (["a"], True), (["a"], 0)]:
        try:
            s.query(terms, k)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted invalid query")
    rows = {1: ["a", "x", "x", "x"], 2: ["b"], 3: ["b"], 4: ["a"], 5: ["a"]}
    c, s = Corpus(rows.items()), None
    s = Search(c)
    assert s.query(["a", "b"], 1)[0][0] == 2
    for ident in [4, 5]:
        c.delete(ident)
        del rows[ident]
    assert s.query(["a", "b"], 1) == [(2, Fraction(3, 4))]
    c.replace(1, ["a", "a"])
    rows[1] = ["a", "a"]
    assert s.query(["a", "b"], 1)[0][0] == 1
    # Unedited document 10 overtakes 20 solely because df(a) falls.
    rows = {10: ["a"] * 2 + ["x"] * 3, 20: ["b"], 21: ["b"],
            30: ["a"], 31: ["a"], 32: ["a"], 33: ["a"]}
    c = Corpus(rows.items())
    s = Search(c)
    assert s.query(["a", "b"], 1)[0][0] == 20
    for ident in [30, 31, 32, 33]:
        c.delete(ident)
        del rows[ident]
    assert s.query(["a", "b"], 1) == [(10, Fraction(6, 7))]
    assert s.query(["a", "b"], 5) == oracle(rows, ["a", "b"], 5)
    # A balanced document wins without being either term's individual winner.
    c = Corpus([(1, ["a"] * 9 + ["x"]), (2, ["b"] * 9 + ["x"]),
                (3, ["a"] * 5 + ["b"] * 5)])
    s = Search(c)
    assert s.query(["a", "b"], 1) == [(3, Fraction(1, 1))]
    rng = random.Random(911)
    rows = {}
    c = Corpus()
    s = Search(c)
    vocab = ["a", "b", "c", "d", "e", "f"]
    for _ in range(180):
        ident = rng.randrange(-10, 70)
        if rng.randrange(4) == 0:
            rows.pop(ident, None)
            c.delete(ident)
        else:
            tokens = [rng.choice(vocab) for _ in range(rng.randrange(12))]
            rows[ident] = tokens
            c.replace(ident, tokens)
        terms = rng.sample(vocab + ["absent"], rng.randrange(1, 8))
        k = rng.randrange(1, 15)
        assert s.query(terms, k) == oracle(rows, terms, k)
        assert c.n == len(rows)
        for term in vocab:
            assert c.frequency(term) == sum(term in x for x in rows.values())
    print("REGRESSION_OK hand_scores, edits, validation, ties, random")

def demo():
    c = Corpus([(10, ["a"] * 2 + ["x"] * 3), (20, ["b"]),
                (21, ["b"]), (30, ["a"]), (31, ["a"]), (32, ["a"]), (33, ["a"])])
    s = Search(c)
    print("before", s.query(["a", "b"], 2), "df(a)", c.frequency("a"))
    for ident in (30, 31, 32, 33):
        c.delete(ident)
    result = s.query(["a", "b"], 2)
    assert result[0][0] == 10
    print("after ", result, "df(a)", c.frequency("a"), "scored", s.last_scored)

    # Live correction handoff: the score of an *unedited* document rises
    # because a delete lowers df, so the index must follow the current corpus.
    c = Corpus([(1, ["a"] * 2 + ["p"] * 3), (2, ["a"] * 2 + ["p"] * 3),
                (3, ["a"]), (4, ["a"]), (5, ["a"]), (6, ["a"])])
    s = Search(c)
    print("demo df(a)", c.frequency("a"), "top", s.query(["a"], 3),
          "degraded", s.last_degraded, "lists", s.last_lists_read)
    for ident in (3, 4, 5):
        c.delete(ident)
    print("demo df(a)", c.frequency("a"), "top", s.query(["a"], 3),
          "degraded", s.last_degraded, "lists", s.last_lists_read)
    # Local replace: only the edited id's postings move, and the ranking
    # immediately reflects the new length/tf without any rebuild.
    c.replace(1, ["a", "a", "a", "a"])
    print("demo replace 1", "read", c.read(1), "top", s.query(["a"], 3))

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    correctness()
    if mode == "target":
        import verify
        verify.run()          # independent differential + boundary checks
        import bench
        bench.main(True)      # quantification + frozen-reference timing
    elif mode == "demo":
        demo()
    elif mode != "baseline":
        raise SystemExit("expected baseline, target or demo")
