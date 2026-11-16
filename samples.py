"""Deterministic streaming fixtures. No network or downloaded corpus."""
def documents(kind, size=60000):
    if kind not in ("mixed", "df_jump"):
        raise ValueError(kind)
    for i in range(size):
        terms = ["common"] * (1 + i % 17) + ["noise" + str(i)] + ["padding"] * (i // 17 % 29)
        if i < 2000:
            terms += ["r" + str(i % 40)] * (1 + i % 3)
        if kind == "df_jump" and 3000 <= i < 3010:
            terms += ["boost"] + ["padding"] * 30
        if kind == "df_jump" and 4000 <= i < 4050:
            terms += ["r0"] * 3
        yield i, terms

def plan(kind):
    rounds = []
    for i in range(10):
        if kind == "mixed":
            op = ("replace", 100 + i, ["common", "r" + str(i), "r" + str(i)])
            queries = [
                (["common", "r" + str((4 * i + j) % 40)], 5)
                for j in range(4)]
        elif kind == "df_jump":
            op = ("delete", 3000 + i) if i < 9 else (
                "replace", 4500, ["common", "r0", "r0"])
            queries = [
                (["common", "boost", "r" + str(j)], 5)
                for j in range(4)]
        else:
            raise ValueError(kind)
        rounds.append((op, queries))
    return rounds
