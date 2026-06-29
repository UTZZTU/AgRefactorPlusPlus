#include <assert.h>
#include <stdint.h>
#include <stddef.h>

#define MAX_ANCHORS     10240

#define MM_SEED_SEG_SHIFT  48
#define MM_SEED_SEG_MASK   (0xffULL<<(MM_SEED_SEG_SHIFT))

#ifndef kroundup32
#define kroundup32(x) (--(x), (x)|=(x)>>1, (x)|=(x)>>2, (x)|=(x)>>4, (x)|=(x)>>8, (x)|=(x)>>16, ++(x))
#endif

#define RS_MIN_SIZE 64
#define RS_MAX_BITS 8

typedef struct { uint64_t x, y; } mm128_t;

int32_t ilog2_32(uint32_t v) {
#pragma HLS INLINE
    int32_t r = 0;
    if (v >= 1 << 16) { v >>= 16; r += 16; }
    if (v >= 1 << 8)  { v >>= 8;  r += 8;  }
    if (v >= 1 << 4)  { v >>= 4;  r += 4;  }
    if (v >= 1 << 2)  { v >>= 2;  r += 2;  }
    if (v >= 1 << 1)  {           r += 1;  }
    return r;
}

/* ---------------- Radix sort helpers ---------------- */

#define MAX_N      MAX_ANCHORS
#define RS_BITS    8
#define RS_SIZE    (1 << RS_BITS)
#define RS_PASSES  (64 / RS_BITS)

static inline int clamp_n(int n) { return (n > MAX_N) ? MAX_N : (n < 0 ? 0 : n); }

static void radix_sort_u64(uint64_t a[MAX_ANCHORS], int n)
{
    static uint64_t buf[MAX_N];
    uint16_t cnt[RS_SIZE];
    int pass, i;

    n = clamp_n(n);
    if (n <= 1) return;

    for (pass = 0; pass < RS_PASSES; ++pass) {
        for (i = 0; i < RS_SIZE; ++i) cnt[i] = 0;
        for (i = 0; i < n; ++i) ++cnt[(a[i] >> (pass * RS_BITS)) & (RS_SIZE - 1)];
        uint16_t sum = 0;
        for (i = 0; i < RS_SIZE; ++i) { uint16_t c = cnt[i]; cnt[i] = sum; sum += c; }
        for (i = 0; i < n; ++i) { uint8_t d = (a[i] >> (pass * RS_BITS)) & (RS_SIZE - 1); buf[cnt[d]++] = a[i]; }
        for (i = 0; i < n; ++i) a[i] = buf[i];
    }
}

static void radix_sort_128x(mm128_t a[MAX_ANCHORS], int n)
{
    static mm128_t buf[MAX_N];
    uint16_t cnt[RS_SIZE];
    int pass, i;

    n = clamp_n(n);
    if (n <= 1) return;

    for (pass = 0; pass < RS_PASSES; ++pass) {
        for (i = 0; i < RS_SIZE; ++i) cnt[i] = 0;
        for (i = 0; i < n; ++i) ++cnt[(a[i].x >> (pass * RS_BITS)) & (RS_SIZE - 1)];
        uint16_t sum = 0;
        for (i = 0; i < RS_SIZE; ++i) { uint16_t c = cnt[i]; cnt[i] = sum; sum += c; }
        for (i = 0; i < n; ++i) { uint8_t d = (a[i].x >> (pass * RS_BITS)) & (RS_SIZE - 1); buf[cnt[d]++] = a[i]; }
        for (i = 0; i < n; ++i) a[i] = buf[i];
    }
}

/* ---------------- Main DP chaining ---------------- */

void mm_chain_dp_new(
    int max_dist_x,
    int max_dist_y,
    int bw, int max_skip, int min_cnt, int min_sc, int is_cdna, int n_segs,
    int64_t n_in,
    mm128_t a[MAX_ANCHORS],
    int *n_u_out,
    uint64_t u_out[MAX_ANCHORS])
{
    (void)max_skip; (void)is_cdna; (void)n_segs; /* unused in this simplified path */

    int64_t n = (n_in > MAX_ANCHORS) ? MAX_ANCHORS : (n_in < 0 ? 0 : n_in);
    int32_t f[MAX_ANCHORS] = {0};
    int32_t p[MAX_ANCHORS] = {0};
    int32_t t[MAX_ANCHORS] = {0};
    int32_t v[MAX_ANCHORS] = {0};
    uint64_t u[MAX_ANCHORS] = {0};
    uint64_t u2[MAX_ANCHORS] = {0};
    mm128_t  b[MAX_ANCHORS];
    mm128_t  w[MAX_ANCHORS];

    int32_t k, n_u, n_v;
    int64_t i, j, st = 0;
    uint32_t sum_qspan = 0;
    float avg_qspan;

    for (i = 0; i < n; ++i) sum_qspan += (uint32_t)((a[i].y >> 32) & 0xFF);
    avg_qspan = (n > 0) ? (float)(sum_qspan / n) : 0.0f;

    for (i = 0; i < n; ++i) {
        uint64_t ri = a[i].x;
        int64_t  max_j = -1;
        int32_t  qi = (int32_t)a[i].y;
        int32_t  q_span = (a[i].y >> 32) & 0xFF;
        int32_t  max_f = q_span;

        while (st < i && ri > a[st].x + max_dist_x) ++st;
        for (j = i - 1; j >= st && j > i - 65; --j) {
            int64_t dr = ri - a[j].x;
            int32_t dq = qi - (int32_t)a[j].y;
            if (dr == 0 || dq <= 0) continue;
            if (dq > max_dist_y || dq > max_dist_x) continue;

            int32_t dd = (dr > dq) ? (int32_t)(dr - dq) : (int32_t)(dq - dr);
            if (dd > bw) continue;
            if (n_segs > 1 && dr > max_dist_y) continue;

            int32_t min_d = dq < dr ? dq : dr;
            int32_t sc = (min_d > q_span) ? q_span : (dq < dr ? dq : dr);
            int32_t log_dd = dd ? ilog2_32((uint32_t)dd) : 0;
            sc -= (int)((dd * 0.01f * avg_qspan) + (log_dd >> 1));
            sc += f[j];

            if (sc > max_f) { max_f = sc; max_j = (int32_t)j; }
        }
        f[i] = max_f; p[i] = (int32_t)max_j;
        v[i] = (max_j >= 0 && v[max_j] > max_f) ? v[max_j] : max_f;
    }

    for (i = 0; i < n; ++i)
        if (p[i] >= 0) t[p[i]] = 1;

    for (i = n_u = 0; i < n; ++i)
        if (t[i] == 0 && v[i] >= min_sc) ++n_u;

    if (n_u == 0) { *n_u_out = 0; return; }

    n_u = 0;
    for (i = 0; i < n; ++i) {
        if (t[i] == 0 && v[i] >= min_sc) {
            j = i;
            while (j >= 0 && f[j] < v[j]) j = p[j];
            if (j < 0) j = i;
            u[n_u++] = ((uint64_t)f[j] << 32) | (uint32_t)j;
        }
    }

    radix_sort_u64(u, n_u);
    for (i = 0; i < (n_u >> 1); ++i) {
        uint64_t tmp = u[i];
        u[i] = u[n_u - 1 - i];
        u[n_u - 1 - i] = tmp;
    }

    for (i = 0; i < n; ++i) t[i] = 0;

    for (i = n_v = k = 0; i < n_u; ++i) {
        int32_t n_v0 = n_v, k0 = k;
        j = (int32_t)u[i];
        do {
            v[n_v++] = j;
            t[j] = 1;
            j = p[j];
        } while (j >= 0 && t[j] == 0);
        if (j < 0) {
            if (n_v - n_v0 >= min_cnt)
                u[k++] = (u[i] & 0xffffffff00000000ULL) | (uint32_t)(n_v - n_v0);
        } else if ((int32_t)(u[i] >> 32) - f[j] >= min_sc) {
            if (n_v - n_v0 >= min_cnt)
                u[k++] = (((u[i] >> 32) - f[j]) << 32) | (uint32_t)(n_v - n_v0);
        }
        if (k0 == k) n_v = n_v0;
    }
    n_u = k;

    *n_u_out = n_u;
    for (i = 0; i < n_u; ++i) u_out[i] = u[i];

    k = 0;
    for (i = 0; i < n_u; ++i) {
        int32_t k0 = k, ni = (int32_t)u[i];
        for (j = 0; j < ni; ++j) {
            if (k >= MAX_ANCHORS) break;
            b[k] = a[v[k0 + (ni - j - 1)]];
            ++k;
        }
    }

    k = 0;
    for (i = 0; i < n_u; ++i) {
        w[i].x = b[k].x;
        w[i].y = ((uint64_t)k << 32) | (uint32_t)i;
        k += (int32_t)u[i];
    }

    radix_sort_128x(w, n_u);

    k = 0;
    for (i = 0; i < n_u; ++i) {
        int32_t idx = (int32_t)(w[i].y & 0xffffffffu);
        int32_t src = (int32_t)(w[i].y >> 32);
        int32_t len = (int32_t)u[idx];
        u2[i] = u[idx];
        for (j = 0; j < len; ++j) {
            if (k >= MAX_ANCHORS) break;
            a[k++] = b[src + j];
        }
    }
    for (i = 0; i < n_u; ++i) {
        u[i] = u2[i];
        u_out[i] = u[i];
    }
}
