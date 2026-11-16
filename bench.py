"""Frozen complete-cycle workload and relative performance measurement."""
import gc
import json
import statistics
import sys
import time
import tracemalloc
from corpus import Corpus
from search import Search
from reference import ReferenceCorpus, ReferenceSearch
from samples import documents, plan

LIMIT = 96 * 1024 * 1024

def execute(c, s, operations):
    answers, scored = [], 0
    for op, queries in operations:
        getattr(c, op[0])(*op[1:])
        for terms, k in queries:
            answers.append(s.query(terms, k))
            scored += s.last_scored
    return answers, scored

def timed(C, S, kind, operations):
    gc.collect()
    start = time.perf_counter()
    c = C(documents(kind))
    s = S(c)
    build = time.perf_counter() - start
    start = time.perf_counter()
    answers, scored = execute(c, s, operations)
    elapsed = time.perf_counter() - start
    return answers, elapsed, build, scored

def memory(C, S, kind, operations):
    gc.collect()
    tracemalloc.start()
    try:
        c = C(documents(kind))
        s = S(c)
        build_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.reset_peak()
        execute(c, s, operations)
        execution_peak = tracemalloc.get_traced_memory()[1]
        return build_peak, execution_peak
    finally:
        tracemalloc.stop()

def main(enforce=False):
    report = []
    for kind in ("mixed", "df_jump"):
        operations = plan(kind)
        ref_times, times, builds, scores = [], [], [], []
        for _ in range(3):
            expected, rt, _, rs = timed(ReferenceCorpus, ReferenceSearch, kind, operations)
            actual, elapsed, build, scored = timed(Corpus, Search, kind, operations)
            assert actual == expected, ("ranking differs", kind)
            ref_times.append(rt)
            times.append(elapsed)
            builds.append(build)
            scores.append(scored)
        build_peak, execution_peak = memory(Corpus, Search, kind, operations)
        ratio = statistics.median(times) / statistics.median(ref_times)
        row = dict(workload=kind, documents=60000, changes=10, queries=40,
                   reference_seconds=ref_times, candidate_seconds=times,
                   median_ratio=ratio, build_seconds=builds,
                   build_peak_bytes=build_peak, execution_peak_bytes=execution_peak,
                   candidate_scored=scores, reference_scored=rs)
        report.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
        if enforce:
            assert ratio <= 0.25, ("OPTIMIZATION_TARGET_NOT_MET", kind, ratio)
            assert build_peak <= LIMIT and execution_peak <= LIMIT, ("MEMORY_LIMIT", kind)
    print("TARGET_OK" if enforce else "BASELINE_MEASURED", flush=True)
    return report

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("baseline", "target"):
        raise SystemExit("usage: python3 bench.py baseline|target")
    main(sys.argv[1] == "target")
