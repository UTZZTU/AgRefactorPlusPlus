#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* ------ public types&macros required for signature (must match impl) ------ */
#define MAX_ANCHORS 10240

typedef struct { uint64_t x, y; } mm128_t;

typedef struct {
    struct { void *fp; int dummy; } chain_dump_in, chain_dump_out;
    int chain_dump_limit;
} mm_mapopt_t;

/* ---------- original and refactored top kernel signatures ---------- */

// 1. Original function (assumed to be in another file or library)
mm128_t *mm_chain_dp_orig(
int max_dist_x, int max_dist_y, int bw,
int max_skip,  int min_cnt,    int min_sc,
int is_cdna,   int n_segs,
int64_t        n, mm128_t *a,
int *n_u, uint64_t **u, void *km, mm_mapopt_t *opt);
 
// 2. HLS-synthesizable function from 'mm_chain_dp_new1.cpp'
void mm_chain_dp_new(int max_dist_x, int max_dist_y, int bw,
    int max_skip, int min_cnt, int min_sc,
    int is_cdna, int n_segs,
    int64_t n_in,
    mm128_t a[MAX_ANCHORS],     // IN/OUT: reordered anchors
    int *n_u_out,           // OUT: #chains
    uint64_t u_out[MAX_ANCHORS] // OUT: (score<<32)|len
);

/* ------ wrapper of the two top kernels to be called from testbench -------- */

/**
 * @brief Wrapper to make the HLS function callable from the testbench.
 *
 * This function has the same signature as `mm_chain_dp_orig` but calls
 * the HLS-style kernel `mm_chain_dp_hls` internally. It handles the
 * memory allocation differences between the two interfaces.
 */
mm128_t *mm_chain_dp_new_wrapper(
    int max_dist_x, int max_dist_y, int bw,
    int max_skip,   int min_cnt,    int min_sc,
    int is_cdna,    int n_segs,
    int64_t n, mm128_t *a,
    int *n_u, uint64_t **u, void *km, mm_mapopt_t *opt)
{
    (void)km; (void)opt;
    if (n > MAX_ANCHORS) n = MAX_ANCHORS;

    static mm128_t  a_buf[MAX_ANCHORS];
    static uint64_t u_buf[MAX_ANCHORS];

    memcpy(a_buf, a, n * sizeof *a_buf);

    mm_chain_dp_new(max_dist_x,max_dist_y,bw,
                    max_skip,min_cnt,min_sc,
                    is_cdna,n_segs,
                    n, a_buf, n_u, u_buf);

    int total_len = 0;
    for (int i = 0; i < *n_u; ++i) total_len += (int)(u_buf[i] & 0xffffffffu);

    mm128_t  *b_heap = (mm128_t*)malloc(total_len * sizeof *b_heap);
    uint64_t *u_heap = (uint64_t*)malloc(*n_u    * sizeof *u_heap);
    if (!b_heap || !u_heap) { free(b_heap); free(u_heap); *n_u = 0; *u = NULL; return NULL; }

    memcpy(b_heap, a_buf,  total_len * sizeof *b_heap);
    memcpy(u_heap, u_buf,  *n_u      * sizeof *u_heap);

    *u = u_heap;
    return b_heap;
}

 
/* ---------- deterministic anchor generator ----------------------- */
static void make_anchors(int64_t n, uint64_t seed, mm128_t *a)
{
    srand((unsigned)seed);
    uint64_t r = 0, q = 0;
    const int QSPAN = 15;
    for (int64_t i = 0; i < n; ++i) {
        r += 40u + rand()%30u;
        q += 40u + rand()%30u;
        a[i].x = r;
        a[i].y = (uint32_t)q | ((uint64_t)QSPAN << 32);
    }
}
 
/* ---------- deep compare helper ---------------------------------- */
static int equal_outputs(int n_u,
                        const uint64_t *u0, const uint64_t *u1,
                        const mm128_t  *b0, const mm128_t  *b1)
{
    if (u0 == NULL || u1 == NULL || b0 == NULL || b1 == NULL) {
        fprintf(stderr, "[DIFF] One of the outputs is NULL\n");
        return 0;
    }
    int off = 0;
    for (int i = 0; i < n_u; ++i) {
        if (u0[i] != u1[i]) {
            fprintf(stderr,"[DIFF] chain-table u[%d]\n", i);
            return 0;
        }
        int len = (int)(u0[i] & 0xffffffffu);
        for (int j = 0; j < len; ++j) {
            if (b0[off+j].x != b1[off+j].x ||
                b0[off+j].y != b1[off+j].y) {
                fprintf(stderr,"[DIFF] anchor %d in chain %d\n", j, i);
                return 0;
            }
        }
        off += len;
    }
    return 1;
}
 
/* ---------- main -------------------------------------------------- */
int main()
{
    const int     R=300;
    const int64_t N=1000;
    const int     MAX_DIST_X=500,MAX_DIST_Y=100,BW=500,MAX_SKIP=25,MIN_CNT=3,MIN_SC=40;

    for (int rep = 0; rep < R; ++rep) {
        /* 1. identical inputs for both kernels */
        mm128_t *a0 = (mm128_t*)malloc((size_t)N * sizeof *a0);
        mm128_t *a1 = (mm128_t*)malloc((size_t)N * sizeof *a1);
        make_anchors(N, 0xCAFE0000u + (unsigned)rep, a0);
        memcpy(a1, a0, (size_t)N * sizeof *a1);

        /* 2. run both – pass NULL for km (forces malloc/free path) */
        int n_u0, n_u1;  uint64_t *u0 = NULL, *u1 = NULL;
        mm128_t *b0 = mm_chain_dp_orig(MAX_DIST_X,MAX_DIST_Y,BW,MAX_SKIP,MIN_CNT,MIN_SC,0,1,N,a0,&n_u0,&u0,NULL,NULL);
        // use wrapper to call the refactored kernel
        mm128_t *b1 = mm_chain_dp_new_wrapper(MAX_DIST_X,MAX_DIST_Y,BW,MAX_SKIP,MIN_CNT,MIN_SC,0,1,N,a1,&n_u1,&u1,NULL,NULL);

        /* 3. compare */
        if (n_u0 != n_u1) {
            fprintf(stderr,"[FAIL] n_u mismatch: orig=%d new=%d\n", n_u0, n_u1);
        } else if (!equal_outputs(n_u0,u0,u1,b0,b1)) {
            fprintf(stderr,"[FAIL] mismatch on repetition %d\n", rep);
            return 1;
        }

    }

    return 0;
}
 