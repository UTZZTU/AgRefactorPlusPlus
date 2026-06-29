#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <stddef.h>

// ============================================================================
// Constants and Macros
// ============================================================================

#define MM_SEED_SEG_SHIFT  48
#define MM_SEED_SEG_MASK   (0xffULL<<(MM_SEED_SEG_SHIFT))

#define MIN_CORE_SIZE 0x80000

#ifndef kroundup32
#define kroundup32(x) (--(x), (x)|=(x)>>1, (x)|=(x)>>2, (x)|=(x)>>4, (x)|=(x)>>8, (x)|=(x)>>16, ++(x))
#endif

#define RS_MIN_SIZE 64
#define RS_MAX_BITS 8

#define KSORT_SWAP(type_t, a, b) { register type_t t=(a); (a)=(b); (b)=t; }

// ============================================================================
// Data Types
// ============================================================================

// emulate 128-bit integers
typedef struct { uint64_t x, y; } mm128_t;

// memory allocator statistics
typedef struct {
    size_t capacity, available, n_blocks, n_cores, largest;
} km_stat_t;

// memory allocator header
typedef struct header_t {
    size_t size;
    struct header_t *ptr;
} header_t;

// memory allocator
typedef struct {
    header_t base, *loop_head, *core_head;
} kmem_t;

// mutex file type for chain dumping
typedef struct {
    pthread_mutex_t mutex;
    FILE *fp;
} mm_mutex_file_t;

// mapping options structure (simplified for mm_chain_dp)
typedef struct {
    int seed;
    int sdust_thres;
    int flag;
    int bw;
    int max_gap, max_gap_ref;
    int max_frag_len;
    int max_chain_skip;
    int min_cnt;
    int min_chain_score;
    float mask_level;
    float pri_ratio;
    int best_n;
    int max_join_long, max_join_short;
    int min_join_flank_sc;
    float min_join_flank_ratio;
    int a, b, q, e, q2, e2;
    int sc_ambi;
    int noncan;
    int zdrop, zdrop_inv;
    int end_bonus;
    int min_dp_max;
    int min_ksw_len;
    int anchor_ext_len, anchor_ext_shift;
    float max_clip_ratio;
    int pe_ori, pe_bonus;
    float mid_occ_frac;
    int32_t min_mid_occ;
    int32_t mid_occ;
    int32_t max_occ;
    int mini_batch_size;
    const char *split_prefix;
    mm_mutex_file_t chain_dump_in;
    mm_mutex_file_t chain_dump_out;
    int chain_dump_limit;
} mm_mapopt_t;

// ============================================================================
// Log table for fast integer log2
// ============================================================================

static const char LogTable256[256] = {
#define LT(n) n, n, n, n, n, n, n, n, n, n, n, n, n, n, n, n
    -1, 0, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3,
    LT(4), LT(5), LT(5), LT(6), LT(6), LT(6), LT(6),
    LT(7), LT(7), LT(7), LT(7), LT(7), LT(7), LT(7), LT(7)
};

static inline int ilog2_32(uint32_t v)
{
    uint32_t t, tt;
    if ((tt = v>>16)) return (t = tt>>8) ? 24 + LogTable256[t] : 16 + LogTable256[tt];
    return (t = v>>8) ? 8 + LogTable256[t] : LogTable256[v];
}

// ============================================================================
// Memory Allocator Implementation (kalloc)
// ============================================================================

// Forward declarations
static void kfree(void *_km, void *ap);

static void panic(const char *s)
{
    fprintf(stderr, "%s\n", s);
    abort();
}

static void *km_init(void)
{
    return calloc(1, sizeof(kmem_t));
}

static void km_destroy(void *_km)
{
    kmem_t *km = (kmem_t*)_km;
    header_t *p, *q;
    if (km == NULL) return;
    for (p = km->core_head; p != NULL;) {
        q = p->ptr;
        free(p);
        p = q;
    }
    free(km);
}

static header_t *morecore(kmem_t *km, size_t nu)
{
    header_t *q;
    size_t bytes, *p;
    nu = (nu + 1 + (MIN_CORE_SIZE - 1)) / MIN_CORE_SIZE * MIN_CORE_SIZE;
    bytes = nu * sizeof(header_t);
    q = (header_t*)malloc(bytes);
    if (!q) panic("[morecore] insufficient memory");
    q->ptr = km->core_head, q->size = nu, km->core_head = q;
    p = (size_t*)(q + 1);
    *p = nu - 1;
    kfree(km, p + 1);
    return km->loop_head;
}

static void kfree(void *_km, void *ap)
{
    header_t *p, *q;
    kmem_t *km = (kmem_t*)_km;
    
    if (!ap) return;
    if (km == NULL) {
        free(ap);
        return;
    }
    p = (header_t*)((size_t*)ap - 1);
    p->size = *((size_t*)ap - 1);
    
    for (q = km->loop_head; !(p > q && p < q->ptr); q = q->ptr)
        if (q >= q->ptr && (p > q || p < q->ptr)) break;
        
    if (p + p->size == q->ptr) {
        p->size += q->ptr->size;
        p->ptr = q->ptr->ptr;
    } else if (p + p->size > q->ptr && q->ptr >= p) {
        panic("[kfree] The end of the allocated block enters a free block.");
    } else p->ptr = q->ptr;

    if (q + q->size == p) {
        q->size += p->size;
        q->ptr = p->ptr;
        km->loop_head = q;
    } else if (q + q->size > p && p >= q) {
        panic("[kfree] The end of a free block enters the allocated block.");
    } else km->loop_head = p, q->ptr = p;
}

static void *kmalloc(void *_km, size_t n_bytes)
{
    kmem_t *km = (kmem_t*)_km;
    size_t n_units;
    header_t *p, *q;

    if (n_bytes == 0) return 0;
    if (km == NULL) return malloc(n_bytes);
    n_units = (n_bytes + sizeof(size_t) + sizeof(header_t) - 1) / sizeof(header_t) + 1;

    if (!(q = km->loop_head))
        q = km->loop_head = km->base.ptr = &km->base;
    for (p = q->ptr;; q = p, p = p->ptr) {
        if (p->size >= n_units) {
            if (p->size == n_units) q->ptr = p->ptr;
            else {
                p->size -= n_units;
                p += p->size;
                *(size_t*)p = n_units;
            }
            km->loop_head = q;
            return (size_t*)p + 1;
        }
        if (p == km->loop_head) {
            if ((p = morecore(km, n_units)) == 0) return 0;
        }
    }
}

static void *kcalloc(void *_km, size_t count, size_t size)
{
    kmem_t *km = (kmem_t*)_km;
    void *p;
    if (size == 0 || count == 0) return 0;
    if (km == NULL) return calloc(count, size);
    p = kmalloc(km, count * size);
    memset(p, 0, count * size);
    return p;
}

// ============================================================================
// Radix Sort Implementation
// ============================================================================

// Radix sort bucket structure
typedef struct {
    uint64_t *b, *e;
} rsbucket_64_t;

typedef struct {
    mm128_t *b, *e;
} rsbucket_128x_t;

// Insert sort for small arrays
static void rs_insertsort_64(uint64_t *beg, uint64_t *end)
{
    uint64_t *i;
    for (i = beg + 1; i < end; ++i)
        if (*i < *(i - 1)) {
            uint64_t *j, tmp = *i;
            for (j = i; j > beg && tmp < *(j-1); --j)
                *j = *(j - 1);
            *j = tmp;
        }
}

static void rs_insertsort_128x(mm128_t *beg, mm128_t *end)
{
    mm128_t *i;
    for (i = beg + 1; i < end; ++i)
        if (i->x < (i - 1)->x) {
            mm128_t *j, tmp = *i;
            for (j = i; j > beg && tmp.x < (j-1)->x; --j)
                *j = *(j - 1);
            *j = tmp;
        }
}

// Radix sort for 64-bit integers
static void rs_sort_64(uint64_t *beg, uint64_t *end, int n_bits, int s)
{
    uint64_t *i;
    int size = 1<<n_bits, m = size - 1;
    rsbucket_64_t *k, b[1<<RS_MAX_BITS], *be = b + size;
    assert(n_bits <= RS_MAX_BITS);
    for (k = b; k != be; ++k) k->b = k->e = beg;
    for (i = beg; i != end; ++i) ++b[(*i)>>s&m].e;
    for (k = b + 1; k != be; ++k)
        k->e += (k-1)->e - beg, k->b = (k-1)->e;
    for (k = b; k != be;) {
        if (k->b != k->e) {
            rsbucket_64_t *l;
            if ((l = b + ((*k->b)>>s&m)) != k) {
                uint64_t tmp = *k->b, swap;
                do {
                    swap = tmp; tmp = *l->b; *l->b++ = swap;
                    l = b + (tmp>>s&m);
                } while (l != k);
                *k->b++ = tmp;
            } else ++k->b;
        } else ++k;
    }
    for (b->b = beg, k = b + 1; k != be; ++k) k->b = (k-1)->e;
    if (s) {
        s = s > n_bits? s - n_bits : 0;
        for (k = b; k != be; ++k)
            if (k->e - k->b > RS_MIN_SIZE) rs_sort_64(k->b, k->e, n_bits, s);
            else if (k->e - k->b > 1) rs_insertsort_64(k->b, k->e);
    }
}

// Radix sort for 128-bit structures (using .x field)
static void rs_sort_128x(mm128_t *beg, mm128_t *end, int n_bits, int s)
{
    mm128_t *i;
    int size = 1<<n_bits, m = size - 1;
    rsbucket_128x_t *k, b[1<<RS_MAX_BITS], *be = b + size;
    assert(n_bits <= RS_MAX_BITS);
    for (k = b; k != be; ++k) k->b = k->e = beg;
    for (i = beg; i != end; ++i) ++b[(i->x)>>s&m].e;
    for (k = b + 1; k != be; ++k)
        k->e += (k-1)->e - beg, k->b = (k-1)->e;
    for (k = b; k != be;) {
        if (k->b != k->e) {
            rsbucket_128x_t *l;
            if ((l = b + ((k->b->x)>>s&m)) != k) {
                mm128_t tmp = *k->b, swap;
                do {
                    swap = tmp; tmp = *l->b; *l->b++ = swap;
                    l = b + (tmp.x>>s&m);
                } while (l != k);
                *k->b++ = tmp;
            } else ++k->b;
        } else ++k;
    }
    for (b->b = beg, k = b + 1; k != be; ++k) k->b = (k-1)->e;
    if (s) {
        s = s > n_bits? s - n_bits : 0;
        for (k = b; k != be; ++k)
            if (k->e - k->b > RS_MIN_SIZE) rs_sort_128x(k->b, k->e, n_bits, s);
            else if (k->e - k->b > 1) rs_insertsort_128x(k->b, k->e);
    }
}

static void radix_sort_64(uint64_t *beg, uint64_t *end)
{
    if (end - beg <= RS_MIN_SIZE) rs_insertsort_64(beg, end);
    else rs_sort_64(beg, end, RS_MAX_BITS, (8 - 1) * RS_MAX_BITS);
}

static void radix_sort_128x(mm128_t *beg, mm128_t *end)
{
    if (end - beg <= RS_MIN_SIZE) rs_insertsort_128x(beg, end);
    else rs_sort_128x(beg, end, RS_MAX_BITS, (8 - 1) * RS_MAX_BITS);
}

// ============================================================================
// Main Chain DP Function
// ============================================================================

mm128_t *mm_chain_dp_orig(
    int max_dist_x, 
    int max_dist_y, 
    int bw, int max_skip, int min_cnt, int min_sc, int is_cdna, int n_segs, 
    int64_t n, mm128_t *a, 
    int *n_u_, uint64_t **_u, 
    void *km, mm_mapopt_t *opt)
{
    int32_t k, *f, *p, *t, *v, n_u, n_v;
    int64_t i, j, st = 0;
    uint64_t *u, *u2, sum_qspan = 0;
    float avg_qspan;
    mm128_t *b, *w;

    if (_u) *_u = 0, *n_u_ = 0;
    f = (int32_t*)kmalloc(km, n * 4);
    p = (int32_t*)kmalloc(km, n * 4);
    t = (int32_t*)kmalloc(km, n * 4);
    v = (int32_t*)kmalloc(km, n * 4);
    memset(t, 0, n * 4);

    for (i = 0; i < n; ++i) sum_qspan += a[i].y>>32&0xff;
    avg_qspan = (float)sum_qspan / n;

    // fill the score and backtrack arrays
    for (i = 0; i < n; ++i) {
        uint64_t ri = a[i].x;
        int64_t max_j = -1;
        int32_t qi = (int32_t)a[i].y, q_span = a[i].y>>32&0xff;
        int32_t max_f = q_span, min_d;
        int32_t sidi = (a[i].y & MM_SEED_SEG_MASK) >> MM_SEED_SEG_SHIFT;
        while (st < i && ri > a[st].x + max_dist_x) ++st;
        int h = 65;
        for (j = i - 1; j >= st && j > i - h; --j) {
            int64_t dr = ri - a[j].x;
            int32_t dq = qi - (int32_t)a[j].y, dd, sc, log_dd;
            int32_t sidj = (a[j].y & MM_SEED_SEG_MASK) >> MM_SEED_SEG_SHIFT;
            // optimization assertions, no splice support
            assert(is_cdna == 0);
            assert(sidi == sidj);
            if ((/*sidi == sidj*/1 && dr == 0) || dq <= 0) continue;
            if ((/*sidi == sidj*/1 && dq > max_dist_y) || dq > max_dist_x) continue;
            dd = dr > dq? dr - dq : dq - dr;
            if (/*sidi == sidj*/1 && dd > bw) continue;
            if (n_segs > 1 && /*!is_cdna*/1 && /*sidi == sidj*/1 && dr > max_dist_y) continue;
            min_d = dq < dr? dq : dr;
            sc = min_d > q_span? q_span : dq < dr? dq : dr;
            log_dd = dd? ilog2_32(dd) : 0;
            if (/*is_cdna*/0 || /*sidi != sidj*/0) {
                int c_log, c_lin;
                c_lin = (int)(dd * .01 * avg_qspan);
                c_log = log_dd;
                if (sidi != sidj && dr == 0) ++sc;
                else if (dr > dq || sidi != sidj) sc -= c_lin < c_log? c_lin : c_log;
                else sc -= c_lin + (c_log>>1);
            } else sc -= (int)(dd * .01 * avg_qspan) + (log_dd>>1);
            sc += f[j];
            if (sc > max_f) {
                max_f = sc, max_j = j;
            }
        }
        f[i] = max_f, p[i] = max_j;
        v[i] = max_j >= 0 && v[max_j] > max_f? v[max_j] : max_f;
    }

    if (opt && (opt->chain_dump_in.fp || opt->chain_dump_out.fp)) {
        pthread_mutex_lock(&(opt->chain_dump_in.mutex));
        pthread_mutex_lock(&opt->chain_dump_out.mutex);
        if (opt->chain_dump_in.fp) {
            FILE *fp = opt->chain_dump_in.fp;
            static int count = 0;
            if (count++ > opt->chain_dump_limit) {
                fclose(opt->chain_dump_in.fp);
                fclose(opt->chain_dump_out.fp);
                exit(0);
            }
            fprintf(fp, "%lld\t%.6f\t%d\t%d\t%d\n",
                    (long long)n, avg_qspan, max_dist_x, max_dist_y, bw);
            for (i = 0; i < n; ++i) {
                fprintf(fp, "%u\t%d\t%d\t%d\n",
                        (unsigned int)(uint32_t)(a[i].x >> 32),
                        (int)(int32_t)(a[i].x),
                        (int)(int32_t)(a[i].y >> 32 & 0xff),
                        (int)(int32_t)(a[i].y));
            }
            fprintf(fp, "EOR\n");
        }

        if (opt->chain_dump_out.fp) {
            FILE *fp = opt->chain_dump_out.fp;
            fprintf(fp, "%lld\n", (long long)n);
            for (i = 0; i < n; i++) {
                fprintf(fp, "%d\t%d\n", (int)f[i], (int)p[i]);
            }
            fprintf(fp, "EOR\n");
        }
        pthread_mutex_unlock(&(opt->chain_dump_in.mutex));
        pthread_mutex_unlock(&opt->chain_dump_out.mutex);
    }

    // find the ending positions of chains
    memset(t, 0, n * 4);
    for (i = 0; i < n; ++i)
        if (p[i] >= 0) t[p[i]] = 1;
    for (i = n_u = 0; i < n; ++i)
        if (t[i] == 0 && v[i] >= min_sc)
            ++n_u;
    if (n_u == 0) {
        kfree(km, a); kfree(km, f); kfree(km, p); kfree(km, t); kfree(km, v);
        return 0;
    }
    u = (uint64_t*)kmalloc(km, n_u * 8);
    for (i = n_u = 0; i < n; ++i) {
        if (t[i] == 0 && v[i] >= min_sc) {
            j = i;
            while (j >= 0 && f[j] < v[j]) j = p[j];
            if (j < 0) j = i;
            u[n_u++] = (uint64_t)f[j] << 32 | j;
        }
    }
    radix_sort_64(u, u + n_u);
    for (i = 0; i < n_u>>1; ++i) {
        uint64_t t = u[i];
        u[i] = u[n_u - i - 1], u[n_u - i - 1] = t;
    }

    // backtrack
    memset(t, 0, n * 4);
    for (i = n_v = k = 0; i < n_u; ++i) {
        int32_t n_v0 = n_v, k0 = k;
        j = (int32_t)u[i];
        do {
            v[n_v++] = j;
            t[j] = 1;
            j = p[j];
        } while (j >= 0 && t[j] == 0);
        if (j < 0) {
            if (n_v - n_v0 >= min_cnt) u[k++] = u[i]>>32<<32 | (n_v - n_v0);
        } else if ((int32_t)(u[i]>>32) - f[j] >= min_sc) {
            if (n_v - n_v0 >= min_cnt) u[k++] = ((u[i]>>32) - f[j]) << 32 | (n_v - n_v0);
        }
        if (k0 == k) n_v = n_v0;
    }
    *n_u_ = n_u = k, *_u = u;

    // free temporary arrays
    kfree(km, f); kfree(km, p); kfree(km, t);

    // write the result to b[]
    b = (mm128_t*)kmalloc(km, n_v * sizeof(mm128_t));
    for (i = 0, k = 0; i < n_u; ++i) {
        int32_t k0 = k, ni = (int32_t)u[i];
        for (j = 0; j < ni; ++j)
            b[k] = a[v[k0 + (ni - j - 1)]], ++k;
    }
    kfree(km, v);

    // sort u[] and a[] by a[].x, such that adjacent chains may be joined
    w = (mm128_t*)kmalloc(km, n_u * sizeof(mm128_t));
    for (i = k = 0; i < n_u; ++i) {
        w[i].x = b[k].x, w[i].y = (uint64_t)k<<32|i;
        k += (int32_t)u[i];
    }
    radix_sort_128x(w, w + n_u);
    u2 = (uint64_t*)kmalloc(km, n_u * 8);
    for (i = k = 0; i < n_u; ++i) {
        int32_t j = (int32_t)w[i].y, n = (int32_t)u[j];
        u2[i] = u[j];
        memcpy(&a[k], &b[w[i].y>>32], n * sizeof(mm128_t));
        k += n;
    }
    memcpy(u, u2, n_u * 8);
    memcpy(b, a, k * sizeof(mm128_t));
    kfree(km, a); kfree(km, w); kfree(km, u2);
    return b;
}
