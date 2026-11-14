"""Synthetic credit-default style dataset for spikes (stdlib only)."""
import csv, math, random, sys
random.seed(7)
n = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
with open(sys.argv[1], "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["age", "income", "limit_bal", "utilization", "late_payments", "tenure_months", "region", "default"])
    for _ in range(n):
        age = random.randint(21, 70); income = round(random.lognormvariate(10.8, 0.5)); limit = round(income * random.uniform(0.1, 0.6))
        util = round(random.betavariate(2, 3), 3); late = min(int(random.expovariate(0.8)), 9); tenure = random.randint(1, 240)
        region = random.choice(["north", "south", "east", "west"])
        z = -2.2 + 2.5 * util + 0.45 * late - 0.012 * (age - 40) - 0.004 * tenure + (0.3 if region == "south" else 0)
        w.writerow([age, income, limit, util, late, tenure, region, "yes" if random.random() < 1 / (1 + math.exp(-z)) else "no"])
