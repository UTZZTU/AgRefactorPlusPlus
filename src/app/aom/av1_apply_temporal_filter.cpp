#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <assert.h>

// ============================================================================
// CONSTANTS (extracted from AOM)
// ============================================================================

// Temporal filter constants
#define TF_WINDOW_LENGTH 5
#define TF_WEIGHT_SCALE 1000
#define TF_WINDOW_BLOCK_BALANCE_WEIGHT 5
#define TF_Q_DECAY_THRESHOLD 20
#define TF_SEARCH_ERROR_NORM_WEIGHT 20
#define TF_STRENGTH_THRESHOLD 4
#define TF_SEARCH_DISTANCE_THRESHOLD 0.1
#define TF_QINDEX_CUTOFF 128

// Plane constants
#define MAX_MB_PLANE 3
#define AOM_PLANE_Y 0
#define AOM_PLANE_U 1  
#define AOM_PLANE_V 2

// Block size constants
#define BLOCK_4X4 0
#define BLOCK_4X8 1
#define BLOCK_8X4 2
#define BLOCK_8X8 3
#define BLOCK_8X16 4
#define BLOCK_16X8 5
#define BLOCK_16X16 6
#define BLOCK_16X32 7
#define BLOCK_32X16 8
#define BLOCK_32X32 9
#define BLOCK_32X64 10
#define BLOCK_64X32 11
#define BLOCK_64X64 12
#define BLOCK_64X128 13
#define BLOCK_128X64 14
#define BLOCK_128X128 15
#define BLOCK_4X16 16
#define BLOCK_16X4 17
#define BLOCK_8X32 18
#define BLOCK_32X8 19
#define BLOCK_16X64 20
#define BLOCK_64X16 21
#define BLOCK_SIZES_ALL 22
#define BLOCK_SIZES 19
#define BLOCK_INVALID BLOCK_SIZES_ALL

// YUV buffer flags
#define YV12_FLAG_HIGHBITDEPTH 1

// ============================================================================
// UTILITY MACROS (extracted from AOM)
// ============================================================================

#define AOMMIN(x, y) (((x) < (y)) ? (x) : (y))
#define AOMMAX(x, y) (((x) > (y)) ? (x) : (y))
#define CLIP(x, min, max) (AOMMAX(min, AOMMIN(x, max)))

// Convert to shortptr for high bit depth
#define CONVERT_TO_SHORTPTR(x) ((uint16_t*)(((uintptr_t)(x)) + 0))

// ============================================================================
// TYPE DEFINITIONS (extracted from AOM)  
// ============================================================================

typedef int BLOCK_SIZE;

// Motion vector structure
typedef struct {
  int16_t row;
  int16_t col;
} MV;

// YUV buffer configuration (simplified version)
typedef struct {
  uint8_t *buffer_alloc;        // Allocated buffer memory
  size_t buffer_alloc_sz;       // Size of allocated buffer
  uint8_t *buffers[MAX_MB_PLANE]; // Pointers to plane buffers
  int strides[MAX_MB_PLANE];    // Stride for each plane
  int heights[MAX_MB_PLANE];    // Height for each plane
  int y_crop_height;            // Crop height for Y plane
  int y_crop_width;             // Crop width for Y plane  
  int flags;                    // Buffer flags (e.g. high bit depth)
} YV12_BUFFER_CONFIG;

// Plane information structure
typedef struct {
  int subsampling_x;           // Horizontal subsampling
  int subsampling_y;           // Vertical subsampling
} PLANE_INFO;

// Macroblock structure (simplified)
typedef struct {
  int bd;                      // Bit depth
  PLANE_INFO plane[MAX_MB_PLANE]; // Per-plane information
  void *error_info;            // Error handling (unused in standalone)
} MACROBLOCKD;

// ============================================================================
// BLOCK SIZE UTILITIES (extracted from AOM)
// ============================================================================

// Block width lookup table
extern const int block_size_wide[BLOCK_SIZES_ALL];

// Block height lookup table  
extern const int block_size_high[BLOCK_SIZES_ALL];

// ============================================================================
// MATH UTILITIES (extracted from AOM)
// ============================================================================

// Fast approximation of exp(-x) for temporal filtering
static inline float approx_exp(float y) {
#define A ((1 << 23) / 0.69314718056f)  // (1 << 23) / ln(2)
#define B 127  // IEEE floating point standard offset
#define C 60801  // Magic number for accuracy
  union {
    float as_float;
    int32_t as_int32;
  } container;
  container.as_int32 = ((int32_t)(y * A)) + ((B << 23) - C);
  return container.as_float;
#undef A
#undef B  
#undef C
}

// Round positive float to nearest integer
static inline int iroundpf(float x) {
  return (int)(x + 0.5f);
}

// Check if frame is high bit depth
static inline int is_frame_high_bitdepth(const YV12_BUFFER_CONFIG *buf) {
  return !!(buf->flags & YV12_FLAG_HIGHBITDEPTH);
}

// ============================================================================
// MEMORY MANAGEMENT (simplified)
// ============================================================================

// Aligned memory allocation (simplified version)
static inline void* aom_memalign(size_t align, size_t size) {
  return malloc(size);
}

// Memory free  
static inline void aom_free(void *ptr) {
  free(ptr);
}

// Error handling (simplified - just abort on error)
static inline void aom_internal_error(void *error_info, int error, const char *message) {
  (void)error_info;
  (void)error;
  fprintf(stderr, "AOM Error: %s\n", message);
  abort();
}

// ============================================================================
// MAIN TEMPORAL FILTER FUNCTION
// ============================================================================

/*!\brief Applies temporal filtering to a macroblock
 *
 * This is the main temporal filtering function that applies noise reduction
 * by combining information from multiple frames in a temporal window.
 *
 * \param[in]   frame_to_filter Frame buffer to be filtered
 * \param[in]   mbd            Macroblock structure with plane info  
 * \param[in]   block_size     Size of the block to filter
 * \param[in]   mb_row         Macroblock row position
 * \param[in]   mb_col         Macroblock column position
 * \param[in]   num_planes     Number of planes (1=Y only, 3=YUV)
 * \param[in]   noise_levels   Noise level per plane
 * \param[in]   subblock_mvs   Motion vectors for 4 subblocks
 * \param[in]   subblock_mses  Mean squared error for 4 subblocks
 * \param[in]   q_factor       Quantization factor
 * \param[in]   filter_strength Filter strength parameter
 * \param[in]   tf_wgt_calc_lvl Weight calculation level (0 or 1)
 * \param[in]   pred           Prediction buffer
 * \param[out]  accum          Accumulation buffer (output)
 * \param[out]  count          Count buffer (output)
 */
void av1_apply_temporal_filter(
    const YV12_BUFFER_CONFIG *frame_to_filter, 
    const MACROBLOCKD *mbd,
    const BLOCK_SIZE block_size, 
    const int mb_row, 
    const int mb_col,
    const int num_planes, 
    const double *noise_levels, 
    const MV *subblock_mvs,
    const int *subblock_mses, 
    const int q_factor, 
    const int filter_strength,
    int tf_wgt_calc_lvl, 
    const uint8_t *pred, 
    uint32_t *accum,
    uint16_t *count);

// ============================================================================
// BLOCK SIZE LOOKUP TABLES (extracted from AOM)
// ============================================================================

const int block_size_wide[BLOCK_SIZES_ALL] = {
  4, 4, 8, 8, 8, 16, 16, 16, 32, 32, 32, 64, 64, 64, 128, 128, 4, 16, 8, 32, 16, 64
};

const int block_size_high[BLOCK_SIZES_ALL] = {
  4, 8, 4, 8, 16, 8, 16, 32, 16, 32, 64, 32, 64, 128, 64, 128, 16, 4, 32, 8, 64, 16  
};

// ============================================================================
// HELPER FUNCTIONS (extracted from temporal_filter.c)
// ============================================================================

/*!\brief Computes pixel-wise squared differences between reference and target
 *
 * \param[in]   ref              Reference buffer
 * \param[in]   ref_offset       Offset in reference buffer
 * \param[in]   ref_stride       Reference buffer stride
 * \param[in]   tgt              Target buffer  
 * \param[in]   tgt_offset       Offset in target buffer
 * \param[in]   tgt_stride       Target buffer stride
 * \param[in]   height           Block height
 * \param[in]   width            Block width
 * \param[in]   is_high_bitdepth Whether buffers are high bit depth
 * \param[out]  square_diff      Output squared differences
 */
static inline void compute_square_diff(const uint8_t *ref, const int ref_offset,
                                       const int ref_stride, const uint8_t *tgt,
                                       const int tgt_offset,
                                       const int tgt_stride, const int height,
                                       const int width,
                                       const int is_high_bitdepth,
                                       uint32_t *square_diff) {
  const uint16_t *ref16 = CONVERT_TO_SHORTPTR(ref);
  const uint16_t *tgt16 = CONVERT_TO_SHORTPTR(tgt);

  int ref_idx = 0;
  int tgt_idx = 0;
  int idx = 0;
  for (int i = 0; i < height; ++i) {
    for (int j = 0; j < width; ++j) {
      const uint16_t ref_value = is_high_bitdepth ? ref16[ref_offset + ref_idx]
                                                  : ref[ref_offset + ref_idx];
      const uint16_t tgt_value = is_high_bitdepth ? tgt16[tgt_offset + tgt_idx]
                                                  : tgt[tgt_offset + tgt_idx];
      const uint32_t diff = (ref_value > tgt_value) ? (ref_value - tgt_value)
                                                    : (tgt_value - ref_value);
      square_diff[idx] = diff * diff;

      ++ref_idx;
      ++tgt_idx;
      ++idx;
    }
    ref_idx += (ref_stride - width);
    tgt_idx += (tgt_stride - width);
  }
}

/*!\brief Accumulates luma squared errors for chroma plane filtering
 * 
 * \param[in]   square_diff    Luma squared differences
 * \param[out]  luma_sse_sum   Accumulated luma SSE for chroma filtering
 * \param[in]   block_height   Chroma block height
 * \param[in]   block_width    Chroma block width  
 * \param[in]   ss_x_shift     Chroma horizontal subsampling shift
 * \param[in]   ss_y_shift     Chroma vertical subsampling shift
 */
static void compute_luma_sq_error_sum(uint32_t *square_diff,
                                      uint32_t *luma_sse_sum, int block_height,
                                      int block_width, int ss_x_shift,
                                      int ss_y_shift) {
  for (int i = 0; i < block_height; ++i) {
    for (int j = 0; j < block_width; ++j) {
      for (int ii = 0; ii < (1 << ss_y_shift); ++ii) {
        for (int jj = 0; jj < (1 << ss_x_shift); ++jj) {
          const int yy = (i << ss_y_shift) + ii;     // Y-coord on Y-plane.
          const int xx = (j << ss_x_shift) + jj;     // X-coord on Y-plane.
          const int ww = block_width << ss_x_shift;  // Width of Y-plane.
          luma_sse_sum[i * block_width + j] += square_diff[yy * ww + xx];
        }
      }
    }
  }
}

// ============================================================================
// MAIN TEMPORAL FILTER IMPLEMENTATION (from av1_apply_temporal_filter_c)
// ============================================================================

void av1_apply_temporal_filter(
    const YV12_BUFFER_CONFIG *frame_to_filter, const MACROBLOCKD *mbd,
    const BLOCK_SIZE block_size, const int mb_row, const int mb_col,
    const int num_planes, const double *noise_levels, const MV *subblock_mvs,
    const int *subblock_mses, const int q_factor, const int filter_strength,
    int tf_wgt_calc_lvl, const uint8_t *pred, uint32_t *accum,
    uint16_t *count) {
  
  // Block information.
  const int mb_height = block_size_high[block_size];
  const int mb_width = block_size_wide[block_size];
  const int mb_pels = mb_height * mb_width;
  const int is_high_bitdepth = is_frame_high_bitdepth(frame_to_filter);
  const uint16_t *pred16 = CONVERT_TO_SHORTPTR(pred);
  
  // Frame information.
  const int frame_height = frame_to_filter->y_crop_height;
  const int frame_width = frame_to_filter->y_crop_width;
  const int min_frame_size = AOMMIN(frame_height, frame_width);
  
  // Variables to simplify combined error calculation.
  const double inv_factor = 1.0 / ((TF_WINDOW_BLOCK_BALANCE_WEIGHT + 1) *
                                   TF_SEARCH_ERROR_NORM_WEIGHT);
  const double weight_factor =
      (double)TF_WINDOW_BLOCK_BALANCE_WEIGHT * inv_factor;
      
  // Decay factors for non-local mean approach.
  double decay_factor[MAX_MB_PLANE] = { 0 };
  
  // Adjust filtering based on q.
  // Larger q -> stronger filtering -> larger weight.
  // Smaller q -> weaker filtering -> smaller weight.
  double q_decay = pow((double)q_factor / TF_Q_DECAY_THRESHOLD, 2);
  q_decay = CLIP(q_decay, 1e-5, 1);
  if (q_factor >= TF_QINDEX_CUTOFF) {
    // Max q_factor is 255, therefore the upper bound of q_decay is 8.
    // We do not need a clip here.
    q_decay = 0.5 * pow((double)q_factor / 64, 2);
  }
  
  // Smaller strength -> smaller filtering weight.
  double s_decay = pow((double)filter_strength / TF_STRENGTH_THRESHOLD, 2);
  s_decay = CLIP(s_decay, 1e-5, 1);
  
  for (int plane = 0; plane < num_planes; plane++) {
    // Larger noise -> larger filtering weight.
    const double n_decay = 0.5 + log(2 * noise_levels[plane] + 5.0);
    decay_factor[plane] = 1 / (n_decay * q_decay * s_decay);
  }
  
  double d_factor[4] = { 0 };
  for (int subblock_idx = 0; subblock_idx < 4; subblock_idx++) {
    // Larger motion vector -> smaller filtering weight.
    const MV mv = subblock_mvs[subblock_idx];
    const double distance = sqrt(pow(mv.row, 2) + pow(mv.col, 2));
    double distance_threshold = min_frame_size * TF_SEARCH_DISTANCE_THRESHOLD;
    distance_threshold = AOMMAX(distance_threshold, 1);
    d_factor[subblock_idx] = distance / distance_threshold;
    d_factor[subblock_idx] = AOMMAX(d_factor[subblock_idx], 1);
  }

  // Allocate memory for pixel-wise squared differences. They,
  // regardless of the subsampling, are assigned with memory of size `mb_pels`.
  uint32_t *square_diff = (uint32_t*)aom_memalign(16, mb_pels * sizeof(uint32_t));
  if (!square_diff) {
    aom_internal_error(mbd->error_info, -1, "Error allocating temporal filter data");
  }
  memset(square_diff, 0, mb_pels * sizeof(square_diff[0]));

  // Allocate memory for accumulated luma squared error. This value will be
  // consumed while filtering the chroma planes.
  uint32_t *luma_sse_sum = (uint32_t*)aom_memalign(32, mb_pels * sizeof(uint32_t));
  if (!luma_sse_sum) {
    aom_free(square_diff);
    aom_internal_error(mbd->error_info, -1, "Error allocating temporal filter data");
  }
  memset(luma_sse_sum, 0, mb_pels * sizeof(luma_sse_sum[0]));

  // Get window size for pixel-wise filtering.
  assert(TF_WINDOW_LENGTH % 2 == 1);
  const int half_window = TF_WINDOW_LENGTH >> 1;

  // Handle planes in sequence.
  int plane_offset = 0;
  for (int plane = 0; plane < num_planes; ++plane) {
    // Locate pixel on reference frame.
    const int subsampling_y = mbd->plane[plane].subsampling_y;
    const int subsampling_x = mbd->plane[plane].subsampling_x;
    const int h = mb_height >> subsampling_y;  // Plane height.
    const int w = mb_width >> subsampling_x;   // Plane width.
    const int frame_stride =
        frame_to_filter->strides[plane == AOM_PLANE_Y ? 0 : 1];
    const int frame_offset = mb_row * h * frame_stride + mb_col * w;
    const uint8_t *ref = frame_to_filter->buffers[plane];
    const int ss_y_shift =
        subsampling_y - mbd->plane[AOM_PLANE_Y].subsampling_y;
    const int ss_x_shift =
        subsampling_x - mbd->plane[AOM_PLANE_Y].subsampling_x;
    const int num_ref_pixels = TF_WINDOW_LENGTH * TF_WINDOW_LENGTH +
                               ((plane) ? (1 << (ss_x_shift + ss_y_shift)) : 0);
    const double inv_num_ref_pixels = 1.0 / num_ref_pixels;

    // Filter U-plane and V-plane using Y-plane. This is because motion
    // search is only done on Y-plane, so the information from Y-plane will
    // be more accurate. The luma sse sum is reused in both chroma planes.
    if (plane == AOM_PLANE_U)
      compute_luma_sq_error_sum(square_diff, luma_sse_sum, h, w, ss_x_shift,
                                ss_y_shift);
    compute_square_diff(ref, frame_offset, frame_stride, pred, plane_offset, w,
                        h, w, is_high_bitdepth, square_diff);

    // Perform filtering.
    int pred_idx = 0;
    for (int i = 0; i < h; ++i) {
      for (int j = 0; j < w; ++j) {
        // non-local mean approach
        uint64_t sum_square_diff = 0;

        for (int wi = -half_window; wi <= half_window; ++wi) {
          for (int wj = -half_window; wj <= half_window; ++wj) {
            const int y = CLIP(i + wi, 0, h - 1);  // Y-coord on current plane.
            const int x = CLIP(j + wj, 0, w - 1);  // X-coord on current plane.
            sum_square_diff += square_diff[y * w + x];
          }
        }

        sum_square_diff += luma_sse_sum[i * w + j];

        // Scale down the difference for high bit depth input.
        if (mbd->bd > 8) sum_square_diff >>= ((mbd->bd - 8) * 2);

        // Combine window error and block error, and normalize it.
        const double window_error = sum_square_diff * inv_num_ref_pixels;
        const int subblock_idx = (i >= h / 2) * 2 + (j >= w / 2);
        const double block_error = (double)subblock_mses[subblock_idx];
        const double combined_error =
            weight_factor * window_error + block_error * inv_factor;

        // Compute filter weight.
        double scaled_error =
            combined_error * d_factor[subblock_idx] * decay_factor[plane];
        scaled_error = AOMMIN(scaled_error, 7);
        int weight;
        if (tf_wgt_calc_lvl == 0) {
          weight = (int)(exp(-scaled_error) * TF_WEIGHT_SCALE);
        } else {
          const float fweight =
              approx_exp((float)-scaled_error) * TF_WEIGHT_SCALE;
          weight = iroundpf(fweight);
        }

        const int idx = plane_offset + pred_idx;  // Index with plane shift.
        const int pred_value = is_high_bitdepth ? pred16[idx] : pred[idx];
        accum[idx] += weight * pred_value;
        count[idx] += weight;

        ++pred_idx;
      }
    }
    plane_offset += h * w;
  }

  aom_free(square_diff);
  aom_free(luma_sse_sum);
}
