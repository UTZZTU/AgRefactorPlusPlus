from math import comb

def pass_at_k(N, c, k):
    if k <= 0:
        return 0.0
    if c <= 0:
        return 0.0
    if k > N:
        # If you're sampling more than N without replacement, treat as taking all
        return 1.0 if c > 0 else 0.0
    return 1.0 - (comb(N - c, k) / comb(N, k))

N = 20
ks = [1, 3, 5]

# Header
print(f"Pass@k for N={N}")
print(f"{'successes':>9} | " + " | ".join([f"pass@{k:>1}" for k in ks]))
print("-" * (11 + 7 * len(ks)))

# Rows
for c in range(N + 1):
    values = [pass_at_k(N, c, k) for k in ks]
    print(f"{c:9d} | " + " | ".join(f"{v:0.6f}" for v in values))
