"""Power analysis: how many RESOLVED markets to detect a given edge?

Backs section 5 of PREREGISTRATION.md. Run before collection; the answer is why
this study is framed around calibration (cheap) rather than edge (expensive).

Expected Brier gap = sm^2 - sa^2. Simulate to find N where the 95%
paired-bootstrap-equivalent CI (normal approx on the paired diff)
excludes zero at least 80% of the time.
"""
import random, math, statistics

def trial(n, sa, sm, rng):
    diffs = []
    for _ in range(n):
        tp = rng.betavariate(2, 2)
        m = min(max(tp + rng.gauss(0, sm), .01), .99)
        a = min(max(tp + rng.gauss(0, sa), .01), .99)
        y = 1 if rng.random() < tp else 0
        diffs.append((m - y) ** 2 - (a - y) ** 2)
    mean = statistics.fmean(diffs)
    se = statistics.stdev(diffs) / math.sqrt(n)
    return (mean - 1.96 * se) > 0          # detected agent edge

def power(n, sa, sm, reps=400, seed=1):
    rng = random.Random(seed)
    return sum(trial(n, sa, sm, rng) for _ in range(reps)) / reps

print(f"{'agent sigma':>12} {'mkt sigma':>10} {'Brier gap':>10}   N for 80% power")
print("-" * 62)
for sa, sm in [(0.02, 0.06), (0.03, 0.06), (0.04, 0.06),
               (0.05, 0.10), (0.02, 0.10), (0.06, 0.06)]:
    gap = sm**2 - sa**2
    found = None
    for n in (100, 200, 400, 800, 1500, 3000, 6000, 12000):
        if power(n, sa, sm, reps=250) >= 0.80:
            found = n
            break
    label = f"{found}" if found else ">12000"
    print(f"{sa:>12.2f} {sm:>10.2f} {gap:>10.4f}   {label}")
