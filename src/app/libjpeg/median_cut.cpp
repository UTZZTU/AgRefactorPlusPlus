#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>


/* Fixed configuration - matches BITS_IN_JSAMPLE == 8 */
#define MAXJSAMPLE    255
#define MAXNUMCOLORS  (MAXJSAMPLE + 1)

/* Histogram precision constants */
#define HIST_C0_BITS  5         /* bits of precision in R histogram */
#define HIST_C1_BITS  6         /* bits of precision in G histogram */
#define HIST_C2_BITS  5         /* bits of precision in B histogram */

/* Number of elements along histogram axes */
#define HIST_C0_ELEMS  (1 << HIST_C0_BITS)
#define HIST_C1_ELEMS  (1 << HIST_C1_BITS)
#define HIST_C2_ELEMS  (1 << HIST_C2_BITS)

/* Shift amounts to get histogram index from pixel value */
#define C0_SHIFT  (8 - HIST_C0_BITS)  /* BITS_IN_JSAMPLE = 8 */
#define C1_SHIFT  (8 - HIST_C1_BITS)
#define C2_SHIFT  (8 - HIST_C2_BITS)

/* Color scaling factors for distance calculation */
#define R_SCALE  2              /* scale R distances by this much */
#define G_SCALE  3              /* scale G distances by this much */
#define B_SCALE  1              /* and B by this much */

/* Histogram cell type */
typedef uint16_t histcell;
typedef histcell *histptr;

/* 3D histogram structure */
typedef histcell hist1d[HIST_C2_ELEMS];
typedef hist1d *hist2d;
typedef hist2d *hist3d;

/* Box structure for median-cut algorithm */
typedef struct {
    /* The bounds of the box (inclusive); expressed as histogram indexes */
    int c0min, c0max;
    int c1min, c1max;
    int c2min, c2max;
    /* The volume (actually 2-norm) of the box */
    int64_t volume;
    /* The number of nonzero histogram cells within this box */
    long colorcount;
} median_cut_box;

typedef median_cut_box *median_cut_boxptr;

/* Color format type - simplified from JPEG color space */
typedef enum {
    COLOR_RGB = 0,    /* Standard RGB (R=0, G=1, B=2) */
    COLOR_BGR = 1     /* BGR format (B=0, G=1, R=2) */
} color_format_t;

/* Context structure for standalone median-cut operation */
typedef struct {
    /* Input parameters */
    color_format_t color_format;
    int width, height;
    
    /* Histogram data */
    hist3d histogram;
    
    /* Results */
    int actual_colors;
    uint8_t colormap[3][MAXNUMCOLORS];  /* [component][color_index] */
} median_cut_context;

/**
 * Main standalone median_cut function - refactored version
 * This is the core algorithm extracted and made standalone
 * 
 * Parameters:
 *   histogram: 3D histogram of color usage
 *   boxlist: array of boxes to split
 *   numboxes: current number of boxes
 *   desired_colors: target number of colors
 *   color_format: RGB or BGR layout
 * 
 * Returns: final number of boxes/colors
 */
int median_cut(hist3d histogram, median_cut_boxptr boxlist, 
                  int numboxes, int desired_colors, color_format_t color_format);

/**
 * Helper function to find box with largest color population
 */
median_cut_boxptr find_biggest_color_pop(median_cut_boxptr boxlist, int numboxes);

/**
 * Helper function to find box with largest volume
 */
median_cut_boxptr find_biggest_volume(median_cut_boxptr boxlist, int numboxes);

/**
 * Helper function to update box statistics
 */
void update_box(hist3d histogram, median_cut_boxptr boxp);

/**
 * Convert color format to component indices
 */
void get_component_indices(color_format_t format, int *r_idx, int *g_idx, int *b_idx);

/* Static color scales array for different formats */
static const int c_scales[3] = { R_SCALE, G_SCALE, B_SCALE };

/**
 * Convert color format to component indices
 */
void get_component_indices(color_format_t format, int *r_idx, int *g_idx, int *b_idx) {
    switch (format) {
        case COLOR_RGB:
            *r_idx = 0; *g_idx = 1; *b_idx = 2;
            break;
        case COLOR_BGR:
            *b_idx = 0; *g_idx = 1; *r_idx = 2;
            break;
        default:
            *r_idx = 0; *g_idx = 1; *b_idx = 2;  /* Default to RGB */
            break;
    }
}

/**
 * Get color scale for component based on format
 */
static int get_color_scale(color_format_t format, int component) {
    int r_idx, g_idx, b_idx;
    get_component_indices(format, &r_idx, &g_idx, &b_idx);
    
    if (component == r_idx) return R_SCALE;
    if (component == g_idx) return G_SCALE;
    if (component == b_idx) return B_SCALE;
    return 1;  /* Default scale */
}

/**
 * Find the splittable box with the largest color population
 * Returns NULL if no splittable boxes remain
 */
median_cut_boxptr find_biggest_color_pop(median_cut_boxptr boxlist, int numboxes) {
    median_cut_boxptr boxp;
    int i;
    long maxc = 0;
    median_cut_boxptr which = NULL;

    for (i = 0, boxp = boxlist; i < numboxes; i++, boxp++) {
        if (boxp->colorcount > maxc && boxp->volume > 0) {
            which = boxp;
            maxc = boxp->colorcount;
        }
    }
    return which;
}

/**
 * Find the splittable box with the largest (scaled) volume
 * Returns NULL if no splittable boxes remain
 */
median_cut_boxptr find_biggest_volume(median_cut_boxptr boxlist, int numboxes) {
    median_cut_boxptr boxp;
    int i;
    int64_t maxv = 0;
    median_cut_boxptr which = NULL;

    for (i = 0, boxp = boxlist; i < numboxes; i++, boxp++) {
        if (boxp->volume > maxv) {
            which = boxp;
            maxv = boxp->volume;
        }
    }
    return which;
}

/**
 * Shrink the min/max bounds of a box to enclose only nonzero elements,
 * and recompute its volume and population
 */
void update_box(hist3d histogram, median_cut_boxptr boxp) {
    histptr histp;
    int c0, c1, c2;
    int c0min, c0max, c1min, c1max, c2min, c2max;
    int64_t dist0, dist1, dist2;
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
    dist0 = ((c0max - c0min) << C0_SHIFT) * R_SCALE;
    dist1 = ((c1max - c1min) << C1_SHIFT) * G_SCALE;
    dist2 = ((c2max - c2min) << C2_SHIFT) * B_SCALE;
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

/**
 * Main median_cut algorithm - refactored standalone version
 * Repeatedly select and split the largest box until we have enough boxes
 */
int median_cut(hist3d histogram, median_cut_boxptr boxlist, 
                  int numboxes, int desired_colors, color_format_t color_format) {
    int n, lb;
    int c0, c1, c2, cmax;
    median_cut_boxptr b1, b2;
    int r_idx, g_idx, b_idx;
    
    get_component_indices(color_format, &r_idx, &g_idx, &b_idx);

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
         * See notes in update_box about scaling distances.
         */
        c0 = ((b1->c0max - b1->c0min) << C0_SHIFT) * get_color_scale(color_format, 0);
        c1 = ((b1->c1max - b1->c1min) << C1_SHIFT) * get_color_scale(color_format, 1);
        c2 = ((b1->c2max - b1->c2min) << C2_SHIFT) * get_color_scale(color_format, 2);
        /* We want to break any ties in favor of green, then red, blue last.
         * This code does the right thing for R,G,B or B,G,R color orders only.
         */
        if (r_idx == 0) {  /* RGB format */
            cmax = c1;  n = 1;
            if (c0 > cmax) { cmax = c0;  n = 0; }
            if (c2 > cmax) { n = 2; }
        } else {  /* BGR format */
            cmax = c1;  n = 1;
            if (c2 > cmax) { cmax = c2;  n = 2; }
            if (c0 > cmax) { n = 0; }
        }
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
        update_box(histogram, b1);
        update_box(histogram, b2);
        numboxes++;
    }
    return numboxes;
}
