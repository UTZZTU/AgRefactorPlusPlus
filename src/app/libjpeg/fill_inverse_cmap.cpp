#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

/*
 * Basic type definitions
 */
typedef uint8_t  sample_t;        /* Sample value type (0-255) */
typedef uint16_t histcell_t;      /* Histogram cell type */
typedef int32_t  distance_t;      /* Distance calculation type */

/* Maximum sample value */
#define MAX_SAMPLE_VALUE  255
#define MAXNUMCOLORS      (MAX_SAMPLE_VALUE + 1)

/*
 * RGB Colormap structure - self-contained colormap representation
 */
typedef struct {
    sample_t *red;      /* Red component array */
    sample_t *green;    /* Green component array */
    sample_t *blue;     /* Blue component array */
    int num_colors;     /* Number of colors in colormap */
} rgb_colormap_t;

/*
 * 3D Histogram structure for inverse colormap caching
 * 
 * The histogram uses reduced precision:
 * - Red:   5 bits (32 levels)
 * - Green: 6 bits (64 levels) 
 * - Blue:  5 bits (32 levels)
 *
 * Each cell stores the closest colormap index + 1 (0 means uncached)
 */
typedef struct {
    histcell_t ***cells;    /* 3D array: cells[r][g][b] */
    int allocated;          /* 1 if memory was allocated, 0 otherwise */
} histogram3d_t;

/*
 * Configuration constants
 * 
 * These match the libjpeg-turbo implementation for compatibility
 */
#define HIST_C0_BITS    5       /* Red precision bits */
#define HIST_C1_BITS    6       /* Green precision bits */ 
#define HIST_C2_BITS    5       /* Blue precision bits */

#define HIST_C0_ELEMS   (1 << HIST_C0_BITS)    /* 32 */
#define HIST_C1_ELEMS   (1 << HIST_C1_BITS)    /* 64 */
#define HIST_C2_ELEMS   (1 << HIST_C2_BITS)    /* 32 */

/* Bit shifts to convert full precision to histogram coordinates */
#define C0_SHIFT        (8 - HIST_C0_BITS)     /* 3 */
#define C1_SHIFT        (8 - HIST_C1_BITS)     /* 2 */
#define C2_SHIFT        (8 - HIST_C2_BITS)     /* 3 */

/* Update box configuration - process 4x8x4 cells at a time */
#define BOX_C0_LOG      (HIST_C0_BITS - 3)     /* 2 */
#define BOX_C1_LOG      (HIST_C1_BITS - 3)     /* 3 */
#define BOX_C2_LOG      (HIST_C2_BITS - 3)     /* 2 */

#define BOX_C0_ELEMS    (1 << BOX_C0_LOG)      /* 4 */
#define BOX_C1_ELEMS    (1 << BOX_C1_LOG)      /* 8 */
#define BOX_C2_ELEMS    (1 << BOX_C2_LOG)      /* 4 */

#define BOX_C0_SHIFT    (C0_SHIFT + BOX_C0_LOG) /* 5 */
#define BOX_C1_SHIFT    (C1_SHIFT + BOX_C1_LOG) /* 5 */
#define BOX_C2_SHIFT    (C2_SHIFT + BOX_C2_LOG) /* 5 */

/* RGB distance scaling factors for perceptual weighting */
#define R_SCALE         2       /* Red scale factor */
#define G_SCALE         3       /* Green scale factor (most important) */
#define B_SCALE         1       /* Blue scale factor (least important) */

/*
 * Public API functions
 */

/* Initialize a 3D histogram structure */
int histogram3d_init(histogram3d_t *hist);

/* Free a 3D histogram structure */
void histogram3d_free(histogram3d_t *hist);

/* Create RGB colormap from separate color arrays */
int rgb_colormap_create(rgb_colormap_t *colormap, 
                        const sample_t *red, const sample_t *green, const sample_t *blue,
                        int num_colors);

/* Free RGB colormap */
void rgb_colormap_free(rgb_colormap_t *colormap);

/*
 * Core algorithm functions
 */

/* Find nearby colormap colors for a given update box */
int find_nearby_colors_standalone(const rgb_colormap_t *colormap,
                                  int minc0, int minc1, int minc2,
                                  sample_t *colorlist);

/* Find best colors for each cell in update box */
void find_best_colors_standalone(const rgb_colormap_t *colormap,
                                int minc0, int minc1, int minc2,
                                int numcolors, const sample_t *colorlist,
                                sample_t *bestcolor);

/* Fill inverse colormap cache for the update box containing (c0,c1,c2) */
void fill_inverse_cmap(const rgb_colormap_t *colormap,
                            histogram3d_t *histogram,
                            int c0, int c1, int c2);

/*
 * Convenience functions
 */

/* Convert RGB values to histogram coordinates */
static inline void rgb_to_hist_coords(sample_t r, sample_t g, sample_t b,
                                     int *c0, int *c1, int *c2)
{
    *c0 = r >> C0_SHIFT;
    *c1 = g >> C1_SHIFT; 
    *c2 = b >> C2_SHIFT;
}

/* Look up cached colormap index, returns -1 if not cached */
static inline int histogram3d_lookup(const histogram3d_t *hist, 
                                    int c0, int c1, int c2)
{
    if (c0 < 0 || c0 >= HIST_C0_ELEMS ||
        c1 < 0 || c1 >= HIST_C1_ELEMS ||
        c2 < 0 || c2 >= HIST_C2_ELEMS) {
        return -1;
    }
    
    histcell_t value = hist->cells[c0][c1][c2];
    return (value == 0) ? -1 : (int)(value - 1);
}

/* Clear histogram cache */
void histogram3d_clear(histogram3d_t *hist);

/*
 * Helper macro for color scaling based on component
 */
#define GET_SCALE(component) ((component == 0) ? R_SCALE : \
                             (component == 1) ? G_SCALE : B_SCALE)

/*
 * Initialize a 3D histogram structure
 */
int histogram3d_init(histogram3d_t *hist)
{
    int i, j;
    
    if (!hist) return 0;
    
    /* Allocate the 3D array structure */
    hist->cells = (histcell_t***)malloc(HIST_C0_ELEMS * sizeof(histcell_t**));
    if (!hist->cells) return 0;
    
    /* Initialize all pointers to NULL for safe cleanup */
    for (i = 0; i < HIST_C0_ELEMS; i++) {
        hist->cells[i] = NULL;
    }
    
    /* Allocate second level arrays */
    for (i = 0; i < HIST_C0_ELEMS; i++) {
        hist->cells[i] = (histcell_t**)malloc(HIST_C1_ELEMS * sizeof(histcell_t*));
        if (!hist->cells[i]) {
            histogram3d_free(hist);
            return 0;
        }
        
        /* Initialize all pointers to NULL */
        for (j = 0; j < HIST_C1_ELEMS; j++) {
            hist->cells[i][j] = NULL;
        }
        
        /* Allocate third level arrays */
        for (j = 0; j < HIST_C1_ELEMS; j++) {
            hist->cells[i][j] = (histcell_t*)calloc(HIST_C2_ELEMS, sizeof(histcell_t));
            if (!hist->cells[i][j]) {
                histogram3d_free(hist);
                return 0;
            }
        }
    }
    
    hist->allocated = 1;
    return 1;
}

/*
 * Free a 3D histogram structure
 */
void histogram3d_free(histogram3d_t *hist)
{
    int i, j;
    
    if (!hist || !hist->allocated) return;
    
    if (hist->cells) {
        for (i = 0; i < HIST_C0_ELEMS; i++) {
            if (hist->cells[i]) {
                for (j = 0; j < HIST_C1_ELEMS; j++) {
                    free(hist->cells[i][j]);
                }
                free(hist->cells[i]);
            }
        }
        free(hist->cells);
    }
    
    hist->cells = NULL;
    hist->allocated = 0;
}

/*
 * Create RGB colormap from separate color arrays
 */
int rgb_colormap_create(rgb_colormap_t *colormap,
                       const sample_t *red, const sample_t *green, const sample_t *blue,
                       int num_colors)
{
    if (!colormap || !red || !green || !blue || num_colors <= 0 || num_colors > MAXNUMCOLORS) {
        return 0;
    }
    
    /* Allocate memory for color arrays */
    colormap->red = (sample_t*)malloc(num_colors * sizeof(sample_t));
    colormap->green = (sample_t*)malloc(num_colors * sizeof(sample_t));
    colormap->blue = (sample_t*)malloc(num_colors * sizeof(sample_t));
    
    if (!colormap->red || !colormap->green || !colormap->blue) {
        rgb_colormap_free(colormap);
        return 0;
    }
    
    /* Copy color data */
    memcpy(colormap->red, red, num_colors * sizeof(sample_t));
    memcpy(colormap->green, green, num_colors * sizeof(sample_t));
    memcpy(colormap->blue, blue, num_colors * sizeof(sample_t));
    
    colormap->num_colors = num_colors;
    return 1;
}

/*
 * Free RGB colormap
 */
void rgb_colormap_free(rgb_colormap_t *colormap)
{
    if (!colormap) return;
    
    free(colormap->red);
    free(colormap->green);
    free(colormap->blue);
    
    colormap->red = NULL;
    colormap->green = NULL;
    colormap->blue = NULL;
    colormap->num_colors = 0;
}

/*
 * Clear histogram cache
 */
void histogram3d_clear(histogram3d_t *hist)
{
    int i, j;
    
    if (!hist || !hist->allocated || !hist->cells) return;
    
    for (i = 0; i < HIST_C0_ELEMS; i++) {
        if (hist->cells[i]) {
            for (j = 0; j < HIST_C1_ELEMS; j++) {
                if (hist->cells[i][j]) {
                    memset(hist->cells[i][j], 0, HIST_C2_ELEMS * sizeof(histcell_t));
                }
            }
        }
    }
}

/*
 * Find nearby colormap colors for a given update box
 * 
 * This function implements Heckbert's "locally sorted search" criterion
 * to efficiently eliminate distant colors before detailed distance computation.
 */
int find_nearby_colors_standalone(const rgb_colormap_t *colormap,
                                 int minc0, int minc1, int minc2,
                                 sample_t *colorlist)
{
    int numcolors = colormap->num_colors;
    int maxc0, maxc1, maxc2;
    int centerc0, centerc1, centerc2;
    int i, x, ncolors;
    distance_t minmaxdist, min_dist, max_dist, tdist;
    distance_t mindist[MAXNUMCOLORS];  /* min distance to colormap entry i */

    /* Compute true coordinates of update box's upper corner and center */
    maxc0 = minc0 + ((1 << BOX_C0_SHIFT) - (1 << C0_SHIFT));
    centerc0 = (minc0 + maxc0) >> 1;
    maxc1 = minc1 + ((1 << BOX_C1_SHIFT) - (1 << C1_SHIFT));
    centerc1 = (minc1 + maxc1) >> 1;
    maxc2 = minc2 + ((1 << BOX_C2_SHIFT) - (1 << C2_SHIFT));
    centerc2 = (minc2 + maxc2) >> 1;

    minmaxdist = 0x7FFFFFFFL;

    for (i = 0; i < numcolors; i++) {
        /* Compute squared distance for red component */
        x = colormap->red[i];
        if (x < minc0) {
            tdist = (x - minc0) * R_SCALE;
            min_dist = tdist * tdist;
            tdist = (x - maxc0) * R_SCALE;
            max_dist = tdist * tdist;
        } else if (x > maxc0) {
            tdist = (x - maxc0) * R_SCALE;
            min_dist = tdist * tdist;
            tdist = (x - minc0) * R_SCALE;
            max_dist = tdist * tdist;
        } else {
            /* within cell range so no contribution to min_dist */
            min_dist = 0;
            if (x <= centerc0) {
                tdist = (x - maxc0) * R_SCALE;
                max_dist = tdist * tdist;
            } else {
                tdist = (x - minc0) * R_SCALE;
                max_dist = tdist * tdist;
            }
        }

        /* Add green component distance */
        x = colormap->green[i];
        if (x < minc1) {
            tdist = (x - minc1) * G_SCALE;
            min_dist += tdist * tdist;
            tdist = (x - maxc1) * G_SCALE;
            max_dist += tdist * tdist;
        } else if (x > maxc1) {
            tdist = (x - maxc1) * G_SCALE;
            min_dist += tdist * tdist;
            tdist = (x - minc1) * G_SCALE;
            max_dist += tdist * tdist;
        } else {
            /* within cell range so no contribution to min_dist */
            if (x <= centerc1) {
                tdist = (x - maxc1) * G_SCALE;
                max_dist += tdist * tdist;
            } else {
                tdist = (x - minc1) * G_SCALE;
                max_dist += tdist * tdist;
            }
        }

        /* Add blue component distance */
        x = colormap->blue[i];
        if (x < minc2) {
            tdist = (x - minc2) * B_SCALE;
            min_dist += tdist * tdist;
            tdist = (x - maxc2) * B_SCALE;
            max_dist += tdist * tdist;
        } else if (x > maxc2) {
            tdist = (x - maxc2) * B_SCALE;
            min_dist += tdist * tdist;
            tdist = (x - minc2) * B_SCALE;
            max_dist += tdist * tdist;
        } else {
            /* within cell range so no contribution to min_dist */
            if (x <= centerc2) {
                tdist = (x - maxc2) * B_SCALE;
                max_dist += tdist * tdist;
            } else {
                tdist = (x - minc2) * B_SCALE;
                max_dist += tdist * tdist;
            }
        }

        mindist[i] = min_dist;      /* save away the results */
        if (max_dist < minmaxdist)
            minmaxdist = max_dist;
    }

    /* Select colors that are candidates for the nearest entry */
    ncolors = 0;
    for (i = 0; i < numcolors; i++) {
        if (mindist[i] <= minmaxdist)
            colorlist[ncolors++] = (sample_t)i;
    }
    return ncolors;
}

/*
 * Find best colors for each cell in update box
 *
 * Uses Thomas' incremental distance calculation method from Graphics Gems II
 * to efficiently compute distances from candidate colors to all cells in the box.
 */
void find_best_colors_standalone(const rgb_colormap_t *colormap,
                                int minc0, int minc1, int minc2,
                                int numcolors, const sample_t *colorlist,
                                sample_t *bestcolor)
{
    int ic0, ic1, ic2;
    int i, icolor;
    distance_t *bptr;               /* pointer into bestdist[] array */
    sample_t *cptr;                 /* pointer into bestcolor[] array */
    distance_t dist0, dist1;        /* initial distance values */
    distance_t dist2;               /* current distance in inner loop */
    distance_t xx0, xx1;            /* distance increments */
    distance_t xx2;
    distance_t inc0, inc1, inc2;    /* initial values for increments */
    /* This array holds the distance to the nearest-so-far color for each cell */
    distance_t bestdist[BOX_C0_ELEMS * BOX_C1_ELEMS * BOX_C2_ELEMS];

    /* Nominal steps between cell centers */
    #define STEP_C0  ((1 << C0_SHIFT) * R_SCALE)
    #define STEP_C1  ((1 << C1_SHIFT) * G_SCALE)
    #define STEP_C2  ((1 << C2_SHIFT) * B_SCALE)

    /* Initialize best-distance for each cell of the update box */
    bptr = bestdist;
    for (i = BOX_C0_ELEMS * BOX_C1_ELEMS * BOX_C2_ELEMS - 1; i >= 0; i--)
        *bptr++ = 0x7FFFFFFFL;

    /* For each color selected by find_nearby_colors, compute its distance
     * to the center of each cell in the box using Thomas' incremental method.
     */
    for (i = 0; i < numcolors; i++) {
        icolor = colorlist[i];
        
        /* Compute (square of) distance from minc0/c1/c2 to this color */
        inc0 = (minc0 - colormap->red[icolor]) * R_SCALE;
        dist0 = inc0 * inc0;
        inc1 = (minc1 - colormap->green[icolor]) * G_SCALE;
        dist0 += inc1 * inc1;
        inc2 = (minc2 - colormap->blue[icolor]) * B_SCALE;
        dist0 += inc2 * inc2;
        
        /* Form the initial difference increments for Thomas' method */
        inc0 = inc0 * (2 * STEP_C0) + STEP_C0 * STEP_C0;
        inc1 = inc1 * (2 * STEP_C1) + STEP_C1 * STEP_C1;
        inc2 = inc2 * (2 * STEP_C2) + STEP_C2 * STEP_C2;
        
        /* Now loop over all cells in box, updating distance per Thomas method */
        bptr = bestdist;
        cptr = bestcolor;
        xx0 = inc0;
        for (ic0 = BOX_C0_ELEMS - 1; ic0 >= 0; ic0--) {
            dist1 = dist0;
            xx1 = inc1;
            for (ic1 = BOX_C1_ELEMS - 1; ic1 >= 0; ic1--) {
                dist2 = dist1;
                xx2 = inc2;
                for (ic2 = BOX_C2_ELEMS - 1; ic2 >= 0; ic2--) {
                    if (dist2 < *bptr) {
                        *bptr = dist2;
                        *cptr = (sample_t)icolor;
                    }
                    dist2 += xx2;
                    xx2 += 2 * STEP_C2 * STEP_C2;
                    bptr++;
                    cptr++;
                }
                dist1 += xx1;
                xx1 += 2 * STEP_C1 * STEP_C1;
            }
            dist0 += xx0;
            xx0 += 2 * STEP_C0 * STEP_C0;
        }
    }
    
    #undef STEP_C0
    #undef STEP_C1
    #undef STEP_C2
}

/*
 * Fill inverse colormap cache for the update box containing (c0,c1,c2)
 *
 * This is the main function that implements the complete algorithm:
 * 1. Determines the update box that contains the given histogram coordinates
 * 2. Uses Heckbert's criterion to find nearby candidate colors
 * 3. Uses Thomas' method to find the best color for each cell in the box
 * 4. Caches the results in the 3D histogram
 */
void fill_inverse_cmap(const rgb_colormap_t *colormap,
                                 histogram3d_t *histogram,
                                 int c0, int c1, int c2)
{
    int minc0, minc1, minc2;        /* lower left corner of update box */
    int ic0, ic1, ic2;
    sample_t *cptr;                 /* pointer into bestcolor[] array */
    histcell_t *cachep;             /* pointer into main cache array */
    /* This array lists the candidate colormap indexes */
    sample_t colorlist[MAXNUMCOLORS];
    int numcolors;                  /* number of candidate colors */
    /* This array holds the actually closest colormap index for each cell */
    sample_t bestcolor[BOX_C0_ELEMS * BOX_C1_ELEMS * BOX_C2_ELEMS];

    /* Convert cell coordinates to update box ID */
    c0 >>= BOX_C0_LOG;
    c1 >>= BOX_C1_LOG;
    c2 >>= BOX_C2_LOG;

    /* Compute true coordinates of update box's origin corner.
     * Actually we compute the coordinates of the center of the corner
     * histogram cell, which are the lower bounds of the volume we care about.
     */
    minc0 = (c0 << BOX_C0_SHIFT) + ((1 << C0_SHIFT) >> 1);
    minc1 = (c1 << BOX_C1_SHIFT) + ((1 << C1_SHIFT) >> 1);
    minc2 = (c2 << BOX_C2_SHIFT) + ((1 << C2_SHIFT) >> 1);

    /* Determine which colormap entries are close enough to be candidates
     * for the nearest entry to some cell in the update box.
     */
    numcolors = find_nearby_colors_standalone(colormap, minc0, minc1, minc2, colorlist);

    /* Determine the actually nearest colors. */
    find_best_colors_standalone(colormap, minc0, minc1, minc2, numcolors, colorlist, bestcolor);

    /* Save the best color numbers (plus 1) in the main cache array */
    c0 <<= BOX_C0_LOG;              /* convert ID back to base cell indexes */
    c1 <<= BOX_C1_LOG;
    c2 <<= BOX_C2_LOG;
    cptr = bestcolor;
    for (ic0 = 0; ic0 < BOX_C0_ELEMS; ic0++) {
        for (ic1 = 0; ic1 < BOX_C1_ELEMS; ic1++) {
            cachep = &histogram->cells[c0 + ic0][c1 + ic1][c2];
            for (ic2 = 0; ic2 < BOX_C2_ELEMS; ic2++) {
                *cachep++ = (histcell_t)((*cptr++) + 1);
            }
        }
    }
}
