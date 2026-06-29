#include <stdint.h>
#include <stddef.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Basic type definitions */
typedef unsigned char JOCTET;
typedef int16_t JCOEF;
typedef JCOEF *JCOEFPTR;

/* Constants */
#define DCTSIZE2 64  /* DCT block size squared */
#define MAX_COMPS_IN_SCAN 4

/* Bit buffer types and sizes */
#if (defined(SIZEOF_SIZE_T) && SIZEOF_SIZE_T == 8) || defined(_WIN64) || \
    (defined(__x86_64__) && defined(__ILP32__))
#define BIT_BUF_SIZE  64
typedef uint64_t bit_buf_type;
#elif (defined(SIZEOF_SIZE_T) && SIZEOF_SIZE_T == 4) || defined(_WIN32)
#define BIT_BUF_SIZE  32
typedef uint32_t bit_buf_type;
#else
#define BIT_BUF_SIZE  32  /* Default fallback */
typedef uint32_t bit_buf_type;
#endif

/* Derived Huffman table structure */
typedef struct {
    unsigned int ehufco[256];   /* code for each symbol */
    char ehufsi[256];           /* length of code for each symbol */
} c_derived_tbl;

/* Savable state for bit buffer and DC values */
typedef struct {
    bit_buf_type put_buffer;              /* current bit accumulation buffer */
    int free_bits;                        /* # of bits available in it */
    int last_dc_val[MAX_COMPS_IN_SCAN];   /* last DC coef for each component */
} savable_state;

/* Working state structure for encoding */
typedef struct {
    JOCTET *next_output_byte;     /* => next byte to write in buffer */
    size_t free_in_buffer;        /* # of byte spaces remaining in buffer */
    savable_state cur;            /* Current bit buffer & DC state */
    void *cinfo;                  /* For error handling - can be NULL for standalone */
    int data_precision;           /* JPEG data precision (usually 8) */
} working_state;

/* Error codes */
#define JERR_BAD_DCT_COEF    1
#define JERR_CANT_SUSPEND    2

/* Error handling callback type */
typedef void (*error_exit_func_t)(void *cinfo, int error_code);

/* Bit counting functions */
#define JPEG_NBITS_NONZERO(x)  (32 - __builtin_clz(x))
#define JPEG_NBITS(x)          ((x) ? JPEG_NBITS_NONZERO(x) : 0)

/* Main encoding function */
int encode_one_block(working_state *state, JCOEFPTR block, 
                                int last_dc_val, c_derived_tbl *dctbl, 
                                c_derived_tbl *actbl, 
                                error_exit_func_t error_exit);

/* JPEG natural order array - zigzag to natural order conversion
 * When reading corrupted data, we put extra "63"s after the real entries
 * to prevent wild stores without adding an inner-loop test.
 */
const int jpeg_natural_order[DCTSIZE2 + 16] = {
    0,  1,  8, 16,  9,  2,  3, 10,
   17, 24, 32, 25, 18, 11,  4,  5,
   12, 19, 26, 33, 40, 48, 41, 34,
   27, 20, 13,  6,  7, 14, 21, 28,
   35, 42, 49, 56, 57, 50, 43, 36,
   29, 22, 15, 23, 30, 37, 44, 51,
   58, 59, 52, 45, 38, 31, 39, 46,
   53, 60, 61, 54, 47, 55, 62, 63,
   63, 63, 63, 63, 63, 63, 63, 63, /* extra entries for safety in decoder */
   63, 63, 63, 63, 63, 63, 63, 63
};
/* Buffer size for worst case coefficient encoding */
#define BUFSIZE  (DCTSIZE2 * 8)

/* MIN macro */
#ifndef MIN
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#endif

/*
 * Buffer management macros (adapted from original jchuff.c)
 */
#define LOAD_BUFFER() { \
    if (state->free_in_buffer < BUFSIZE) { \
        localbuf = 1; \
        buffer = _buffer; \
    } else \
        buffer = state->next_output_byte; \
}

#define STORE_BUFFER() { \
    if (localbuf) { \
        size_t bytes, bytestocopy; \
        bytes = buffer - _buffer; \
        buffer = _buffer; \
        while (bytes > 0) { \
            bytestocopy = MIN(bytes, state->free_in_buffer); \
            memcpy(state->next_output_byte, buffer, bytestocopy); \
            state->next_output_byte += bytestocopy; \
            buffer += bytestocopy; \
            state->free_in_buffer -= bytestocopy; \
            bytes -= bytestocopy; \
            if (state->free_in_buffer == 0) { \
                /* In standalone version, we assume buffer is large enough */ \
                /* In real implementation, this would call dump_buffer() */ \
                break; \
            } \
        } \
    } else { \
        /* When not using local buffer, update free_in_buffer based on buffer movement */ \
        size_t bytes_written = buffer - state->next_output_byte; \
        state->next_output_byte = buffer; \
        state->free_in_buffer -= bytes_written; \
    } \
}

/* JLONG type matching original libjpeg-turbo */
typedef long JLONG;

/*
 * Helper macros for bit manipulation (exactly matching original jchuff.c)
 */

/* Emit a byte with 0xFF stuffing */
#define EMIT_BYTE(b) { \
    buffer[0] = (JOCTET)(b); \
    buffer[1] = 0; \
    buffer += 2; \
    if ((b) == 0xFF) buffer[-1] = 0; else buffer--; \
}

/* Flush bit buffer to output when it gets full */
#if BIT_BUF_SIZE == 64
#define FLUSH() { \
    if (put_buffer & 0x8080808080808080ULL & ~(put_buffer + 0x0101010101010101ULL)) { \
        EMIT_BYTE(put_buffer >> 56) \
        EMIT_BYTE(put_buffer >> 48) \
        EMIT_BYTE(put_buffer >> 40) \
        EMIT_BYTE(put_buffer >> 32) \
        EMIT_BYTE(put_buffer >> 24) \
        EMIT_BYTE(put_buffer >> 16) \
        EMIT_BYTE(put_buffer >>  8) \
        EMIT_BYTE(put_buffer      ) \
    } else { \
        buffer[0] = (JOCTET)(put_buffer >> 56); \
        buffer[1] = (JOCTET)(put_buffer >> 48); \
        buffer[2] = (JOCTET)(put_buffer >> 40); \
        buffer[3] = (JOCTET)(put_buffer >> 32); \
        buffer[4] = (JOCTET)(put_buffer >> 24); \
        buffer[5] = (JOCTET)(put_buffer >> 16); \
        buffer[6] = (JOCTET)(put_buffer >>  8); \
        buffer[7] = (JOCTET)(put_buffer      ); \
        buffer += 8; \
    } \
}
#else
#define FLUSH() { \
    if (put_buffer & 0x80808080U & ~(put_buffer + 0x01010101U)) { \
        EMIT_BYTE(put_buffer >> 24) \
        EMIT_BYTE(put_buffer >> 16) \
        EMIT_BYTE(put_buffer >>  8) \
        EMIT_BYTE(put_buffer      ) \
    } else { \
        buffer[0] = (JOCTET)(put_buffer >> 24); \
        buffer[1] = (JOCTET)(put_buffer >> 16); \
        buffer[2] = (JOCTET)(put_buffer >>  8); \
        buffer[3] = (JOCTET)(put_buffer      ); \
        buffer += 4; \
    } \
}
#endif

/* Fill the bit buffer to capacity with the leading bits from code, then output
 * the bit buffer and put the remaining bits from code into the bit buffer.
 */
#define PUT_AND_FLUSH(code, size) { \
    put_buffer = (put_buffer << (size + free_bits)) | (code >> -free_bits); \
    FLUSH() \
    free_bits += BIT_BUF_SIZE; \
    put_buffer = code; \
}

/* Insert code into the bit buffer and output the bit buffer if needed.
 * NOTE: We can't flush with free_bits == 0, since the left shift in
 * PUT_AND_FLUSH() would have undefined behavior.
 */
#define PUT_BITS(code, size) { \
    free_bits -= size; \
    if (free_bits < 0) \
        PUT_AND_FLUSH(code, size) \
    else \
        put_buffer = (put_buffer << size) | code; \
}

/* Exactly matching original PUT_CODE macro */
#define PUT_CODE(code, size) { \
    temp &= (((JLONG)1) << nbits) - 1; \
    temp |= code << nbits; \
    nbits += size; \
    PUT_BITS(temp, nbits) \
}

/*
 * Main encoding function - encodes a single DCT block with Huffman encoding.
 * This is a self-contained version of the encode_one_block function from jchuff.c.
 */
int encode_one_block(working_state *state, JCOEFPTR block, 
                                int last_dc_val, c_derived_tbl *dctbl, 
                                c_derived_tbl *actbl, 
                                error_exit_func_t error_exit) {
    int temp, nbits, free_bits;
    bit_buf_type put_buffer;
    JOCTET _buffer[BUFSIZE], *buffer;
    int localbuf = 0;
    int max_coef_bits = state->data_precision + 2;
    
    /* Load current bit buffer state */
    free_bits = state->cur.free_bits;
    put_buffer = state->cur.put_buffer;
    LOAD_BUFFER()
    
    /* Encode the DC coefficient difference per JPEG section F.1.2.1 */
    temp = block[0] - last_dc_val;
    
    /* Branch-free absolute value calculation (from original jchuff.c) */
    nbits = temp >> (CHAR_BIT * sizeof(int) - 1);
    temp += nbits;
    nbits ^= temp;
    
    /* Find the number of bits needed for the magnitude */
    nbits = JPEG_NBITS(nbits);
    
    /* Check for out-of-range coefficient values */
    if (nbits > max_coef_bits + 1) {
        if (error_exit) {
            error_exit(state->cinfo, JERR_BAD_DCT_COEF);
        }
        return 0;
    }
    
    /* Emit the Huffman-coded symbol for the number of bits */
    PUT_CODE(dctbl->ehufco[nbits], dctbl->ehufsi[nbits])
    
    /* Encode the AC coefficients per JPEG section F.1.2.2 */
    {
        int r = 0;  /* run length of zeros */
        
        /* Manual loop unrolling for performance (matches original) */
#define kloop(jpeg_natural_order_of_k) { \
    if ((temp = block[jpeg_natural_order_of_k]) == 0) { \
        r += 16; \
    } else { \
        /* Branch-less absolute value, bitwise complement, etc., same as above */ \
        nbits = temp >> (CHAR_BIT * sizeof(int) - 1); \
        temp += nbits; \
        nbits ^= temp; \
        nbits = JPEG_NBITS_NONZERO(nbits); \
        /* Check for out-of-range coefficient values */ \
        if (nbits > max_coef_bits) { \
            if (error_exit) error_exit(state->cinfo, JERR_BAD_DCT_COEF); \
            return 0; \
        } \
        /* if run length > 15, must emit special run-length-16 codes (0xF0) */ \
        while (r >= 16 * 16) { \
            r -= 16 * 16; \
            PUT_BITS(actbl->ehufco[0xf0], actbl->ehufsi[0xf0]) \
        } \
        /* Emit Huffman symbol for run length / number of bits */ \
        r += nbits; \
        PUT_CODE(actbl->ehufco[r], actbl->ehufsi[r]) \
        r = 0; \
    } \
}

        /* One iteration for each value in jpeg_natural_order[] */
        kloop(1);   kloop(8);   kloop(16);  kloop(9);   kloop(2);   kloop(3);
        kloop(10);  kloop(17);  kloop(24);  kloop(32);  kloop(25);  kloop(18);
        kloop(11);  kloop(4);   kloop(5);   kloop(12);  kloop(19);  kloop(26);
        kloop(33);  kloop(40);  kloop(48);  kloop(41);  kloop(34);  kloop(27);
        kloop(20);  kloop(13);  kloop(6);   kloop(7);   kloop(14);  kloop(21);
        kloop(28);  kloop(35);  kloop(42);  kloop(49);  kloop(56);  kloop(57);
        kloop(50);  kloop(43);  kloop(36);  kloop(29);  kloop(22);  kloop(15);
        kloop(23);  kloop(30);  kloop(37);  kloop(44);  kloop(51);  kloop(58);
        kloop(59);  kloop(52);  kloop(45);  kloop(38);  kloop(31);  kloop(39);
        kloop(46);  kloop(53);  kloop(60);  kloop(61);  kloop(54);  kloop(47);
        kloop(55);  kloop(62);  kloop(63);

        /* If the last coefficient(s) were zero, emit an end-of-block code */
        if (r > 0) {
            PUT_BITS(actbl->ehufco[0], actbl->ehufsi[0])
        }
    }
    
    /* Store the updated bit buffer state */
    state->cur.put_buffer = put_buffer;
    state->cur.free_bits = free_bits;
    STORE_BUFFER()
    
    return 1;  /* Success */
}
