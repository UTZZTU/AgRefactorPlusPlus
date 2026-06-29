#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

/* Basic type definitions */
typedef uint8_t JSAMPLE;        /* 8-bit samples */
typedef int32_t JLONG;          /* 32-bit signed integers */
typedef uint16_t UINT16;        /* 16-bit unsigned integers */
typedef int boolean;            /* boolean type */
#ifndef TRUE
#define TRUE 1
#define FALSE 0
#endif

/* Constants from libjpeg-turbo */
#define MAXJSAMPLE 255          /* Maximum JSAMPLE value */
#define MAXNUMCOLORS (MAXJSAMPLE + 1)

/* Scale factors for RGB distance calculations */
#define R_SCALE 2               /* scale R distances by this much */
#define G_SCALE 3               /* scale G distances by this much */
#define B_SCALE 1               /* and B by this much */

/* Histogram precision constants */
#define HIST_C0_BITS 5          /* bits of precision in R histogram */
#define HIST_C1_BITS 6          /* bits of precision in G histogram */
#define HIST_C2_BITS 5          /* bits of precision in B histogram */

/* Number of elements along histogram axes */
#define HIST_C0_ELEMS (1 << HIST_C0_BITS)
#define HIST_C1_ELEMS (1 << HIST_C1_BITS)
#define HIST_C2_ELEMS (1 << HIST_C2_BITS)

/* Shift amounts to get histogram indexes */
#define C0_SHIFT (8 - HIST_C0_BITS)    /* Assuming 8-bit samples */
#define C1_SHIFT (8 - HIST_C1_BITS)
#define C2_SHIFT (8 - HIST_C2_BITS)

/* Color space constants (simplified - assuming RGB only) */
#define C0_SCALE R_SCALE        /* Red scale factor */
#define C1_SCALE G_SCALE        /* Green scale factor */
#define C2_SCALE B_SCALE        /* Blue scale factor */

/* Histogram data types */
typedef UINT16 histcell;        /* histogram cell; prefer unsigned type */
typedef histcell *histptr;      /* for pointers to histogram cells */
typedef histcell hist1d[HIST_C2_ELEMS]; /* typedefs for the array */
typedef hist1d *hist2d;         /* type for the 2nd-level pointers */
typedef hist2d *hist3d;         /* type for top-level pointer */

/* Box structure for median-cut algorithm */
typedef struct {
    /* The bounds of the box (inclusive); expressed as histogram indexes */
    int c0min, c0max;
    int c1min, c1max;
    int c2min, c2max;
    /* The volume (actually 2-norm) of the box */
    JLONG volume;
    /* The number of nonzero histogram cells within this box */
    long colorcount;
} box;

typedef box *boxptr;

/* Memory management structure */
typedef struct {
    void *memory_pool;
    size_t pool_size;
    size_t pool_used;
} memory_mgr;

/* Main input/output structure for select_colors */
typedef struct {
    /* Input parameters */
    int width;                  /* image width */
    int height;                 /* image height */
    JSAMPLE **image_data;       /* RGB image data [height][width*3] */
    int desired_colors;         /* desired number of colors */
    
    /* Output results */
    JSAMPLE colormap[3][MAXNUMCOLORS]; /* generated colormap [component][color_index] */
    int actual_colors;          /* actual number of colors generated */
    
    /* Working storage */
    hist3d histogram;           /* color histogram */
    memory_mgr mem_mgr;         /* memory manager */
    boolean debug_mode;         /* enable debug output */
} select_colors_context;

/* Function declarations */

/* Main function */
int select_colors(select_colors_context *ctx);

/* Helper functions */
int build_histogram(select_colors_context *ctx);
void cleanup_context(select_colors_context *ctx);

/* Memory management */
void* alloc_memory(select_colors_context *ctx, size_t size);
int init_memory_manager(select_colors_context *ctx, size_t pool_size);

/* Utility functions for testing */
select_colors_context* create_test_context(int width, int height, int desired_colors);
void free_test_context(select_colors_context *ctx);
JSAMPLE** create_test_image(int width, int height);
void free_test_image(JSAMPLE **image, int height);

/* Debug macro - can be disabled */
#define DEBUG_TRACE(ctx, fmt, ...) do { \
    if ((ctx)->debug_mode) { \
        printf("[select_colors] " fmt "\n", ##__VA_ARGS__); \
    } \
} while(0)

/* Memory management implementation */
int init_memory_manager(select_colors_context *ctx, size_t pool_size) {
    ctx->mem_mgr.pool_size = pool_size;
    ctx->mem_mgr.pool_used = 0;
    ctx->mem_mgr.memory_pool = malloc(pool_size);
    return (ctx->mem_mgr.memory_pool != NULL) ? 1 : 0;
}

void* alloc_memory(select_colors_context *ctx, size_t size) {
    /* Align size to 8-byte boundary */
    size = (size + 7) & ~7;
    
    if (ctx->mem_mgr.pool_used + size > ctx->mem_mgr.pool_size) {
        return NULL; /* Out of memory */
    }
    
    void *ptr = (char*)ctx->mem_mgr.memory_pool + ctx->mem_mgr.pool_used;
    ctx->mem_mgr.pool_used += size;
    return ptr;
}

/* Helper function: Find the splittable box with the largest color population */
static boxptr find_biggest_color_pop(boxptr boxlist, int numboxes) {
    boxptr boxp;
    int i;
    long maxc = 0;
    boxptr which = NULL;

    for (i = 0, boxp = boxlist; i < numboxes; i++, boxp++) {
        if (boxp->colorcount > maxc && boxp->volume > 0) {
            which = boxp;
            maxc = boxp->colorcount;
        }
    }
    return which;
}

/* Helper function: Find the splittable box with the largest (scaled) volume */
static boxptr find_biggest_volume(boxptr boxlist, int numboxes) {
    boxptr boxp;
    int i;
    JLONG maxv = 0;
    boxptr which = NULL;

    for (i = 0, boxp = boxlist; i < numboxes; i++, boxp++) {
        if (boxp->volume > maxv) {
            which = boxp;
            maxv = boxp->volume;
        }
    }
    return which;
}

/* Helper function: Shrink the min/max bounds of a box to enclose only nonzero elements,
 * and recompute its volume and population */
static void update_box(select_colors_context *ctx, boxptr boxp) {
    hist3d histogram = ctx->histogram;
    histptr histp;
    int c0, c1, c2;
    int c0min, c0max, c1min, c1max, c2min, c2max;
    JLONG dist0, dist1, dist2;
    long ccount;

    c0min = boxp->c0min;  c0max = boxp->c0max;
    c1min = boxp->c1min;  c1max = boxp->c1max;
    c2min = boxp->c2min;  c2max = boxp->c2max;

    if (c0max > c0min)
        for (c0 = c0min; c0 <= c0max; c0++)
            for (c1 = c1min; c1 <= c1max; c1++) {
                histp = &histogram[c0][c1][c2min];
                for (c2 = c2min; c2 <= c2max; c2++)
                    if (*histp++ != 0) {
                        boxp->c0min = c0min = c0;
                        goto have_c0min;
                    }
            }
have_c0min:
    if (c0max > c0min)
        for (c0 = c0max; c0 >= c0min; c0--)
            for (c1 = c1min; c1 <= c1max; c1++) {
                histp = &histogram[c0][c1][c2min];
                for (c2 = c2min; c2 <= c2max; c2++)
                    if (*histp++ != 0) {
                        boxp->c0max = c0max = c0;
                        goto have_c0max;
                    }
            }
have_c0max:
    if (c1max > c1min)
        for (c1 = c1min; c1 <= c1max; c1++)
            for (c0 = c0min; c0 <= c0max; c0++) {
                histp = &histogram[c0][c1][c2min];
                for (c2 = c2min; c2 <= c2max; c2++)
                    if (*histp++ != 0) {
                        boxp->c1min = c1min = c1;
                        goto have_c1min;
                    }
            }
have_c1min:
    if (c1max > c1min)
        for (c1 = c1max; c1 >= c1min; c1--)
            for (c0 = c0min; c0 <= c0max; c0++) {
                histp = &histogram[c0][c1][c2min];
                for (c2 = c2min; c2 <= c2max; c2++)
                    if (*histp++ != 0) {
                        boxp->c1max = c1max = c1;
                        goto have_c1max;
                    }
            }
have_c1max:
    if (c2max > c2min)
        for (c2 = c2min; c2 <= c2max; c2++)
            for (c0 = c0min; c0 <= c0max; c0++) {
                histp = &histogram[c0][c1min][c2];
                for (c1 = c1min; c1 <= c1max; c1++, histp += HIST_C2_ELEMS)
                    if (*histp != 0) {
                        boxp->c2min = c2min = c2;
                        goto have_c2min;
                    }
            }
have_c2min:
    if (c2max > c2min)
        for (c2 = c2max; c2 >= c2min; c2--)
            for (c0 = c0min; c0 <= c0max; c0++) {
                histp = &histogram[c0][c1min][c2];
                for (c1 = c1min; c1 <= c1max; c1++, histp += HIST_C2_ELEMS)
                    if (*histp != 0) {
                        boxp->c2max = c2max = c2;
                        goto have_c2max;
                    }
            }
have_c2max:

    /* Update box volume.
     * We use 2-norm rather than real volume here; this biases the method
     * against making long narrow boxes, and it has the side benefit that
     * a box is splittable iff norm > 0.
     * Since the differences are expressed in histogram-cell units,
     * we have to shift back to JSAMPLE units to get consistent distances;
     * after which, we scale according to the selected distance scale factors.
     */
    dist0 = ((c0max - c0min) << C0_SHIFT) * C0_SCALE;
    dist1 = ((c1max - c1min) << C1_SHIFT) * C1_SCALE;
    dist2 = ((c2max - c2min) << C2_SHIFT) * C2_SCALE;
    boxp->volume = dist0 * dist0 + dist1 * dist1 + dist2 * dist2;

    /* Now scan remaining volume of box and compute population */
    ccount = 0;
    for (c0 = c0min; c0 <= c0max; c0++)
        for (c1 = c1min; c1 <= c1max; c1++) {
            histp = &histogram[c0][c1][c2min];
            for (c2 = c2min; c2 <= c2max; c2++, histp++)
                if (*histp != 0) {
                    ccount++;
                }
        }
    boxp->colorcount = ccount;
}

/* Helper function: Repeatedly select and split the largest box until we have enough boxes */
static int median_cut(select_colors_context *ctx, boxptr boxlist, int numboxes, int desired_colors) {
    int n, lb;
    int c0, c1, c2, cmax;
    boxptr b1, b2;

    while (numboxes < desired_colors) {
        /* Select box to split.
         * Current algorithm: by population for first half, then by volume.
         */
        if (numboxes * 2 <= desired_colors) {
            b1 = find_biggest_color_pop(boxlist, numboxes);
        } else {
            b1 = find_biggest_volume(boxlist, numboxes);
        }
        if (b1 == NULL)             /* no splittable boxes left! */
            break;
        b2 = &boxlist[numboxes];    /* where new box will go */
        /* Copy the color bounds to the new box. */
        b2->c0max = b1->c0max;  b2->c1max = b1->c1max;  b2->c2max = b1->c2max;
        b2->c0min = b1->c0min;  b2->c1min = b1->c1min;  b2->c2min = b1->c2min;
        /* Choose which axis to split the box on.
         * Current algorithm: longest scaled axis.
         * We break ties in favor of green, then red, blue last (RGB order).
         */
        c0 = ((b1->c0max - b1->c0min) << C0_SHIFT) * C0_SCALE;
        c1 = ((b1->c1max - b1->c1min) << C1_SHIFT) * C1_SCALE;
        c2 = ((b1->c2max - b1->c2min) << C2_SHIFT) * C2_SCALE;
        /* We want to break any ties in favor of green, then red, blue last.
         * This code assumes RGB color order.
         */
        cmax = c1;  n = 1; /* Start with green */
        if (c0 > cmax) { cmax = c0;  n = 0; } /* Red */
        if (c2 > cmax) { n = 2; } /* Blue */
        
        /* Choose split point along selected axis, and update box bounds.
         * Current algorithm: split at halfway point.
         * (Since the box has been shrunk to minimum volume,
         * any split will produce two nonempty subboxes.)
         * Note that lb value is max for lower box, so must be < old max.
         */
        switch (n) {
        case 0:
            lb = (b1->c0max + b1->c0min) / 2;
            b1->c0max = lb;
            b2->c0min = lb + 1;
            break;
        case 1:
            lb = (b1->c1max + b1->c1min) / 2;
            b1->c1max = lb;
            b2->c1min = lb + 1;
            break;
        case 2:
            lb = (b1->c2max + b1->c2min) / 2;
            b1->c2max = lb;
            b2->c2min = lb + 1;
            break;
        }
        /* Update stats for boxes */
        update_box(ctx, b1);
        update_box(ctx, b2);
        numboxes++;
    }
    return numboxes;
}

/* Helper function: Compute representative color for a box, put it in colormap[icolor] */
static void compute_color(select_colors_context *ctx, boxptr boxp, int icolor) {
    /* Current algorithm: mean weighted by pixels (not colors) */
    /* Note it is important to get the rounding correct! */
    hist3d histogram = ctx->histogram;
    histptr histp;
    int c0, c1, c2;
    int c0min, c0max, c1min, c1max, c2min, c2max;
    long count;
    long total = 0;
    long c0total = 0;
    long c1total = 0;
    long c2total = 0;

    c0min = boxp->c0min;  c0max = boxp->c0max;
    c1min = boxp->c1min;  c1max = boxp->c1max;
    c2min = boxp->c2min;  c2max = boxp->c2max;

    for (c0 = c0min; c0 <= c0max; c0++)
        for (c1 = c1min; c1 <= c1max; c1++) {
            histp = &histogram[c0][c1][c2min];
            for (c2 = c2min; c2 <= c2max; c2++) {
                if ((count = *histp++) != 0) {
                    total += count;
                    c0total += ((c0 << C0_SHIFT) + ((1 << C0_SHIFT) >> 1)) * count;
                    c1total += ((c1 << C1_SHIFT) + ((1 << C1_SHIFT) >> 1)) * count;
                    c2total += ((c2 << C2_SHIFT) + ((1 << C2_SHIFT) >> 1)) * count;
                }
            }
        }

    ctx->colormap[0][icolor] = (JSAMPLE)((c0total + (total >> 1)) / total);
    ctx->colormap[1][icolor] = (JSAMPLE)((c1total + (total >> 1)) / total);
    ctx->colormap[2][icolor] = (JSAMPLE)((c2total + (total >> 1)) / total);
}

/* Main select_colors function - equivalent to the original but standalone */
static int select_colors_from_histogram(select_colors_context *ctx) {
    boxptr boxlist;
    int numboxes;
    int i;

    /* Allocate workspace for box list */
    boxlist = (boxptr)alloc_memory(ctx, ctx->desired_colors * sizeof(box));
    if (!boxlist) {
        return 0; /* allocation failed */
    }
    
    /* Initialize one box containing whole space */
    numboxes = 1;
    boxlist[0].c0min = 0;
    boxlist[0].c0max = MAXJSAMPLE >> C0_SHIFT;
    boxlist[0].c1min = 0;
    boxlist[0].c1max = MAXJSAMPLE >> C1_SHIFT;
    boxlist[0].c2min = 0;
    boxlist[0].c2max = MAXJSAMPLE >> C2_SHIFT;
    
    /* Shrink it to actually-used volume and set its statistics */
    update_box(ctx, &boxlist[0]);
    
    /* Perform median-cut to produce final box list */
    numboxes = median_cut(ctx, boxlist, numboxes, ctx->desired_colors);
    
    /* Compute the representative color for each box, fill colormap */
    for (i = 0; i < numboxes; i++)
        compute_color(ctx, &boxlist[i], i);
    
    ctx->actual_colors = numboxes;
    DEBUG_TRACE(ctx, "Selected %d colors", numboxes);
    
    return 1; /* success */
}

/* Build histogram from image data */
int build_histogram(select_colors_context *ctx) {
    int row, col;
    JSAMPLE *ptr;
    histptr histp;
    hist3d histogram;
    
    /* Allocate histogram */
    histogram = (hist3d)alloc_memory(ctx, HIST_C0_ELEMS * sizeof(hist2d));
    if (!histogram) return 0;
    
    for (int c0 = 0; c0 < HIST_C0_ELEMS; c0++) {
        histogram[c0] = (hist2d)alloc_memory(ctx, HIST_C1_ELEMS * sizeof(hist1d));
        if (!histogram[c0]) return 0;
        /* Initialize histogram cells to zero */
        memset(histogram[c0], 0, HIST_C1_ELEMS * sizeof(hist1d));
    }
    
    ctx->histogram = histogram;
    
    /* Build histogram by scanning image */
    for (row = 0; row < ctx->height; row++) {
        ptr = ctx->image_data[row];
        for (col = 0; col < ctx->width; col++) {
            /* get pixel value and index into the histogram */
            histp = &histogram[ptr[0] >> C0_SHIFT]
                              [ptr[1] >> C1_SHIFT]
                              [ptr[2] >> C2_SHIFT];
            /* increment, check for overflow and undo increment if so. */
            if (++(*histp) <= 0)
                (*histp)--;
            ptr += 3;
        }
    }
    
    DEBUG_TRACE(ctx, "Built histogram from %dx%d image", ctx->width, ctx->height);
    return 1; /* success */
}

/* Main entry point for standalone select_colors */
int select_colors(select_colors_context *ctx) {
    if (!ctx || !ctx->image_data) {
        return 0; /* invalid input */
    }
    
    /* Initialize memory manager with reasonable pool size */
    size_t pool_size = 1024 * 1024; /* 1MB should be plenty */
    if (!init_memory_manager(ctx, pool_size)) {
        return 0; /* memory allocation failed */
    }
    
    /* Build histogram from image data */
    if (!build_histogram(ctx)) {
        return 0; /* histogram build failed */
    }
    
    /* Perform color selection using median-cut */
    if (!select_colors_from_histogram(ctx)) {
        return 0; /* color selection failed */
    }
    
    return 1; /* success */
}
