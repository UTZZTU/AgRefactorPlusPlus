#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

/* Fixed configuration - matches 8-bit JPEG samples */
#define DCTSIZE         8           /* The basic DCT block is 8x8 samples */
#define DCTSIZE2        64          /* DCTSIZE squared */
#define MAXJSAMPLE      255
#define CENTERJSAMPLE   128

/* Mathematical constants for IDCT */
#define CONST_BITS      13
#define PASS1_BITS      2

/* Pre-calculated DCT constants (scaled by 2^CONST_BITS) - with  suffix */
#define FIX_0_298631336  ((JLONG)2446)     /* FIX(0.298631336) */
#define FIX_0_390180644  ((JLONG)3196)     /* FIX(0.390180644) */
#define FIX_0_541196100  ((JLONG)4433)     /* FIX(0.541196100) */
#define FIX_0_765366865  ((JLONG)6270)     /* FIX(0.765366865) */
#define FIX_0_899976223  ((JLONG)7373)     /* FIX(0.899976223) */
#define FIX_1_175875602  ((JLONG)9633)     /* FIX(1.175875602) */
#define FIX_1_501321110  ((JLONG)12299)    /* FIX(1.501321110) */
#define FIX_1_847759065  ((JLONG)15137)    /* FIX(1.847759065) */
#define FIX_1_961570560  ((JLONG)16069)    /* FIX(1.961570560) */
#define FIX_2_053119869  ((JLONG)16819)    /* FIX(2.053119869) */
#define FIX_2_562915447  ((JLONG)20995)    /* FIX(2.562915447) */
#define FIX_3_072711026  ((JLONG)25172)    /* FIX(3.072711026) */

/* Additional constants for different block sizes */
#define FIX_0_082392200  ((JLONG)675)      /* FIX(0.082392200) */
#define FIX_0_113262861  ((JLONG)928)      /* FIX(0.113262861) */
#define FIX_0_195090322  ((JLONG)1597)     /* FIX(0.195090322) */
#define FIX_0_277785116  ((JLONG)2276)     /* FIX(0.277785116) */
#define FIX_0_337496120  ((JLONG)2766)     /* FIX(0.337496120) */
#define FIX_0_368899322  ((JLONG)3022)     /* FIX(0.368899322) */
#define FIX_0_466553967  ((JLONG)3823)     /* FIX(0.466553967) */
#define FIX_0_575808191  ((JLONG)4717)     /* FIX(0.575808191) */
#define FIX_0_601344887  ((JLONG)4926)     /* FIX(0.601344887) */
#define FIX_0_707106781  ((JLONG)5793)     /* FIX(0.707106781) */
#define FIX_1_224744871  ((JLONG)10033)    /* FIX(1.224744871) */

/* Utility macros for fixed-point arithmetic - with  suffix to avoid conflicts */
#define ONE                 ((JLONG)1)
#define CONST_SCALE         (ONE << CONST_BITS)
#define FIX(x)              ((JLONG)((x) * CONST_SCALE + 0.5))
#define MULTIPLY(var, const) ((var) * (const))
#define DEQUANTIZE(coef, quantval) (((int16_t)(coef)) * (quantval))
#define LEFT_SHIFT(x, n)   ((x) << (n))
#define RIGHT_SHIFT(x, n)  ((x) >> (n))
#define DESCALE(x, n)      RIGHT_SHIFT((x) + (ONE << ((n) - 1)), n)

/* Sample value constants */
#define MAXJSAMPLE     255         /* Maximum sample value */
#define CENTERJSAMPLE  128         /* Center sample value */

/* Range limiting */
#define RANGE_MASK      (MAXJSAMPLE * 4 + 3)  /* 2 bits wider than legal samples */

/* Data types for refactored IDCT (use original libjpeg types in comparison) */
typedef int16_t JCOEF;           /* DCT coefficients */
typedef int16_t ISLOW_MULT_TYPE; /* Quantization multipliers */ 
typedef uint8_t JSAMPLE;         /* Image samples */
typedef long JLONG;              /* Must match original JLONG */

/* IDCT block sizes supported */
typedef enum {
    IDCT_1x1 = 1,
    IDCT_2x2 = 2,
    IDCT_3x3 = 3,
    IDCT_4x4 = 4,
    IDCT_5x5 = 5,
    IDCT_6x6 = 6,
    IDCT_7x7 = 7,
    IDCT_8x8 = 8,
    IDCT_9x9 = 9,
    IDCT_10x10 = 10,
    IDCT_11x11 = 11,
    IDCT_12x12 = 12,
    IDCT_13x13 = 13,
    IDCT_14x14 = 14,
    IDCT_15x15 = 15,
    IDCT_16x16 = 16
} idct_size_t;

/* IDCT context structure */
typedef struct {
    /* Configuration */
    idct_size_t block_size;
    int scaled_size;
    
    /* Quantization table (dequantization multipliers) */
    ISLOW_MULT_TYPE quant_table[DCTSIZE2];
    
    /* Range limiting table */
    JSAMPLE *range_limit_table;
    JSAMPLE range_limit_base[5 * (MAXJSAMPLE + 1) + CENTERJSAMPLE];
} idct_context;

/* Function prototypes */

/**
 * Initialize IDCT context with quantization table
 * Returns 0 on success, -1 on failure
 */
int idct_init(idct_context *ctx, idct_size_t size, const unsigned int *quant_table);

/**
 * Cleanup IDCT context
 */
void idct_cleanup(idct_context *ctx);

/**
 * Main 8x8 inverse DCT function - refactored version
 * Performs dequantization and inverse DCT on one block of coefficients
 * 
 * Parameters:
 *   ctx: IDCT context with quantization table and range limits
 *   coef_block: Input DCT coefficients (8x8 block)
 *   output_buf: Output sample buffer (8x8 block)
 *   output_stride: Stride for output buffer
 * 
 * Returns: 0 on success, -1 on failure
 */
int idct_8x8(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);

/**
 * Scaled inverse DCT functions for different output sizes
 */
int idct_1x1(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);
int idct_2x2(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);
int idct_3x3(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);
int idct_4x4(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);
int idct_5x5(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);
int idct_6x6(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);
int idct_7x7(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);

/**
 * Enhanced inverse DCT functions for upscaling
 */
int idct_9x9(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride);
int idct_10x10(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride);
int idct_11x11(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride);
int idct_12x12(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride);
int idct_13x13(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride);
int idct_14x14(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride);
int idct_15x15(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride);
int idct_16x16(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride);

/**
 * Generic IDCT function that dispatches to appropriate size-specific function
 */
int idct_generic(idct_context *ctx, const JCOEF *coef_block,
                    JSAMPLE *output_buf, int output_stride);

/**
 * Utility functions for testing and verification
 */

/**
 * Create standard JPEG quantization table
 */
void create_standard_quant_table(unsigned int *table, int quality_factor);

/**
 * Compare two output blocks for testing
 */
double compare_idct_output(const JSAMPLE *block1, const JSAMPLE *block2,
                             int size1, int size2, int stride1, int stride2);

/**
 * Validate IDCT output for correctness
 */
int validate_idct_output(const JSAMPLE *output_block, int size, int stride);

/* Standard JPEG quantization table (quality factor ~50) */
static const unsigned int std_jpeg_quant_table[DCTSIZE2] = {
    16,  11,  12,  14,  12,  10,  16,  14,
    13,  14,  18,  17,  16,  19,  24,  40,
    26,  24,  22,  22,  24,  49,  35,  37,
    29,  40,  58,  51,  61,  60,  57,  51,
    56,  55,  64,  72,  92,  78,  64,  68,
    87,  69,  55,  56,  80, 109,  81,  87,
    95,  98, 103, 104, 103,  62,  77, 113,
    121, 112, 100, 120,  92, 101, 103,  99
};

/*
 * Initialize IDCT context with quantization table
 */
int idct_init(idct_context *ctx, idct_size_t size, const unsigned int *quant_table) {
    int i;
    JSAMPLE *table;
    
    if (!ctx) return -1;
    
    ctx->block_size = size;
    ctx->scaled_size = (int)size;
    
    /* Set up quantization table (convert to dequantization multipliers) */
    if (quant_table) {
        for (i = 0; i < DCTSIZE2; i++) {
            /* Convert quantization values to IDCT multiplier format */
            /* Match original libjpeg-turbo: use quantval*8 then 8192L reciprocal */
            long scaled = quant_table[i] * 8;  /* Match original *8 scaling */
            if (scaled < 1) scaled = 1;
            if (scaled > 65535*8) scaled = 65535*8;
            
            /* Convert to multiplier (reciprocal * scale) - match original 8192L */
            ctx->quant_table[i] = (ISLOW_MULT_TYPE)
                ((CONST_SCALE + scaled/2) / scaled);
        }
    } else {
        /* Use standard quantization table */
        for (i = 0; i < DCTSIZE2; i++) {
            /* Match original scaling: quantval*8 then 8192L reciprocal */
            long scaled = std_jpeg_quant_table[i] * 8;
            ctx->quant_table[i] = (ISLOW_MULT_TYPE)
                ((CONST_SCALE + scaled/2) / scaled);
        }
    }
    
    /* Set up sample range limit table (exactly like original libjpeg-turbo) */
    table = ctx->range_limit_base;
    table += (MAXJSAMPLE + 1); /* Allow negative subscripts of simple table */
    ctx->range_limit_table = table;
    
    /* First segment of "simple" table: limit[x] = 0 for x < 0 */
    for (i = 0; i < (MAXJSAMPLE + 1); i++) {
        table[-(MAXJSAMPLE + 1) + i] = 0;
    }
    
    /* Main part of "simple" table: limit[x] = x */
    for (i = 0; i <= MAXJSAMPLE; i++) {
        table[i] = (JSAMPLE)i;
    }
    
    table += CENTERJSAMPLE;  /* Point to where post-IDCT table starts */
    
    /* End of simple table, rest of first half of post-IDCT table */
    for (i = CENTERJSAMPLE; i < 2 * (MAXJSAMPLE + 1); i++) {
        table[i] = MAXJSAMPLE;
    }
    
    /* Second half of post-IDCT table */
    for (i = 2 * (MAXJSAMPLE + 1); i < 4 * (MAXJSAMPLE + 1); i++) {
        table[i] = MAXJSAMPLE;
    }
    
    /* Reset table pointer for normal usage */
    table = ctx->range_limit_base + (MAXJSAMPLE + 1);
    ctx->range_limit_table = table;
    
    return 0;
}

/*
 * Cleanup IDCT context
 */
void idct_cleanup(idct_context *ctx) {
    if (ctx) {
        /* Nothing to free currently - all data is embedded */
        memset(ctx, 0, sizeof(idct_context));
    }
}

/*
 * Create standard JPEG quantization table
 */
void create_standard_quant_table(unsigned int *table, int quality_factor) {
    int i;
    int quality = (quality_factor < 1) ? 1 : ((quality_factor > 100) ? 100 : quality_factor);
    int scale = (quality < 50) ? (5000 / quality) : (200 - quality * 2);
    
    for (i = 0; i < DCTSIZE2; i++) {
        long val = ((long)std_jpeg_quant_table[i] * scale + 50L) / 100L;
        if (val < 1) val = 1;
        if (val > 65535) val = 65535;
        table[i] = (unsigned int)val;
    }
}

/*
 * Range limit a value to [0, MAXJSAMPLE]
 */
static inline JSAMPLE get_range_limit(int val, JSAMPLE *range_table) {
    /* Use the pre-computed range limit table for fast clamping */
    return range_table[val & RANGE_MASK];
}

/*
 * Core 8x8 inverse DCT implementation (refactored from jidctint.c)
 * This is the main workhorse function implementing the Loeffler algorithm
 */
int idct_8x8(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    JLONG tmp0, tmp1, tmp2, tmp3;
    JLONG tmp10, tmp11, tmp12, tmp13;
    JLONG z1, z2, z3, z4, z5;
    const JCOEF *inptr;
    ISLOW_MULT_TYPE *quantptr;
    int *wsptr;
    JSAMPLE *outptr;
    JSAMPLE *range_limit;
    int ctr;
    int workspace[DCTSIZE2];  /* buffers data between passes */
    
    if (!ctx || !coef_block || !output_buf) return -1;
    
    range_limit = ctx->range_limit_table;
    
    /* Pass 1: process columns from input, store into work array. */
    /* Note results are scaled up by sqrt(8) compared to a true IDCT; */
    /* furthermore, we scale the results by 2**PASS1_BITS. */
    
    inptr = coef_block;
    quantptr = ctx->quant_table;
    wsptr = workspace;
    
    for (ctr = DCTSIZE; ctr > 0; ctr--) {
        /* Due to quantization, we will usually find that many of the input
         * coefficients are zero, especially the AC terms.  We can exploit this
         * by short-circuiting the IDCT calculation for any column in which all
         * the AC terms are zero.  In that case each output is equal to the
         * DC coefficient (with scale factor as needed).
         * With typical images and quantization tables, half or more of the
         * column DCT calculations can be simplified this way.
         */
        
        if (inptr[DCTSIZE * 1] == 0 && inptr[DCTSIZE * 2] == 0 &&
            inptr[DCTSIZE * 3] == 0 && inptr[DCTSIZE * 4] == 0 &&
            inptr[DCTSIZE * 5] == 0 && inptr[DCTSIZE * 6] == 0 &&
            inptr[DCTSIZE * 7] == 0) {
            /* AC terms all zero */
            int dcval = LEFT_SHIFT(DEQUANTIZE(inptr[DCTSIZE * 0],
                                   quantptr[DCTSIZE * 0]), PASS1_BITS);
            
            wsptr[DCTSIZE * 0] = dcval;
            wsptr[DCTSIZE * 1] = dcval;
            wsptr[DCTSIZE * 2] = dcval;
            wsptr[DCTSIZE * 3] = dcval;
            wsptr[DCTSIZE * 4] = dcval;
            wsptr[DCTSIZE * 5] = dcval;
            wsptr[DCTSIZE * 6] = dcval;
            wsptr[DCTSIZE * 7] = dcval;
            
            inptr++;            /* advance pointers to next column */
            quantptr++;
            wsptr++;
            continue;
        }
        
        /* Even part: reverse the even part of the forward DCT. */
        /* The rotator is sqrt(2)*c(-6). */
        
        z2 = DEQUANTIZE(inptr[DCTSIZE * 2], quantptr[DCTSIZE * 2]);
        z3 = DEQUANTIZE(inptr[DCTSIZE * 6], quantptr[DCTSIZE * 6]);
        
        z1 = MULTIPLY(z2 + z3, FIX_0_541196100);
        tmp2 = z1 + MULTIPLY(z3, -FIX_1_847759065);
        tmp3 = z1 + MULTIPLY(z2, FIX_0_765366865);
        
        z2 = DEQUANTIZE(inptr[DCTSIZE * 0], quantptr[DCTSIZE * 0]);
        z3 = DEQUANTIZE(inptr[DCTSIZE * 4], quantptr[DCTSIZE * 4]);
        
        tmp0 = LEFT_SHIFT(z2 + z3, CONST_BITS);
        tmp1 = LEFT_SHIFT(z2 - z3, CONST_BITS);
        
        tmp10 = tmp0 + tmp3;
        tmp13 = tmp0 - tmp3;
        tmp11 = tmp1 + tmp2;
        tmp12 = tmp1 - tmp2;
        
        /* Odd part per figure 8; the matrix is unitary and hence its
         * transpose is its inverse.  i0..i3 are y7,y5,y3,y1 respectively.
         */
        
        tmp0 = DEQUANTIZE(inptr[DCTSIZE * 7], quantptr[DCTSIZE * 7]);
        tmp1 = DEQUANTIZE(inptr[DCTSIZE * 5], quantptr[DCTSIZE * 5]);
        tmp2 = DEQUANTIZE(inptr[DCTSIZE * 3], quantptr[DCTSIZE * 3]);
        tmp3 = DEQUANTIZE(inptr[DCTSIZE * 1], quantptr[DCTSIZE * 1]);
        
        z1 = tmp0 + tmp3;
        z2 = tmp1 + tmp2;
        z3 = tmp0 + tmp2;
        z4 = tmp1 + tmp3;
        z5 = MULTIPLY(z3 + z4, FIX_1_175875602); /* sqrt(2) * c3 */
        
        tmp0 = MULTIPLY(tmp0, FIX_0_298631336); /* sqrt(2) * (-c1+c3+c5-c7) */
        tmp1 = MULTIPLY(tmp1, FIX_2_053119869); /* sqrt(2) * ( c1+c3-c5+c7) */
        tmp2 = MULTIPLY(tmp2, FIX_3_072711026); /* sqrt(2) * ( c1+c3+c5-c7) */
        tmp3 = MULTIPLY(tmp3, FIX_1_501321110); /* sqrt(2) * ( c1+c3-c5-c7) */
        z1 = MULTIPLY(z1, -FIX_0_899976223); /* sqrt(2) * (c7-c3) */
        z2 = MULTIPLY(z2, -FIX_2_562915447); /* sqrt(2) * (-c1-c3) */
        z3 = MULTIPLY(z3, -FIX_1_961570560); /* sqrt(2) * (-c3-c5) */
        z4 = MULTIPLY(z4, -FIX_0_390180644); /* sqrt(2) * (c5-c3) */
        
        z3 += z5;
        z4 += z5;
        
        tmp0 += z1 + z3;
        tmp1 += z2 + z4;
        tmp2 += z2 + z3;
        tmp3 += z1 + z4;
        
        /* Final output stage: inputs are tmp10..tmp13, tmp0..tmp3 */
        
        wsptr[DCTSIZE * 0] = (int)DESCALE(tmp10 + tmp3, CONST_BITS - PASS1_BITS);
        wsptr[DCTSIZE * 7] = (int)DESCALE(tmp10 - tmp3, CONST_BITS - PASS1_BITS);
        wsptr[DCTSIZE * 1] = (int)DESCALE(tmp11 + tmp2, CONST_BITS - PASS1_BITS);
        wsptr[DCTSIZE * 6] = (int)DESCALE(tmp11 - tmp2, CONST_BITS - PASS1_BITS);
        wsptr[DCTSIZE * 2] = (int)DESCALE(tmp12 + tmp1, CONST_BITS - PASS1_BITS);
        wsptr[DCTSIZE * 5] = (int)DESCALE(tmp12 - tmp1, CONST_BITS - PASS1_BITS);
        wsptr[DCTSIZE * 3] = (int)DESCALE(tmp13 + tmp0, CONST_BITS - PASS1_BITS);
        wsptr[DCTSIZE * 4] = (int)DESCALE(tmp13 - tmp0, CONST_BITS - PASS1_BITS);
        
        inptr++;            /* advance pointers to next column */
        quantptr++;
        wsptr++;
    }
    
    /* Pass 2: process rows from work array, store into output array. */
    /* Note that we must descale the results by a factor of 8 == 2**3, */
    /* and also undo the PASS1_BITS scaling. */
    
    wsptr = workspace;
    for (ctr = 0; ctr < DCTSIZE; ctr++) {
        outptr = output_buf + ctr * output_stride;
        
        /* Shortcut for DC-only rows (optimization from original jidctint.c) */
        if (wsptr[1] == 0 && wsptr[2] == 0 && wsptr[3] == 0 && wsptr[4] == 0 &&
            wsptr[5] == 0 && wsptr[6] == 0 && wsptr[7] == 0) {
            /* AC terms all zero - use simpler scaling */
            JSAMPLE dcval = get_range_limit((int)DESCALE((JLONG)wsptr[0], 
                                                              PASS1_BITS + 3) 
                                             + CENTERJSAMPLE, range_limit);
            
            outptr[0] = dcval;
            outptr[1] = dcval;
            outptr[2] = dcval;
            outptr[3] = dcval;
            outptr[4] = dcval;
            outptr[5] = dcval;
            outptr[6] = dcval;
            outptr[7] = dcval;
            
            wsptr += DCTSIZE;       /* advance pointer to next row */
            continue;
        }
        
        /* Even part: reverse the even part of the forward DCT. */
        /* The rotator is sqrt(2)*c(-6). */
        
        z2 = (int32_t)wsptr[2];
        z3 = (int32_t)wsptr[6];
        
        z1 = MULTIPLY(z2 + z3, FIX_0_541196100);
        tmp2 = z1 + MULTIPLY(z3, -FIX_1_847759065);
        tmp3 = z1 + MULTIPLY(z2, FIX_0_765366865);
        
        tmp0 = LEFT_SHIFT((int32_t)wsptr[0] + (int32_t)wsptr[4], CONST_BITS);
        tmp1 = LEFT_SHIFT((int32_t)wsptr[0] - (int32_t)wsptr[4], CONST_BITS);
        
        tmp10 = tmp0 + tmp3;
        tmp13 = tmp0 - tmp3;
        tmp11 = tmp1 + tmp2;
        tmp12 = tmp1 - tmp2;
        
        /* Odd part per figure 8; the matrix is unitary and hence its
         * transpose is its inverse.  i0..i3 are y7,y5,y3,y1 respectively.
         */
        
        tmp0 = (int32_t)wsptr[7];
        tmp1 = (int32_t)wsptr[5];
        tmp2 = (int32_t)wsptr[3];
        tmp3 = (int32_t)wsptr[1];
        
        z1 = tmp0 + tmp3;
        z2 = tmp1 + tmp2;
        z3 = tmp0 + tmp2;
        z4 = tmp1 + tmp3;
        z5 = MULTIPLY(z3 + z4, FIX_1_175875602); /* sqrt(2) * c3 */
        
        tmp0 = MULTIPLY(tmp0, FIX_0_298631336); /* sqrt(2) * (-c1+c3+c5-c7) */
        tmp1 = MULTIPLY(tmp1, FIX_2_053119869); /* sqrt(2) * ( c1+c3-c5+c7) */
        tmp2 = MULTIPLY(tmp2, FIX_3_072711026); /* sqrt(2) * ( c1+c3+c5-c7) */
        tmp3 = MULTIPLY(tmp3, FIX_1_501321110); /* sqrt(2) * ( c1+c3-c5-c7) */
        z1 = MULTIPLY(z1, -FIX_0_899976223); /* sqrt(2) * (c7-c3) */
        z2 = MULTIPLY(z2, -FIX_2_562915447); /* sqrt(2) * (-c1-c3) */
        z3 = MULTIPLY(z3, -FIX_1_961570560); /* sqrt(2) * (-c3-c5) */
        z4 = MULTIPLY(z4, -FIX_0_390180644); /* sqrt(2) * (c5-c3) */
        
        z3 += z5;
        z4 += z5;
        
        tmp0 += z1 + z3;
        tmp1 += z2 + z4;
        tmp2 += z2 + z3;
        tmp3 += z1 + z4;
        
        /* Final output stage: inputs are tmp10..tmp13, tmp0..tmp3 */
        
        outptr[0] = get_range_limit((int)DESCALE(tmp10 + tmp3, CONST_BITS + PASS1_BITS + 3)
                                  + CENTERJSAMPLE, range_limit);
        outptr[7] = get_range_limit((int)DESCALE(tmp10 - tmp3, CONST_BITS + PASS1_BITS + 3)
                                  + CENTERJSAMPLE, range_limit);
        outptr[1] = get_range_limit((int)DESCALE(tmp11 + tmp2, CONST_BITS + PASS1_BITS + 3)
                                  + CENTERJSAMPLE, range_limit);
        outptr[6] = get_range_limit((int)DESCALE(tmp11 - tmp2, CONST_BITS + PASS1_BITS + 3)
                                  + CENTERJSAMPLE, range_limit);
        outptr[2] = get_range_limit((int)DESCALE(tmp12 + tmp1, CONST_BITS + PASS1_BITS + 3)
                                  + CENTERJSAMPLE, range_limit);
        outptr[5] = get_range_limit((int)DESCALE(tmp12 - tmp1, CONST_BITS + PASS1_BITS + 3)
                                  + CENTERJSAMPLE, range_limit);
        outptr[3] = get_range_limit((int)DESCALE(tmp13 + tmp0, CONST_BITS + PASS1_BITS + 3)
                                  + CENTERJSAMPLE, range_limit);
        outptr[4] = get_range_limit((int)DESCALE(tmp13 - tmp0, CONST_BITS + PASS1_BITS + 3)
                                  + CENTERJSAMPLE, range_limit);
        
        wsptr += DCTSIZE;       /* advance pointer to next row */
    }
    
    return 0;
}

/*
 * 1x1 inverse DCT (trivial case - just scale the DC coefficient)
 */
int idct_1x1(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    int dcval;
    JSAMPLE *range_limit;
    
    (void)output_stride;  /* Unused for 1x1 */
    
    if (!ctx || !coef_block || !output_buf) return -1;
    
    range_limit = ctx->range_limit_table;
    
    /* Scale the DC coefficient - match original scaling */
    dcval = DEQUANTIZE(coef_block[0], ctx->quant_table[0]);
    dcval = LEFT_SHIFT(dcval, PASS1_BITS);  /* Scale up like original */
    dcval = (int)DESCALE(dcval, CONST_BITS + PASS1_BITS + 3);  /* Scale down properly */
    
    output_buf[0] = get_range_limit(dcval + CENTERJSAMPLE, range_limit);
    
    return 0;
}

/*
 * 2x2 inverse DCT
 */
int idct_2x2(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    JLONG tmp0, tmp1, tmp2, tmp3;
    JSAMPLE *range_limit;
    JSAMPLE *outptr;
    
    if (!ctx || !coef_block || !output_buf) return -1;
    
    range_limit = ctx->range_limit_table;
    
    /* 2x2 IDCT implementation with proper scaling */
    tmp0 = DEQUANTIZE(coef_block[0], ctx->quant_table[0]);
    tmp1 = DEQUANTIZE(coef_block[1], ctx->quant_table[1]);
    tmp2 = DEQUANTIZE(coef_block[DCTSIZE], ctx->quant_table[DCTSIZE]);
    tmp3 = DEQUANTIZE(coef_block[DCTSIZE + 1], ctx->quant_table[DCTSIZE + 1]);
    
    /* Scale coefficients properly */
    tmp0 = LEFT_SHIFT(tmp0, CONST_BITS - PASS1_BITS + 1);
    tmp1 = LEFT_SHIFT(tmp1, CONST_BITS - PASS1_BITS + 1);
    tmp2 = LEFT_SHIFT(tmp2, CONST_BITS - PASS1_BITS + 1);  
    tmp3 = LEFT_SHIFT(tmp3, CONST_BITS - PASS1_BITS + 1);
    
    /* Simple 2x2 IDCT - mathematical definition */
    /* f[0,0] = (1/2) * (c[0,0] + c[0,1] + c[1,0] + c[1,1]) */
    /* f[0,1] = (1/2) * (c[0,0] - c[0,1] + c[1,0] - c[1,1]) */
    /* f[1,0] = (1/2) * (c[0,0] + c[0,1] - c[1,0] - c[1,1]) */
    /* f[1,1] = (1/2) * (c[0,0] - c[0,1] - c[1,0] + c[1,1]) */
    
    JLONG f00 = (tmp0 + tmp1 + tmp2 + tmp3) >> 1;
    JLONG f01 = (tmp0 - tmp1 + tmp2 - tmp3) >> 1;
    JLONG f10 = (tmp0 + tmp1 - tmp2 - tmp3) >> 1;
    JLONG f11 = (tmp0 - tmp1 - tmp2 + tmp3) >> 1;
    
    /* Output with proper descaling */
    outptr = output_buf;
    outptr[0] = get_range_limit((int)DESCALE(f00, CONST_BITS + 1) + CENTERJSAMPLE, range_limit);
    outptr[1] = get_range_limit((int)DESCALE(f01, CONST_BITS + 1) + CENTERJSAMPLE, range_limit);
    
    outptr = output_buf + output_stride;
    outptr[0] = get_range_limit((int)DESCALE(f10, CONST_BITS + 1) + CENTERJSAMPLE, range_limit);
    outptr[1] = get_range_limit((int)DESCALE(f11, CONST_BITS + 1) + CENTERJSAMPLE, range_limit);
    
    return 0;
}

/*
 * 4x4 inverse DCT (adapted from the original jidctred.c)
 */
int idct_4x4(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    JLONG tmp0, tmp1, tmp2, tmp10, tmp11, tmp12;
    JLONG z1, z3;
    const JCOEF *inptr;
    ISLOW_MULT_TYPE *quantptr;
    int *wsptr;
    JSAMPLE *outptr;
    JSAMPLE *range_limit;
    int ctr;
    int workspace[4*4]; /* 4x4 workspace */
    
    if (!ctx || !coef_block || !output_buf) return -1;
    
    range_limit = ctx->range_limit_table;
    
    /* Pass 1: process columns from input, store into work array. */
    
    inptr = coef_block;
    quantptr = ctx->quant_table;
    wsptr = workspace;
    
    for (ctr = 0; ctr < 4; ctr++) {
        /* Even part */
        
        tmp0 = DEQUANTIZE(inptr[DCTSIZE*0], quantptr[DCTSIZE*0]);
        tmp1 = DEQUANTIZE(inptr[DCTSIZE*2], quantptr[DCTSIZE*2]);
        
        tmp10 = LEFT_SHIFT(tmp0 + tmp1, CONST_BITS+1);
        tmp11 = LEFT_SHIFT(tmp0 - tmp1, CONST_BITS+1);
        
        /* Odd part */
        
        tmp0 = DEQUANTIZE(inptr[DCTSIZE*1], quantptr[DCTSIZE*1]);
        tmp1 = DEQUANTIZE(inptr[DCTSIZE*3], quantptr[DCTSIZE*3]);
        
        z1 = MULTIPLY(tmp0 + tmp1, FIX_0_541196100);  /* c6 */
        /* Add fudge factor here for final descale. */
        z1 += ONE << (CONST_BITS - PASS1_BITS - 1);
        tmp0 = RIGHT_SHIFT(z1 + MULTIPLY(tmp0, FIX_0_765366865), /* c2-c6 */
                           CONST_BITS - PASS1_BITS);
        tmp1 = RIGHT_SHIFT(z1 - MULTIPLY(tmp1, FIX_1_847759065), /* c2+c6 */
                           CONST_BITS - PASS1_BITS);
        
        /* Final output stage */
        
        wsptr[4*0] = (int)RIGHT_SHIFT(tmp10 + LEFT_SHIFT(tmp0, PASS1_BITS), 
                                      CONST_BITS + 1 - PASS1_BITS);
        wsptr[4*3] = (int)RIGHT_SHIFT(tmp10 - LEFT_SHIFT(tmp0, PASS1_BITS),
                                      CONST_BITS + 1 - PASS1_BITS);
        wsptr[4*1] = (int)RIGHT_SHIFT(tmp11 + LEFT_SHIFT(tmp1, PASS1_BITS),
                                      CONST_BITS + 1 - PASS1_BITS);
        wsptr[4*2] = (int)RIGHT_SHIFT(tmp11 - LEFT_SHIFT(tmp1, PASS1_BITS),
                                      CONST_BITS + 1 - PASS1_BITS);
        
        inptr++;
        quantptr++;
        wsptr++;
    }
    
    /* Pass 2: process rows from work array, store into output array. */
    
    wsptr = workspace;
    for (ctr = 0; ctr < 4; ctr++) {
        outptr = output_buf + ctr * output_stride;
        
        /* Even part */
        
        /* Add range center and fudge factor for final descale and range-limit. */
        z3 = LEFT_SHIFT((int32_t)wsptr[0] + (int32_t)wsptr[2], CONST_BITS + 1);
        z3 += ONE << (CONST_BITS + PASS1_BITS + 3);
        tmp0 = z3 + LEFT_SHIFT((int32_t)wsptr[0], CONST_BITS + 1);
        tmp1 = z3 - LEFT_SHIFT((int32_t)wsptr[2], CONST_BITS + 1);
        
        /* Odd part */
        
        z1 = MULTIPLY((int32_t)wsptr[1] + (int32_t)wsptr[3], FIX_0_541196100); /* c6 */
        tmp2 = z1 + MULTIPLY((int32_t)wsptr[1], FIX_0_765366865); /* c2-c6 */
        tmp12 = z1 - MULTIPLY((int32_t)wsptr[3], FIX_1_847759065); /* c2+c6 */
        
        /* Final output stage */
        
        outptr[0] = get_range_limit((int)RIGHT_SHIFT(tmp0 + tmp2,
                                                   CONST_BITS + PASS1_BITS + 3 + 1),
                                  range_limit);
        outptr[3] = get_range_limit((int)RIGHT_SHIFT(tmp0 - tmp2,
                                                   CONST_BITS + PASS1_BITS + 3 + 1),
                                  range_limit);
        outptr[1] = get_range_limit((int)RIGHT_SHIFT(tmp1 + tmp12,
                                                   CONST_BITS + PASS1_BITS + 3 + 1),
                                  range_limit);
        outptr[2] = get_range_limit((int)RIGHT_SHIFT(tmp1 - tmp12,
                                                   CONST_BITS + PASS1_BITS + 3 + 1),
                                  range_limit);
        
        wsptr += 4;
    }
    
    return 0;
}

/* Stub implementations for other block sizes - many would require complex algorithms */

int idct_3x3(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    JLONG tmp0, tmp2, tmp10, tmp12;
    const JCOEF *inptr;
    ISLOW_MULT_TYPE *quantptr;
    int *wsptr;
    JSAMPLE *outptr;
    JSAMPLE *range_limit;
    int ctr;
    int workspace[3 * 3];  /* buffers data between passes */

    if (!ctx || !coef_block || !output_buf) return -1;
    
    range_limit = ctx->range_limit_table;

    /* Pass 1: process columns from input, store into work array. */

    inptr = coef_block;
    quantptr = ctx->quant_table;
    wsptr = workspace;
    
    for (ctr = 0; ctr < 3; ctr++, inptr++, quantptr++, wsptr++) {
        /* Even part */

        tmp0 = DEQUANTIZE(inptr[DCTSIZE * 0], quantptr[DCTSIZE * 0]);
        tmp0 = LEFT_SHIFT(tmp0, CONST_BITS);
        /* Add fudge factor here for final descale. */
        tmp0 += ONE << (CONST_BITS - PASS1_BITS - 1);
        tmp2 = DEQUANTIZE(inptr[DCTSIZE * 2], quantptr[DCTSIZE * 2]);
        tmp12 = MULTIPLY(tmp2, FIX_0_707106781); /* c2 */
        tmp10 = tmp0 + tmp12;
        tmp2 = tmp0 - tmp12 - tmp12;

        /* Odd part */

        tmp12 = DEQUANTIZE(inptr[DCTSIZE * 1], quantptr[DCTSIZE * 1]);
        tmp0 = MULTIPLY(tmp12, FIX_1_224744871); /* c1 */

        /* Final output stage */

        wsptr[3 * 0] = (int)RIGHT_SHIFT(tmp10 + tmp0, CONST_BITS - PASS1_BITS);
        wsptr[3 * 2] = (int)RIGHT_SHIFT(tmp10 - tmp0, CONST_BITS - PASS1_BITS);
        wsptr[3 * 1] = (int)RIGHT_SHIFT(tmp2, CONST_BITS - PASS1_BITS);
    }

    /* Pass 2: process 3 rows from work array, store into output array. */

    wsptr = workspace;
    for (ctr = 0; ctr < 3; ctr++) {
        outptr = output_buf + ctr * output_stride;

        /* Even part */

        /* Add fudge factor here for final descale. */
        tmp0 = (int32_t)wsptr[0] + (ONE << (PASS1_BITS + 2));
        tmp0 = LEFT_SHIFT(tmp0, CONST_BITS);
        tmp2 = (int32_t)wsptr[2];
        tmp12 = MULTIPLY(tmp2, FIX_0_707106781); /* c2 */
        tmp10 = tmp0 + tmp12;
        tmp2 = tmp0 - tmp12 - tmp12;

        /* Odd part */

        tmp12 = (int32_t)wsptr[1];
        tmp0 = MULTIPLY(tmp12, FIX_1_224744871); /* c1 */

        /* Final output stage */

        outptr[0] = get_range_limit((int)RIGHT_SHIFT(tmp10 + tmp0,
                                                 CONST_BITS + PASS1_BITS + 3) &
                                RANGE_MASK, range_limit);
        outptr[2] = get_range_limit((int)RIGHT_SHIFT(tmp10 - tmp0,
                                                 CONST_BITS + PASS1_BITS + 3) &
                                RANGE_MASK, range_limit);
        outptr[1] = get_range_limit((int)RIGHT_SHIFT(tmp2,
                                                 CONST_BITS + PASS1_BITS + 3) &
                                RANGE_MASK, range_limit);

        wsptr += 3;         /* advance pointer to next row */
    }
    
    return 0;
}

int idct_5x5(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented - would need specialized 5x5 algorithm */
}

int idct_6x6(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented - would need specialized 6x6 algorithm */
}

int idct_7x7(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented - would need specialized 7x7 algorithm */
}

/* Enhanced IDCT functions for upscaling (9x9 to 16x16) - stubs for now */

int idct_9x9(idct_context *ctx, const JCOEF *coef_block, 
                JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented */
}

int idct_10x10(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented */
}

int idct_11x11(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented */
}

int idct_12x12(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented */
}

int idct_13x13(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented */
}

int idct_14x14(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented */
}

int idct_15x15(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented */
}

int idct_16x16(idct_context *ctx, const JCOEF *coef_block, 
                  JSAMPLE *output_buf, int output_stride) {
    (void)ctx; (void)coef_block; (void)output_buf; (void)output_stride;
    return -1; /* Not implemented */
}

/*
 * Generic IDCT function that dispatches to appropriate size-specific function
 */
int idct_generic(idct_context *ctx, const JCOEF *coef_block,
                    JSAMPLE *output_buf, int output_stride) {
    if (!ctx) return -1;
    
    switch (ctx->block_size) {
        case IDCT_1x1:
            return idct_1x1(ctx, coef_block, output_buf, output_stride);
        case IDCT_2x2:
            return idct_2x2(ctx, coef_block, output_buf, output_stride);
        case IDCT_3x3:
            return idct_3x3(ctx, coef_block, output_buf, output_stride);
        case IDCT_4x4:
            return idct_4x4(ctx, coef_block, output_buf, output_stride);
        case IDCT_5x5:
            return idct_5x5(ctx, coef_block, output_buf, output_stride);
        case IDCT_6x6:
            return idct_6x6(ctx, coef_block, output_buf, output_stride);
        case IDCT_7x7:
            return idct_7x7(ctx, coef_block, output_buf, output_stride);
        case IDCT_8x8:
            return idct_8x8(ctx, coef_block, output_buf, output_stride);
        case IDCT_9x9:
            return idct_9x9(ctx, coef_block, output_buf, output_stride);
        case IDCT_10x10:
            return idct_10x10(ctx, coef_block, output_buf, output_stride);
        case IDCT_11x11:
            return idct_11x11(ctx, coef_block, output_buf, output_stride);
        case IDCT_12x12:
            return idct_12x12(ctx, coef_block, output_buf, output_stride);
        case IDCT_13x13:
            return idct_13x13(ctx, coef_block, output_buf, output_stride);
        case IDCT_14x14:
            return idct_14x14(ctx, coef_block, output_buf, output_stride);
        case IDCT_15x15:
            return idct_15x15(ctx, coef_block, output_buf, output_stride);
        case IDCT_16x16:
            return idct_16x16(ctx, coef_block, output_buf, output_stride);
        default:
            return -1; /* Unsupported block size */
    }
}
