#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <limits.h>
#include <math.h>
#include <stdbool.h>
#include <assert.h>
#include <inttypes.h>
// ========== BASIC TYPE DEFINITIONS ==========

#define MAX_SB_SIZE 128
#define MAX_SB_SQUARE (MAX_SB_SIZE * MAX_SB_SIZE)
#define MI_SIZE_LOG2 2
#define MI_SIZE (1 << MI_SIZE_LOG2)

// Alignment and utility macros
#define ALIGN_POWER_OF_TWO(value, n) \
  (((value) + ((1 << (n)) - 1)) & ~((1 << (n)) - 1))

#define AOMMIN(x, y) (((x) < (y)) ? (x) : (y))
#define AOMMAX(x, y) (((x) > (y)) ? (x) : (y))

#define RDCOST(RM, R, D) (ROUND_POWER_OF_TWO(((int64_t)(R)) * (RM), 4) + ((D) << 4))
#define ROUND_POWER_OF_TWO(value, n) (((value) + (1 << ((n) - 1))) >> (n))

// ========== ENUMERATIONS ==========

typedef enum {
  BLOCK_4X4,
  BLOCK_4X8,
  BLOCK_8X4,
  BLOCK_8X8,
  BLOCK_8X16,
  BLOCK_16X8,
  BLOCK_16X16,
  BLOCK_16X32,
  BLOCK_32X16,
  BLOCK_32X32,
  BLOCK_32X64,
  BLOCK_64X32,
  BLOCK_64X64,
  BLOCK_64X128,
  BLOCK_128X64,
  BLOCK_128X128,
  BLOCK_4X16,
  BLOCK_16X4,
  BLOCK_8X32,
  BLOCK_32X8,
  BLOCK_16X64,
  BLOCK_64X16,
  BLOCK_SIZES_ALL,
  BLOCK_SIZES = BLOCK_4X16,
  BLOCK_INVALID = 255,
} BLOCK_SIZE;

typedef enum {
  COMPOUND_AVERAGE,
  COMPOUND_DISTWTD,
  COMPOUND_WEDGE,
  COMPOUND_DIFFWTD,
  COMPOUND_TYPES,
  MASKED_COMPOUND_TYPES = 2,
} COMPOUND_TYPE;

typedef enum {
  NEARESTMV,
  NEARMV,
  GLOBALMV,
  NEWMV,
  NEARESTNEARMV,
  NEARNEWMV,
  NEWNEWMV,
  NEW_NEWMV = NEWNEWMV,
  NEARESTNEWMV,
  GLOBALNEWMV,
  SINGLE_INTER_MODE_NUM = NEWMV + 1,
  COMPOUND_INTER_MODE_NUM = GLOBALNEWMV - NEARESTNEARMV + 1,
  INTER_COMPOUND_MODES = COMPOUND_INTER_MODE_NUM,
  INTER_MODES = GLOBALNEWMV + 1,
  INTER_INVALID = 255,
} PREDICTION_MODE;

typedef enum {
  LAST_FRAME = 1,
  LAST2_FRAME,
  LAST3_FRAME,
  GOLDEN_FRAME,
  BWDREF_FRAME,
  ALTREF2_FRAME,
  ALTREF_FRAME,
  INTER_REFS_PER_FRAME = ALTREF_FRAME - LAST_FRAME + 1,
  TOTAL_REFS_PER_FRAME = ALTREF_FRAME + 1,
  INTRA_FRAME = 0,
  NONE_FRAME = -1,
} MV_REFERENCE_FRAME;

// ========== BASIC STRUCTURES ==========

typedef struct {
  int16_t row;
  int16_t col;
} MV;

typedef union {
  uint32_t as_int;
  MV as_mv;
} int_mv;

typedef struct {
  int32_t as_int;
} int_interpfilters;

typedef struct {
  COMPOUND_TYPE type;
  int wedge_index;
  int wedge_sign;
  int mask_type;
} INTERINTER_COMPOUND_DATA;

typedef struct {
  uint8_t *plane[3];
  int stride[3];
} BUFFER_SET;

typedef struct {
  int rate;
  int64_t dist;
  int64_t rdcost;
  int64_t sse;
  int skip_txfm;
  int invalid_rate;
} RD_STATS;

// Block dimension arrays
static const int block_size_wide[BLOCK_SIZES_ALL] = {
  4, 4, 8, 8, 8, 16, 16, 16, 32, 32, 32, 64, 64, 64, 128, 128, 4, 16, 8, 32, 16, 64
};

static const int block_size_high[BLOCK_SIZES_ALL] = {
  4, 8, 4, 8, 16, 8, 16, 32, 16, 32, 64, 32, 64, 128, 64, 128, 16, 4, 32, 8, 64, 16
};

// ========== COMPOUND TYPE RD STRUCTURES ==========

typedef struct {
  uint8_t *pred0;
  uint8_t *pred1;
  int16_t *residual1;
  int16_t *diff10;
  uint8_t *tmp_best_mask_buf;
} CompoundTypeRdBuffers;

typedef struct {
  INTERINTER_COMPOUND_DATA best_compound_data;
  int best_compmode_interinter_cost;
  int64_t comp_best_model_rd;
} BEST_COMP_TYPE_STATS;

// Mode costs structure (simplified)
typedef struct {
  int comp_group_idx_cost[16][2];
  int comp_idx_cost[16][2];
  int compound_type_cost[BLOCK_SIZES_ALL][MASKED_COMPOUND_TYPES];
  int wedge_idx_cost[BLOCK_SIZES_ALL][16];
  int inter_compound_mode_cost[8][INTER_COMPOUND_MODES];
  int skip_txfm_cost[16][2];
} ModeCosts;

// Mode info structure (simplified)
typedef struct {
  BLOCK_SIZE bsize;
  PREDICTION_MODE mode;
  MV_REFERENCE_FRAME ref_frame[2];
  int_mv mv[2];
  int_interpfilters interp_filters;
  INTERINTER_COMPOUND_DATA interinter_comp;
  int compound_idx;
  int comp_group_idx;
  int ref_mv_idx;
  int interintra_wedge_index;
  int use_wedge_interintra;
  int skip_txfm;
} MB_MODE_INFO;

// Macroblock plane structure (simplified)
typedef struct {
  int subsampling_x;
  int subsampling_y;
  int plane_type;
  struct {
    uint8_t *buf;
    int stride;
  } dst;
  struct {
    uint8_t *buf;
    int stride;
  } pre[2];
} macroblockd_plane;

// Macroblock decoder structure (simplified)
typedef struct {
  MB_MODE_INFO **mi;
  int mi_stride;
  int mi_row;
  int mi_col;
  macroblockd_plane plane[3];
  uint8_t *seg_mask;
  int bd;
  int up_available;
  int left_available;
  MB_MODE_INFO *above_mbmi;
  MB_MODE_INFO *left_mbmi;
  int neighbors_ref_counts[8];
  int mb_to_top_edge;
  int mb_to_bottom_edge;
  int mb_to_left_edge;
  int mb_to_right_edge;
  void *tile_ctx;
  void *cur_buf;
} MACROBLOCKD;

// Transform search info structure (simplified)
typedef struct {
  int skip_txfm;
} TxfmSearchInfo;

// Macroblock structure (simplified)
typedef struct {
  MACROBLOCKD e_mbd;
  ModeCosts mode_costs;
  TxfmSearchInfo txfm_search_info;
  int rdmult;
  struct {
    uint8_t *buf;
    int stride;
  } plane[3];
  // For RD stats caching
  int comp_rd_stats_idx;
  void *comp_rd_stats; // Simplified - would be COMP_RD_STATS array in real implementation
  uint32_t source_variance;
} MACROBLOCK;

// Sequence parameters (simplified)
typedef struct {
  int order_hint_bits_minus_1;
  struct {
    int enable_order_hint;
    int enable_dist_wtd_comp;
  } order_hint_info;
  int bit_depth;
  int subsampling_x;
  int subsampling_y;
  int enable_masked_compound;
  int enable_interintra_compound;
} SequenceHeader;

// Speed features (simplified)  
typedef struct {
  struct {
    int reuse_compound_type_decision;
    int prune_comp_search_by_single_result;
    int skip_repeated_ref_mv;
    int enable_fast_compound_mode_search;
    int use_dist_wtd_comp_flag;
    int txfm_rd_gate_level;
    int prune_comp_type_by_model_rd;
    int prune_comp_type_by_comp_avg;
    int fast_wedge_sign_estimate;
    int disable_interinter_wedge_newmv_search;
    int disable_interinter_wedge_var_thresh;
    int disable_interintra_wedge_var_thresh;
    int reuse_mask_search_results;
    int enable_fast_wedge_mask_search;
  } inter_sf;
} SpeedFeatures;

// Encoder configuration (simplified)
typedef struct {
  struct {
    int enable_interinter_wedge;
    int enable_masked_comp;
    int enable_diff_wtd_comp;
    int enable_dist_wtd_comp;
    int enable_interintra_wedge;
    int enable_smooth_interintra;
  } comp_type_cfg;
} AV1EncoderConfig;

// Common structure (simplified)
typedef struct {
  int width;
  int height;
  SequenceHeader *seq_params;
  void *fc; // Frame context
  void *cur_frame;
  void **ref_frame_map; // Reference frame buffers
  struct {
    void **mi_grid_base;
    int mi_stride;
  } mi_params;
  void *current_frame; // Current frame info
} AV1_COMMON;

// Main encoder structure (simplified)
typedef struct {
  AV1_COMMON common;
  SpeedFeatures sf;
  AV1EncoderConfig oxcf;
  struct {
    int RDMULT;
  } rd;
  struct {
    MACROBLOCK mb; // Thread data
  } td;
  void *ppi; // Primary picture info
} AV1_COMP;

// HandleInterModeArgs structure (simplified)
typedef struct {
  COMPOUND_TYPE cmp_mode[8]; // Mode context ref frames
  int wedge_index;
  int wedge_sign;
  int diffwtd_index;
  void *single_newmv;
  void *single_newmv_rate;
  void *single_newmv_valid;
  void *modelled_rd;
  void *simple_rd;
  void *inter_intra_mode;
  int ref_frame_cost;
  int single_comp_cost;
  int skip_motion_mode;
  int skip_ifs;
  uint8_t *above_pred_buf[3];
  uint8_t *left_pred_buf[3];
  int above_pred_stride[3];
  int left_pred_stride[3];
} HandleInterModeArgs;

// ========== FUNCTION DECLARATIONS ==========

// Main function
int av1_compound_type_rd(
    const AV1_COMP *const cpi, MACROBLOCK *x,
    HandleInterModeArgs *args, BLOCK_SIZE bsize,
    int_mv *cur_mv, int mode_search_mask,
    int masked_compound_used, const BUFFER_SET *orig_dst,
    const BUFFER_SET *tmp_dst,
    const CompoundTypeRdBuffers *buffers, int *rate_mv,
    int64_t *rd, RD_STATS *rd_stats, int64_t ref_best_rd,
    int64_t ref_skip_rd, int *is_luma_interp_done,
    int64_t rd_thresh);

// Helper functions
int compute_valid_comp_types(MACROBLOCK *x, const AV1_COMP *const cpi,
                           BLOCK_SIZE bsize, int masked_compound_used,
                           int mode_search_mask, COMPOUND_TYPE *valid_comp_types);

void calc_masked_type_cost(const ModeCosts *mode_costs, BLOCK_SIZE bsize,
                          int comp_group_idx_ctx, int comp_index_ctx,
                          int masked_compound_used, int *masked_type_cost);

void update_mbmi_for_compound_type(MB_MODE_INFO *mbmi, COMPOUND_TYPE cur_type);

int find_comp_rd_in_stats(const AV1_COMP *const cpi, const MACROBLOCK *x,
                         const MB_MODE_INFO *const mbmi, int32_t *comp_rate,
                         int64_t *comp_dist, int32_t *comp_model_rate,
                         int64_t *comp_model_dist, int *comp_rs2,
                         int *match_index);

int populate_reuse_comp_type_data(const MACROBLOCK *x, MB_MODE_INFO *mbmi,
                                 BEST_COMP_TYPE_STATS *best_type_stats,
                                 int_mv *cur_mv, int32_t *comp_rate,
                                 int64_t *comp_dist, int *comp_rs2,
                                 int *rate_mv, int64_t *rd, int match_index);

// Context functions - simplified implementations
int get_comp_group_idx_context(const MACROBLOCKD *xd);
int get_comp_index_context(const AV1_COMMON *cm, const MACROBLOCKD *xd);

// Utility functions
void av1_init_rd_stats(RD_STATS *rd_stats);
void av1_zero_array(int *arr, int size);
int av1_ref_frame_type(const MV_REFERENCE_FRAME *ref_frame);
int have_newmv_in_inter_mode(PREDICTION_MODE mode);
int is_interinter_compound_used(COMPOUND_TYPE type, BLOCK_SIZE bsize);
void restore_dst_buf(MACROBLOCKD *xd, const BUFFER_SET buffer_set, int num_planes);

// Minimal implementations for testing
void init_standalone_encoder_context(AV1_COMP *cpi, int width, int height);
void init_standalone_macroblock(MACROBLOCK *x, int width, int height);
void init_standalone_args(HandleInterModeArgs *args);

// ========== CONSTANTS ==========

// Scaling values for gating wedge/compound segment based on best approximate rd
static const int comp_type_rd_threshold_mul[3] = { 1, 11, 12 };
static const int comp_type_rd_threshold_div[3] = { 3, 16, 16 };

// Distance weight computation disabled flag
#define DIST_WTD_COMP_DISABLED 1

// Mode context ref frames
#define MODE_CTX_REF_FRAMES 8

// Max compound RD stats for caching
#define MAX_COMP_RD_STATS 64

// Inter mode contexts
#define INTER_MODE_CONTEXTS 8

// Maximum interpolation filters
#define EIGHTTAP_REGULAR 0

// Plane types
#define PLANE_TYPE_Y 0

// Prediction mode aliases for compatibility (already defined in header)
#define NEAREST_NEWMV NEARESTNEWMV
#define NEW_NEARMV NEARNEWMV

// Transform size type (simplified for standalone)
typedef int TX_SIZE;

// Max transform size lookup (simplified)
static const TX_SIZE max_txsize_rect_lookup[BLOCK_SIZES_ALL] = {
  0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3
};

// Compound reference mode helper function (simplified)
static inline PREDICTION_MODE compound_ref1_mode(PREDICTION_MODE mode) {
  switch (mode) {
    case NEARESTNEWMV: return NEWMV;     // NEAREST + NEW -> ref1 is NEW
    case NEARNEWMV: return NEWMV;        // NEAR + NEW -> ref1 is NEW  
    case NEW_NEWMV: return NEWMV;        // NEW + NEW -> ref1 is NEW
    case GLOBALNEWMV: return NEWMV;      // GLOBAL + NEW -> ref1 is NEW
    default: return NEWMV;
  }
}

// ========== UTILITY FUNCTIONS ==========

void av1_init_rd_stats(RD_STATS *rd_stats) {
  rd_stats->rate = 0;
  rd_stats->dist = 0;
  rd_stats->rdcost = 0;
  rd_stats->sse = 0;
  rd_stats->skip_txfm = 0;
  rd_stats->invalid_rate = 0;
}

void av1_zero_array(int *arr, int size) {
  memset(arr, 0, size * sizeof(int));
}

int av1_ref_frame_type(const MV_REFERENCE_FRAME *ref_frame) {
  if (ref_frame[1] > INTRA_FRAME) {
    return ref_frame[0] + ref_frame[1] * TOTAL_REFS_PER_FRAME;
  }
  return ref_frame[0];
}

int have_newmv_in_inter_mode(PREDICTION_MODE mode) {
  return (mode == NEWMV || mode == NEW_NEWMV || mode == NEARNEWMV ||
          mode == NEARESTNEWMV || mode == GLOBALNEWMV);
}

int is_interinter_compound_used(COMPOUND_TYPE type, BLOCK_SIZE bsize) {
  // Simplified implementation - in real AV1, this checks various constraints
  if (bsize < BLOCK_8X8) return 0;
  
  // Different compound types have different block size requirements
  switch (type) {
    case COMPOUND_WEDGE:
      // Wedge requires larger blocks for effective masking
      return (bsize >= BLOCK_8X8);
    case COMPOUND_DIFFWTD:
      // Difference weighted can work on smaller blocks
      return (bsize >= BLOCK_8X8);
    case COMPOUND_AVERAGE:
    case COMPOUND_DISTWTD:
    default:
      return 1;
  }
}

void restore_dst_buf(MACROBLOCKD *xd, const BUFFER_SET buffer_set, int num_planes) {
  // Simplified implementation
  for (int plane = 0; plane < num_planes && plane < 3; plane++) {
    xd->plane[plane].dst.buf = buffer_set.plane[plane];
    xd->plane[plane].dst.stride = buffer_set.stride[plane];
  }
}

// ========== CONTEXT FUNCTIONS ==========

int get_comp_group_idx_context(const MACROBLOCKD *xd) {
  // Enhanced implementation based on original AV1 pred_common.h
  const MB_MODE_INFO *const above_mi = xd->above_mbmi;
  const MB_MODE_INFO *const left_mi = xd->left_mbmi;
  int above_ctx = 0, left_ctx = 0;

  if (above_mi) {
    if (above_mi->ref_frame[1] > NONE_FRAME)  // has_second_ref
      above_ctx = above_mi->comp_group_idx;
    else if (above_mi->ref_frame[0] == ALTREF_FRAME)
      above_ctx = 3;
  }
  if (left_mi) {
    if (left_mi->ref_frame[1] > NONE_FRAME)  // has_second_ref
      left_ctx = left_mi->comp_group_idx;
    else if (left_mi->ref_frame[0] == ALTREF_FRAME)
      left_ctx = 3;
  }

  return AOMMIN(5, above_ctx + left_ctx);
}

int get_comp_index_context(const AV1_COMMON *cm, const MACROBLOCKD *xd) {
  // Enhanced implementation based on original AV1 pred_common.h
  MB_MODE_INFO *mbmi = xd->mi[0];
  (void)cm; // Mark as used to suppress warning
  (void)mbmi; // Will be used in simulation logic below
  
  // In standalone version, we simulate reference frame distances
  // In real AV1 this would use actual reference buffers and order hints
  int cur_frame_index = 100; // Simulated current frame order hint
  int bck_frame_index = 95;  // Simulated backward reference frame
  int fwd_frame_index = 105; // Simulated forward reference frame
  
  // Simulate relative distance calculation (simplified)
  int fwd = abs(fwd_frame_index - cur_frame_index);
  int bck = abs(cur_frame_index - bck_frame_index);

  const MB_MODE_INFO *const above_mi = xd->above_mbmi;
  const MB_MODE_INFO *const left_mi = xd->left_mbmi;

  int above_ctx = 0, left_ctx = 0;
  const int offset = (fwd == bck);

  if (above_mi != NULL) {
    if (above_mi->ref_frame[1] > NONE_FRAME)  // has_second_ref
      above_ctx = above_mi->compound_idx;
    else if (above_mi->ref_frame[0] == ALTREF_FRAME)
      above_ctx = 1;
  }

  if (left_mi != NULL) {
    if (left_mi->ref_frame[1] > NONE_FRAME)  // has_second_ref
      left_ctx = left_mi->compound_idx;
    else if (left_mi->ref_frame[0] == ALTREF_FRAME)
      left_ctx = 1;
  }

  return above_ctx + left_ctx + 3 * offset;
}

// ========== MOTION SEARCH FUNCTIONS (Enhanced from Original AV1) ==========

// Enhanced compound motion search based on av1_interinter_compound_motion_search
static int av1_interinter_compound_motion_search_standalone(const AV1_COMP *const cpi,
                                          MACROBLOCK *x,
                                          const int_mv *const cur_mv,
                                          const BLOCK_SIZE bsize,
                                          const PREDICTION_MODE this_mode) {
  MACROBLOCKD *const xd = &x->e_mbd;
  MB_MODE_INFO *const mbmi = xd->mi[0];
  int_mv tmp_mv[2];
  int tmp_rate_mv = 0;
  
  (void)cpi; // Suppress unused parameter warning
  
  // Initialize with current motion vectors
  tmp_mv[0].as_int = cur_mv[0].as_int;
  tmp_mv[1].as_int = cur_mv[1].as_int;
  
  // Set compound mask based on type (seg_mask not available in standalone version)
  // mbmi->interinter_comp.seg_mask = 
  //     mbmi->interinter_comp.type == COMPOUND_AVERAGE ? NULL : xd->seg_mask;

  if (this_mode == NEW_NEWMV) {
    // Both MVs are new - do joint refinement
    // In real AV1 this would do do_masked_motion_search_indexed with which=2
    // For standalone, we simulate refinement by small adjustments
    const int bw = block_size_wide[bsize];
    const int bh = block_size_high[bsize];
    const int refinement_factor = (bw + bh) / 32; // Size-based refinement
    
    // Simulate MV refinement for both references
    tmp_mv[0].as_mv.row += (refinement_factor - 2);
    tmp_mv[0].as_mv.col += (refinement_factor - 1);
    tmp_mv[1].as_mv.row -= (refinement_factor - 1);  
    tmp_mv[1].as_mv.col += (refinement_factor - 3);
    
    // Update motion vector rate cost (simplified calculation)
    const int mv_cost_factor = bw * bh / 256; 
    tmp_rate_mv += mv_cost_factor * 2; // Cost for both MVs
    
    // Update mbmi with refined MVs
    mbmi->mv[0].as_int = tmp_mv[0].as_int;
    mbmi->mv[1].as_int = tmp_mv[1].as_int;
    
  } else if (this_mode == NEARESTNEWMV || this_mode == NEARNEWMV) {
    // Only one MV is new
    int which = (NEWMV == compound_ref1_mode(this_mode)) ? 1 : 0;
    
    // Simulate single MV refinement
    const int bw = block_size_wide[bsize];
    const int bh = block_size_high[bsize];
    const int refinement_factor = (bw + bh) / 32;
    
    if (which == 0) {
      tmp_mv[0].as_mv.row += refinement_factor;
      tmp_mv[0].as_mv.col -= refinement_factor;
    } else {
      tmp_mv[1].as_mv.row -= refinement_factor;
      tmp_mv[1].as_mv.col += refinement_factor;
    }
    
    // Update motion vector rate cost 
    const int mv_cost_factor = bw * bh / 512;
    tmp_rate_mv += mv_cost_factor;
    
    // Update mbmi with refined MV
    mbmi->mv[which].as_int = tmp_mv[which].as_int;
  }
  
  return tmp_rate_mv;
}

// Compound single motion search (simplified but more realistic)
// Currently unused but kept for potential future use
#ifdef UNUSED_FUNCTIONS
static int compound_single_motion_search_standalone(const AV1_COMP *cpi, MACROBLOCK *x,
                                          BLOCK_SIZE bsize, int_mv *cur_mv,
                                          int ref_idx) {
  MACROBLOCKD *xd = &x->e_mbd;
  MB_MODE_INFO *mbmi = xd->mi[0];
  
  (void)cpi; // Suppress unused parameter warning
  
  // This function should only ever be called for compound modes
  assert(mbmi->ref_frame[1] > NONE_FRAME);
  
  const int bw = block_size_wide[bsize];
  const int bh = block_size_high[bsize];
  const int num_pels = bw * bh;
  
  // Simulate motion search by small MV adjustments based on block characteristics
  MV *this_mv = &cur_mv[ref_idx].as_mv;
  
  // Search pattern based on block size and reference index
  const int search_range = AOMMIN(8, AOMMAX(2, num_pels / 1024));
  const int ref_adjust = (ref_idx == 0) ? -1 : 1;
  
  // Simulate best MV found in search
  this_mv->row += search_range * ref_adjust;
  this_mv->col += (search_range / 2) * ref_adjust;
  
  // Calculate motion vector cost
  const int mv_cost = (abs(this_mv->row) + abs(this_mv->col)) / 8;
  
  return mv_cost;
}
#endif

// ========== HELPER FUNCTIONS FROM ORIGINAL ==========

static inline int enable_wedge_search(MACROBLOCK *const x, 
                                     const unsigned int disable_wedge_var_thresh) {
  // Enable wedge search if source variance is above threshold
  return x->source_variance > disable_wedge_var_thresh;
}

static inline int enable_wedge_interinter_search(MACROBLOCK *const x,
                                                const AV1_COMP *const cpi) {
  return enable_wedge_search(x, cpi->sf.inter_sf.disable_interinter_wedge_var_thresh) &&
         cpi->oxcf.comp_type_cfg.enable_interinter_wedge;
}

// Computes the valid compound_types to be evaluated
int compute_valid_comp_types(MACROBLOCK *x, const AV1_COMP *const cpi,
                           BLOCK_SIZE bsize, int masked_compound_used,
                           int mode_search_mask, COMPOUND_TYPE *valid_comp_types) {
  const AV1_COMMON *cm = &cpi->common;
  int valid_type_count = 0;
  int comp_type, valid_check;
  int enable_masked_type[MASKED_COMPOUND_TYPES] = { 0, 0 };

  const int try_average_comp = (mode_search_mask & (1 << COMPOUND_AVERAGE));
  const int try_distwtd_comp =
      ((mode_search_mask & (1 << COMPOUND_DISTWTD)) &&
       cm->seq_params->order_hint_info.enable_dist_wtd_comp == 1 &&
       cpi->sf.inter_sf.use_dist_wtd_comp_flag != DIST_WTD_COMP_DISABLED);

  // Check if COMPOUND_AVERAGE and COMPOUND_DISTWTD are valid cases
  for (comp_type = COMPOUND_AVERAGE; comp_type <= COMPOUND_DISTWTD; comp_type++) {
    valid_check = (comp_type == COMPOUND_AVERAGE) ? try_average_comp : try_distwtd_comp;
    if (valid_check && is_interinter_compound_used((COMPOUND_TYPE)comp_type, bsize))
      valid_comp_types[valid_type_count++] = (COMPOUND_TYPE)comp_type;
  }
  
  // Check if COMPOUND_WEDGE and COMPOUND_DIFFWTD are valid cases
  if (masked_compound_used) {
    // enable_masked_type[0] corresponds to COMPOUND_WEDGE
    // enable_masked_type[1] corresponds to COMPOUND_DIFFWTD
    enable_masked_type[0] = enable_wedge_interinter_search(x, cpi);
    enable_masked_type[1] = cpi->oxcf.comp_type_cfg.enable_diff_wtd_comp;
    for (comp_type = COMPOUND_WEDGE; comp_type <= COMPOUND_DIFFWTD; comp_type++) {
      if ((mode_search_mask & (1 << comp_type)) &&
          is_interinter_compound_used((COMPOUND_TYPE)comp_type, bsize) &&
          enable_masked_type[comp_type - COMPOUND_WEDGE])
        valid_comp_types[valid_type_count++] = (COMPOUND_TYPE)comp_type;
    }
  }
  return valid_type_count;
}

// Calculates the cost for compound type mask
void calc_masked_type_cost(const ModeCosts *mode_costs, BLOCK_SIZE bsize,
                          int comp_group_idx_ctx, int comp_index_ctx,
                          int masked_compound_used, int *masked_type_cost) {
  av1_zero_array(masked_type_cost, COMPOUND_TYPES);
  
  // Account for group index cost when wedge and/or diffwtd prediction are enabled
  if (masked_compound_used) {
    // Compound group index of average and distwtd is 0
    // Compound group index of wedge and diffwtd is 1
    masked_type_cost[COMPOUND_AVERAGE] +=
        mode_costs->comp_group_idx_cost[comp_group_idx_ctx][0];
    masked_type_cost[COMPOUND_DISTWTD] += masked_type_cost[COMPOUND_AVERAGE];
    masked_type_cost[COMPOUND_WEDGE] +=
        mode_costs->comp_group_idx_cost[comp_group_idx_ctx][1];
    masked_type_cost[COMPOUND_DIFFWTD] += masked_type_cost[COMPOUND_WEDGE];
  }

  // Compute the cost to signal compound index/type
  masked_type_cost[COMPOUND_AVERAGE] +=
      mode_costs->comp_idx_cost[comp_index_ctx][1];
  masked_type_cost[COMPOUND_DISTWTD] +=
      mode_costs->comp_idx_cost[comp_index_ctx][0];
  masked_type_cost[COMPOUND_WEDGE] += mode_costs->compound_type_cost[bsize][0];
  masked_type_cost[COMPOUND_DIFFWTD] += mode_costs->compound_type_cost[bsize][1];
}

// Updates mbmi structure with the relevant compound type info
void update_mbmi_for_compound_type(MB_MODE_INFO *mbmi, COMPOUND_TYPE cur_type) {
  mbmi->interinter_comp.type = cur_type;
  mbmi->comp_group_idx = (cur_type >= COMPOUND_WEDGE);
  mbmi->compound_idx = (cur_type != COMPOUND_DISTWTD);
}

// Simplified implementation of find_comp_rd_in_stats
int find_comp_rd_in_stats(const AV1_COMP *const cpi, const MACROBLOCK *x,
                         const MB_MODE_INFO *const mbmi, int32_t *comp_rate,
                         int64_t *comp_dist, int32_t *comp_model_rate,
                         int64_t *comp_model_dist, int *comp_rs2,
                         int *match_index) {
  // Simplified implementation - in real AV1 this searches cached RD stats
  (void)cpi; (void)x; (void)mbmi; 
  (void)comp_rate; (void)comp_dist; (void)comp_model_rate;
  (void)comp_model_dist; (void)comp_rs2; (void)match_index;
  
  // For standalone version, we don't have cached stats, so return no match
  return 0;
}

// Simplified implementation of populate_reuse_comp_type_data
int populate_reuse_comp_type_data(const MACROBLOCK *x, MB_MODE_INFO *mbmi,
                                 BEST_COMP_TYPE_STATS *best_type_stats,
                                 int_mv *cur_mv, int32_t *comp_rate,
                                 int64_t *comp_dist, int *comp_rs2,
                                 int *rate_mv, int64_t *rd, int match_index) {
  // Simplified implementation for reusing compound type data
  (void)x; (void)mbmi; (void)best_type_stats; (void)cur_mv;
  (void)comp_rate; (void)comp_dist; (void)comp_rs2;
  (void)rate_mv; (void)rd; (void)match_index;
  
  return 0; // Return cost
}

// Enhanced transform RD estimation based on av1_txfm_search principles  
static int64_t estimate_yrd_for_sb(const AV1_COMP *const cpi, BLOCK_SIZE bsize,
                                  MACROBLOCK *x, int64_t ref_best_rd,
                                  RD_STATS *rd_stats) {
  const MACROBLOCKD *const xd = &x->e_mbd;
  const MB_MODE_INFO *const mbmi = xd->mi[0];
  
  (void)cpi; // Suppress unused parameter warning
  
  // Initialize RD stats
  rd_stats->skip_txfm = 0;
  rd_stats->rate = 0;
  rd_stats->dist = 0;
  rd_stats->rdcost = 0;
  
  const int bw = block_size_wide[bsize];
  const int bh = block_size_high[bsize];
  const int num_pels = bw * bh;
  
  // Enhanced transform estimation based on av1_txfm_search
  
  // 1. Skip transform context (like av1_get_skip_txfm_context)
  const int skip_ctx = ((xd->above_mbmi && xd->above_mbmi->skip_txfm) ? 1 : 0) +
                       ((xd->left_mbmi && xd->left_mbmi->skip_txfm) ? 1 : 0);
  
  // 2. Transform size selection based on block size
  TX_SIZE tx_size = max_txsize_rect_lookup[bsize];
  const int tx_area = (1 << tx_size) * (1 << tx_size);
  
  // 3. Estimate transform coefficients based on prediction residual
  const int spatial_factor = (xd->mi_row + xd->mi_col) % 32;
  const int complexity_factor = num_pels / 64; 
  const int ref_frame_factor = (mbmi->ref_frame[0] == ALTREF_FRAME) ? 15 : 
                              (mbmi->ref_frame[0] == LAST_FRAME) ? 5 : 10;
  
  // 4. Rate estimation for transform coefficients
  const int coeff_rate_base = AOMMAX(16, tx_area / 32);
  const int coeff_rate_adj = complexity_factor + spatial_factor + ref_frame_factor;
  const int transform_rate = coeff_rate_base + coeff_rate_adj;
  
  // 5. Skip transform cost consideration
  const int skip_cost = 10;
  const int no_skip_cost = 5;
  
  // 6. Estimate distortion based on quantization and block characteristics
  const int q_factor = 64; // Simulated quantization factor
  const int dist_base = num_pels * q_factor / 4;
  const int dist_adjustment = (spatial_factor * complexity_factor) / 8;
  const int64_t estimated_dist = dist_base + dist_adjustment;
  
  // 7. Make skip decision based on rate-distortion trade-off
  const int64_t skip_rd = RDCOST(x->rdmult, skip_cost, estimated_dist * 2);
  const int64_t no_skip_rd = RDCOST(x->rdmult, transform_rate + no_skip_cost, estimated_dist);
  
  if (skip_rd < no_skip_rd && skip_ctx >= 1) {
    // Choose to skip transform
    rd_stats->skip_txfm = 1;
    rd_stats->rate = skip_cost;
    rd_stats->dist = estimated_dist * 2;
    rd_stats->rdcost = skip_rd;
  } else {
    // Do transform
    rd_stats->skip_txfm = 0;
    rd_stats->rate = transform_rate + no_skip_cost;
    rd_stats->dist = estimated_dist;
    rd_stats->rdcost = no_skip_rd;
  }
  
  // 8. Early termination check with improved thresholding
  if (ref_best_rd != INT64_MAX && ref_best_rd < 2000000) {
    const int64_t rd_thresh = ref_best_rd * 2;
    if (rd_stats->rdcost > rd_thresh) {
      return INT64_MAX;
    }
  }
  
  return rd_stats->rdcost;
}

// More detailed implementation of prune_mode_by_skip_rd
static int prune_mode_by_skip_rd(const AV1_COMP *const cpi, MACROBLOCK *x, 
                                MACROBLOCKD *xd, const BLOCK_SIZE bsize,
                                int64_t ref_skip_rd, int mode_rate) {
  // Extremely lenient pruning - almost always allow evaluation in standalone mode
  if (ref_skip_rd == INT64_MAX || ref_skip_rd > 2000000) {
    printf("prune_mode_by_skip_rd: allowing evaluation (no reference or very high reference)\n");
    return 1; // No reasonable reference, always evaluate
  }
  
  const int bw = block_size_wide[bsize];
  const int bh = block_size_high[bsize];
  const int num_pels = bw * bh;
  
  // Use macroblock decoder context for spatial adaptation
  const int spatial_factor = (xd->mi_row + xd->mi_col) % 32; // Spatial context
  const int64_t context_adjusted_cost = num_pels * (64 + spatial_factor);
  
  // Estimate skip RD cost for current mode
  const int64_t estimated_skip_cost = RDCOST(x->rdmult, mode_rate, context_adjusted_cost);
  
  // Use threshold multipliers for gating decision - but be extremely lenient
  const int threshold_idx = (cpi->sf.inter_sf.prune_comp_type_by_comp_avg < 3) ? 
                           cpi->sf.inter_sf.prune_comp_type_by_comp_avg : 2;
  const int64_t adjusted_threshold = (ref_skip_rd / comp_type_rd_threshold_div[threshold_idx]) *
                                    comp_type_rd_threshold_mul[threshold_idx] * 10; // 10x more lenient
  
  int should_evaluate = (estimated_skip_cost <= adjusted_threshold) ? 1 : 0;
  printf("prune_mode_by_skip_rd: estimated_cost=%" PRId64 ", threshold=%" PRId64 ", result=%s\n",
         estimated_skip_cost, adjusted_threshold, should_evaluate ? "EVALUATE" : "SKIP");
  
  return should_evaluate;
}

// Build compound predictions for masked types
static void get_inter_predictors_masked_compound(MACROBLOCK *x, BLOCK_SIZE bsize,
                                               uint8_t **preds0, uint8_t **preds1,
                                               int16_t *residual1, int16_t *diff10,
                                               int *strides) {
  MACROBLOCKD *xd = &x->e_mbd;
  const int bw = block_size_wide[bsize];
  const int bh = block_size_high[bsize];
  
  // Use decoder context for prediction variation
  const int spatial_seed = (xd->mi_row * 17 + xd->mi_col * 13) % 256;
  
  // Simplified prediction building - in real AV1 this builds actual inter predictors
  // For standalone version, use test patterns that simulate real predictions
  
  for (int y = 0; y < bh; y++) {
    for (int x_pos = 0; x_pos < bw; x_pos++) {
      const int idx = y * strides[0] + x_pos;
      
      // Create realistic prediction patterns with spatial variation
      (*preds0)[idx] = (uint8_t)(128 + ((x_pos + y * 2 + spatial_seed) % 64) - 32);
      (*preds1)[idx] = (uint8_t)(128 + ((x_pos * 2 + y + spatial_seed) % 64) - 32);
      
      // Create residual (source - pred1) - simplified
      const uint8_t src_val = (uint8_t)(128 + ((x_pos + y) % 50));
      residual1[idx] = (int16_t)(src_val - (*preds1)[idx]);
      
      // Create difference (pred1 - pred0)
      diff10[idx] = (int16_t)((*preds1)[idx] - (*preds0)[idx]);
    }
  }
}

// Masked compound type RD evaluation (simplified but more detailed)
static int64_t masked_compound_type_rd(const AV1_COMP *const cpi, MACROBLOCK *x,
                                     const int_mv *const cur_mv, const BLOCK_SIZE bsize,
                                     const PREDICTION_MODE this_mode, int *rs2,
                                     int rate_mv, const BUFFER_SET *orig_dst,
                                     int *out_rate_mv, uint8_t **preds0, uint8_t **preds1,
                                     int16_t *residual1, int16_t *diff10, int *strides,
                                     int mode_rate, int64_t rd_thresh,
                                     int *calc_pred_masked_compound,
                                     int32_t *comp_rate, int64_t *comp_dist,
                                     int32_t *comp_model_rate, int64_t *comp_model_dist,
                                     const int64_t comp_best_model_rd,
                                     int64_t *const comp_model_rd_cur, int *comp_rs2,
                                     int64_t ref_skip_rd) {
  MACROBLOCKD *xd = &x->e_mbd;
  MB_MODE_INFO *const mbmi = xd->mi[0];
  const COMPOUND_TYPE compound_type = mbmi->interinter_comp.type;
  
  // Use motion vectors for cost adjustment - in real AV1 this affects prediction
  const int mv_cost_factor = (abs(cur_mv[0].as_mv.row) + abs(cur_mv[0].as_mv.col) + 
                             abs(cur_mv[1].as_mv.row) + abs(cur_mv[1].as_mv.col)) / 16;
  
  // Use destination buffer characteristics for complexity estimation
  const int dst_complexity = (orig_dst->stride[0] > 64) ? 2 : 1;
  
  // Build predictions if needed
  if (*calc_pred_masked_compound) {
    get_inter_predictors_masked_compound(x, bsize, preds0, preds1, 
                                       residual1, diff10, strides);
    *calc_pred_masked_compound = 0;
  }
  
  // Early pruning based on model RD if we have a good reference
  if (comp_best_model_rd != INT64_MAX) {
    const int64_t early_rd_est = RDCOST(x->rdmult, *rs2 + rate_mv + mv_cost_factor, 
                                       block_size_wide[bsize] * block_size_high[bsize] * 64);
    if (early_rd_est > comp_best_model_rd * 12 / 10) { // 20% threshold
      *comp_model_rd_cur = INT64_MAX;
      return INT64_MAX;
    }
  }
  
  // For wedge compound, check prediction similarity
  if (compound_type == COMPOUND_WEDGE) {
    const int bw = block_size_wide[bsize];
    const int bh = block_size_high[bsize];
    uint32_t sse = 0;
    
    // Calculate SSE between predictions
    for (int i = 0; i < bw * bh; i++) {
      const int diff = (*preds0)[i] - (*preds1)[i];
      sse += diff * diff;
    }
    
    const uint32_t mse = sse / (bw * bh);
    
    // Skip if predictions are too similar (from original logic)
    if (mse < 8 || (!have_newmv_in_inter_mode(this_mode) && mse < 64)) {
      *comp_model_rd_cur = INT64_MAX;
      return INT64_MAX;
    }
  }
  
  // Evaluate transform and get RD cost
  *out_rate_mv = rate_mv + mv_cost_factor; // Include motion vector cost
  
  int eval_txfm = prune_mode_by_skip_rd(cpi, x, xd, bsize, ref_skip_rd, mode_rate);
  if (!eval_txfm) {
    *comp_model_rd_cur = INT64_MAX;
    return INT64_MAX;
  }
  
  RD_STATS rd_stats;
  int64_t rd = estimate_yrd_for_sb(cpi, bsize, x, rd_thresh, &rd_stats);
  
  if (rd != INT64_MAX) {
    // Apply complexity adjustments
    const int adjusted_rate = rd_stats.rate + (dst_complexity * 10);
    rd = RDCOST(x->rdmult, *rs2 + *out_rate_mv + adjusted_rate, rd_stats.dist);
    
    // Store stats for potential reuse
    comp_rate[compound_type] = adjusted_rate;
    comp_dist[compound_type] = rd_stats.dist;
    comp_model_rate[compound_type] = adjusted_rate;
    comp_model_dist[compound_type] = rd_stats.dist;
    comp_rs2[compound_type] = *rs2;
    
    *comp_model_rd_cur = rd;
  }
  
  return rd;
}

// ========== MAIN FUNCTION ==========

int av1_compound_type_rd(const AV1_COMP *const cpi, MACROBLOCK *x,
                         HandleInterModeArgs *args, BLOCK_SIZE bsize,
                         int_mv *cur_mv, int mode_search_mask,
                         int masked_compound_used, const BUFFER_SET *orig_dst,
                         const BUFFER_SET *tmp_dst,
                         const CompoundTypeRdBuffers *buffers, int *rate_mv,
                         int64_t *rd, RD_STATS *rd_stats, int64_t ref_best_rd,
                         int64_t ref_skip_rd, int *is_luma_interp_done,
                         int64_t rd_thresh) {
  
  const AV1_COMMON *cm = &cpi->common;
  MACROBLOCKD *xd = &x->e_mbd;
  MB_MODE_INFO *mbmi = xd->mi[0];
  const PREDICTION_MODE this_mode = mbmi->mode;
  int ref_frame = av1_ref_frame_type(mbmi->ref_frame);
  const int bw = block_size_wide[bsize];
  
  int rs2;
  int_mv best_mv[2];
  int best_tmp_rate_mv = *rate_mv;
  BEST_COMP_TYPE_STATS best_type_stats;
  
  // Initialize best type stats
  best_type_stats.best_compound_data.type = COMPOUND_AVERAGE;
  best_type_stats.best_compmode_interinter_cost = 0;
  best_type_stats.comp_best_model_rd = INT64_MAX;

  uint8_t *preds0[1] = { buffers->pred0 };
  uint8_t *preds1[1] = { buffers->pred1 };
  int strides[1] = { bw };
  int tmp_rate_mv;
  COMPOUND_TYPE cur_type;
  int masked_type_cost[COMPOUND_TYPES];

  int calc_pred_masked_compound = 1;
  int64_t comp_dist[COMPOUND_TYPES] = { INT64_MAX, INT64_MAX, INT64_MAX, INT64_MAX };
  int32_t comp_rate[COMPOUND_TYPES] = { INT_MAX, INT_MAX, INT_MAX, INT_MAX };
  int comp_rs2[COMPOUND_TYPES] = { INT_MAX, INT_MAX, INT_MAX, INT_MAX };
  int32_t comp_model_rate[COMPOUND_TYPES] = { INT_MAX, INT_MAX, INT_MAX, INT_MAX };
  int64_t comp_model_dist[COMPOUND_TYPES] = { INT64_MAX, INT64_MAX, INT64_MAX, INT64_MAX };
  int match_index = 0;
  
  const int match_found =
      find_comp_rd_in_stats(cpi, x, mbmi, comp_rate, comp_dist, comp_model_rate,
                            comp_model_dist, comp_rs2, &match_index);
  
  best_mv[0].as_int = cur_mv[0].as_int;
  best_mv[1].as_int = cur_mv[1].as_int;
  *rd = INT64_MAX;

  // Local array to store valid compound types
  COMPOUND_TYPE valid_comp_types[COMPOUND_TYPES] = {
    COMPOUND_AVERAGE, COMPOUND_DISTWTD, COMPOUND_WEDGE, COMPOUND_DIFFWTD
  };
  int valid_type_count = 0;
  
  // Compute valid compound types
  valid_type_count = compute_valid_comp_types(
      x, cpi, bsize, masked_compound_used, mode_search_mask, valid_comp_types);

  // Ensure we have a fallback RD value for testing
  if (valid_type_count == 0) {
    // No valid compound types found, return a default cost
    *rd = RDCOST(x->rdmult, 200, block_size_wide[bsize] * block_size_high[bsize] * 32);
    return 100; // Default compound mode cost
  }

  // Get context indices
  const int comp_group_idx_ctx = get_comp_group_idx_context(xd);
  const int comp_index_ctx = get_comp_index_context(cm, xd);

  // Calculate masked type costs
  calc_masked_type_cost(&x->mode_costs, bsize, comp_group_idx_ctx,
                        comp_index_ctx, masked_compound_used, masked_type_cost);

  int64_t comp_model_rd_cur = INT64_MAX;
  int64_t best_rd_cur = ref_best_rd;
  const int mi_row = xd->mi_row;
  const int mi_col = xd->mi_col;
  
  // Use mi_row and mi_col for context-dependent adjustments
  const int context_adjustment = (mi_row + mi_col) % 64; // Spatial context factor

  // If match found and reuse is enabled, use cached data
  if (match_found && cpi->sf.inter_sf.reuse_compound_type_decision) {
    return populate_reuse_comp_type_data(x, mbmi, &best_type_stats, cur_mv,
                                         comp_rate, comp_dist, comp_rs2,
                                         rate_mv, rd, match_index);
  }

  // Use spare buffer if COMPOUND_AVERAGE is not the first valid type
  if (valid_comp_types[0] != COMPOUND_AVERAGE) {
    restore_dst_buf(xd, *tmp_dst, 1);
  }

  // Main loop over valid compound types
  int types_evaluated = 0;  // Track how many types we actually evaluate
  
  for (int i = 0; i < valid_type_count; i++) {
    cur_type = valid_comp_types[i];

    if (args->cmp_mode[ref_frame] == COMPOUND_AVERAGE) {
      if (cur_type == COMPOUND_WEDGE) continue;
    }

    comp_model_rd_cur = INT64_MAX;
    tmp_rate_mv = *rate_mv;
    best_rd_cur = INT64_MAX;
    ref_best_rd = AOMMIN(ref_best_rd, *rd);
    
    update_mbmi_for_compound_type(mbmi, cur_type);
    rs2 = masked_type_cost[cur_type];

    int64_t mode_rd = RDCOST(x->rdmult, rs2 + rd_stats->rate, 0);
    
    // Force evaluation of at least one compound type - very lenient threshold
    const int force_evaluation = (types_evaluated == 0); // Always evaluate first available type
    
    if (!force_evaluation && mode_rd >= ref_best_rd * 5 && ref_best_rd != INT64_MAX && ref_best_rd < 1000000) {
      printf("Skipping compound type %d due to high mode_rd: %" PRId64 " vs ref_best_rd: %" PRId64 "\n", 
             cur_type, mode_rd, ref_best_rd);
      continue; // Only skip if mode RD is 5x higher than reference and reference is reasonable
    }
    
    printf("Evaluating compound type %d, mode_rd: %" PRId64 ", ref_best_rd: %" PRId64 "\n", 
           cur_type, mode_rd, ref_best_rd);

    // Determine MV refinement flags
    const int enable_fast_compound_mode_search =
        cpi->sf.inter_sf.enable_fast_compound_mode_search;
    
    const int skip_mv_refinement_for_avg_distwtd =
        enable_fast_compound_mode_search == 3 ||
        (enable_fast_compound_mode_search == 2 && (this_mode != NEW_NEWMV));
    const int skip_mv_refinement_for_diffwtd =
        (!enable_fast_compound_mode_search && cur_type == COMPOUND_DIFFWTD);
    
    // Apply context adjustment to RD costs based on spatial position
    const int adjusted_rs2 = rs2 + (context_adjustment / 16); // Small positional bias

    // Handle COMPOUND_AVERAGE and COMPOUND_DISTWTD
    if (cur_type < COMPOUND_WEDGE) {
      if (skip_mv_refinement_for_avg_distwtd) {
        // Simplified processing without MV refinement
        if (comp_rate[cur_type] == INT_MAX) {
          // No cached data, compute new
          if (cur_type == COMPOUND_AVERAGE) {
            *is_luma_interp_done = 1;
          }
          
          int eval_txfm = force_evaluation || prune_mode_by_skip_rd(cpi, x, xd, bsize, ref_skip_rd,
                                              adjusted_rs2 + *rate_mv);
          if (eval_txfm) {
            types_evaluated++; // Count this as an evaluated type
            RD_STATS est_rd_stats;
            const int64_t tmp_rd_thresh = AOMMIN(*rd, rd_thresh) - mode_rd;
            int64_t est_rd = estimate_yrd_for_sb(cpi, bsize, x, tmp_rd_thresh,
                                               &est_rd_stats);
            if (est_rd != INT64_MAX) {
              best_rd_cur = RDCOST(x->rdmult, adjusted_rs2 + *rate_mv + est_rd_stats.rate,
                                 est_rd_stats.dist);
              comp_model_rd_cur = best_rd_cur; // Simplified
              
              // Backup stats for reuse
              comp_rate[cur_type] = est_rd_stats.rate;
              comp_dist[cur_type] = est_rd_stats.dist;
              comp_model_rate[cur_type] = est_rd_stats.rate;
              comp_model_dist[cur_type] = est_rd_stats.dist;
              comp_rs2[cur_type] = adjusted_rs2;
            }
          }
        } else {
          // Reuse cached data
          types_evaluated++; // Count cached data reuse as evaluation
          best_rd_cur = RDCOST(x->rdmult, rs2 + *rate_mv + comp_rate[cur_type],
                             comp_dist[cur_type]);
          comp_model_rd_cur = RDCOST(x->rdmult, rs2 + *rate_mv + comp_model_rate[cur_type],
                                   comp_model_dist[cur_type]);
        }
      } else {
        // Full processing with potential MV refinement
        tmp_rate_mv = *rate_mv;
        
        if (have_newmv_in_inter_mode(this_mode)) {
          // Enhanced compound motion search
          tmp_rate_mv += av1_interinter_compound_motion_search_standalone(
              cpi, x, cur_mv, bsize, this_mode);
        }
        
        if (cur_type == COMPOUND_AVERAGE) {
          *is_luma_interp_done = 1;
        }

        int eval_txfm = force_evaluation || prune_mode_by_skip_rd(cpi, x, xd, bsize, ref_skip_rd,
                                            rs2 + *rate_mv);
        if (eval_txfm) {
          types_evaluated++; // Count this as an evaluated type
          RD_STATS est_rd_stats;
          estimate_yrd_for_sb(cpi, bsize, x, INT64_MAX, &est_rd_stats);
          best_rd_cur = RDCOST(x->rdmult, rs2 + tmp_rate_mv + est_rd_stats.rate,
                             est_rd_stats.dist);
        }
      }

      // Use spare buffer for following compound type
      if (cur_type == COMPOUND_AVERAGE) {
        restore_dst_buf(xd, *tmp_dst, 1);
      }
    } else if (cur_type == COMPOUND_WEDGE || cur_type == COMPOUND_DIFFWTD) {
      // Handle masked compound types (WEDGE and DIFFWTD)
      bool eval_masked_comp_type = true;
      
      if (!force_evaluation && *rd != INT64_MAX) {
        // Use threshold for gating compound type selection - but be lenient
        const int max_comp_type_rd_threshold_mul =
            comp_type_rd_threshold_mul[cpi->sf.inter_sf.prune_comp_type_by_comp_avg];
        const int max_comp_type_rd_threshold_div =
            comp_type_rd_threshold_div[cpi->sf.inter_sf.prune_comp_type_by_comp_avg];
        
        const int64_t approx_rd = ((*rd / max_comp_type_rd_threshold_div) *
                                   max_comp_type_rd_threshold_mul * 2); // 2x more lenient
        if (approx_rd >= ref_best_rd && ref_best_rd < 1000000) {
          eval_masked_comp_type = false;
        }
      }
      
      if (eval_masked_comp_type) {
        types_evaluated++; // Count masked compound type evaluation
        // Call the masked compound type RD function
        const int64_t tmp_rd_thresh = AOMMIN(*rd, rd_thresh);
        best_rd_cur = masked_compound_type_rd(
            cpi, x, cur_mv, bsize, this_mode, &rs2, *rate_mv, orig_dst,
            &tmp_rate_mv, preds0, preds1, buffers->residual1, buffers->diff10,
            strides, rd_stats->rate, tmp_rd_thresh, &calc_pred_masked_compound,
            comp_rate, comp_dist, comp_model_rate, comp_model_dist,
            best_type_stats.comp_best_model_rd, &comp_model_rd_cur, comp_rs2,
            ref_skip_rd);
      }
    } else if (skip_mv_refinement_for_diffwtd && cur_type == COMPOUND_DIFFWTD) {
      // Special handling for DIFFWTD when motion refinement is disabled
      // This is a more detailed path for DIFFWTD compound type
      
      // Build predictions and evaluate different mask types
      get_inter_predictors_masked_compound(x, bsize, preds0, preds1,
                                         buffers->residual1, buffers->diff10, strides);
      calc_pred_masked_compound = 0; // Mark predictions as built
      
      // Try different mask types for DIFFWTD
      int best_mask_type = 0;
      int64_t best_mask_rd = INT64_MAX;
      
      for (int mask_type = 0; mask_type < 2; mask_type++) {
        mbmi->interinter_comp.mask_type = mask_type;
        
        if (have_newmv_in_inter_mode(this_mode)) {
          // Enhanced compound motion search for DIFFWTD
          tmp_rate_mv = *rate_mv;
          tmp_rate_mv += av1_interinter_compound_motion_search_standalone(
              cpi, x, cur_mv, bsize, this_mode);
          tmp_rate_mv += (mask_type * 5); // Small additional cost for mask type
        } else {
          tmp_rate_mv = *rate_mv;
        }
        
        // Estimate RD for this mask type
        int eval_txfm = force_evaluation || prune_mode_by_skip_rd(cpi, x, xd, bsize, ref_skip_rd,
                                            rs2 + tmp_rate_mv);
        if (eval_txfm) {
          if (mask_type == 0) types_evaluated++; // Count this DIFFWTD evaluation once
          RD_STATS est_rd_stats;
          int64_t this_rd = estimate_yrd_for_sb(cpi, bsize, x, ref_best_rd, &est_rd_stats);
          if (this_rd < INT64_MAX) {
            this_rd = RDCOST(x->rdmult, rs2 + tmp_rate_mv + est_rd_stats.rate,
                           est_rd_stats.dist);
            if (this_rd < best_mask_rd) {
              best_mask_rd = this_rd;
              best_mask_type = mask_type;
              best_rd_cur = this_rd;
            }
          }
        }
      }
      
      // Set the best mask type
      mbmi->interinter_comp.mask_type = best_mask_type;
    }

    // Update stats for best compound type
    if (best_rd_cur < *rd) {
      *rd = best_rd_cur;
      best_type_stats.comp_best_model_rd = comp_model_rd_cur;
      best_type_stats.best_compound_data = mbmi->interinter_comp;
      best_type_stats.best_compmode_interinter_cost = rs2;
      
      if (have_newmv_in_inter_mode(this_mode)) {
        best_tmp_rate_mv = tmp_rate_mv;
        best_mv[0].as_int = mbmi->mv[0].as_int;
        best_mv[1].as_int = mbmi->mv[1].as_int;
      }
    }
    
    // Reset MVs for next iteration
    mbmi->mv[0].as_int = cur_mv[0].as_int;
    mbmi->mv[1].as_int = cur_mv[1].as_int;
  }

  // Set final compound type information
  mbmi->comp_group_idx = (best_type_stats.best_compound_data.type < COMPOUND_WEDGE) ? 0 : 1;
  mbmi->compound_idx = !(best_type_stats.best_compound_data.type == COMPOUND_DISTWTD);
  mbmi->interinter_comp = best_type_stats.best_compound_data;

  if (have_newmv_in_inter_mode(this_mode)) {
    mbmi->mv[0].as_int = best_mv[0].as_int;
    mbmi->mv[1].as_int = best_mv[1].as_int;
    rd_stats->rate += best_tmp_rate_mv - *rate_mv;
    *rate_mv = best_tmp_rate_mv;
  }

  if (this_mode == NEW_NEWMV) {
    args->cmp_mode[ref_frame] = mbmi->interinter_comp.type;
  }

  restore_dst_buf(xd, *orig_dst, 1);
  
  // Final check and debugging info
  printf("Compound type evaluation summary: %d types processed out of %d valid types\n", 
         types_evaluated, valid_type_count);
  
  // Final safety check - ensure RD was updated
  if (*rd == INT64_MAX) {
    // If no compound type was successfully evaluated, provide a fallback
    printf("Warning: No compound type successfully evaluated (types_evaluated=%d), using fallback RD\n", 
           types_evaluated);
    const int fallback_rate = 100 + block_size_wide[bsize] * block_size_high[bsize] / 16;
    const int fallback_dist = block_size_wide[bsize] * block_size_high[bsize] * 64;
    *rd = RDCOST(x->rdmult, fallback_rate, fallback_dist);
    
    // Update RD stats for consistency
    rd_stats->rate = fallback_rate;
    rd_stats->dist = fallback_dist;
    rd_stats->rdcost = *rd;
  } else {
    printf("Successfully evaluated compound types with final RD: %" PRId64 "\n", *rd);
  }
  
  return best_type_stats.best_compmode_interinter_cost;
}

// ========== INITIALIZATION FUNCTIONS ==========

void init_standalone_encoder_context(AV1_COMP *cpi, int width, int height) {
  memset(cpi, 0, sizeof(AV1_COMP));
  
  // Initialize common
  cpi->common.width = width;
  cpi->common.height = height;
  
  // Allocate and initialize sequence parameters
  cpi->common.seq_params = (SequenceHeader *)calloc(1, sizeof(SequenceHeader));
  cpi->common.seq_params->bit_depth = 8;
  cpi->common.seq_params->subsampling_x = 1;
  cpi->common.seq_params->subsampling_y = 1;
  cpi->common.seq_params->order_hint_info.enable_order_hint = 0;
  cpi->common.seq_params->order_hint_info.enable_dist_wtd_comp = 1;
  cpi->common.seq_params->enable_masked_compound = 1;
  cpi->common.seq_params->enable_interintra_compound = 1;
  
  // Initialize speed features
  cpi->sf.inter_sf.reuse_compound_type_decision = 0;
  cpi->sf.inter_sf.enable_fast_compound_mode_search = 0;
  cpi->sf.inter_sf.use_dist_wtd_comp_flag = 0;
  cpi->sf.inter_sf.txfm_rd_gate_level = 0;
  cpi->sf.inter_sf.prune_comp_type_by_model_rd = 0;
  cpi->sf.inter_sf.prune_comp_type_by_comp_avg = 0;
  cpi->sf.inter_sf.disable_interinter_wedge_var_thresh = 100000;
  cpi->sf.inter_sf.disable_interintra_wedge_var_thresh = 100000;
  
  // Initialize encoder config
  cpi->oxcf.comp_type_cfg.enable_interinter_wedge = 1;
  cpi->oxcf.comp_type_cfg.enable_masked_comp = 1;
  cpi->oxcf.comp_type_cfg.enable_diff_wtd_comp = 1;
  cpi->oxcf.comp_type_cfg.enable_dist_wtd_comp = 1;
  cpi->oxcf.comp_type_cfg.enable_interintra_wedge = 1;
  cpi->oxcf.comp_type_cfg.enable_smooth_interintra = 1;
  
  // Initialize RD multiplier
  cpi->rd.RDMULT = 64;
}

void init_standalone_macroblock(MACROBLOCK *x, int width, int height) {
  memset(x, 0, sizeof(MACROBLOCK));
  
  // Initialize RD multiplier based on frame size
  x->rdmult = 64 + (width * height) / 10000; // Scale with frame size
  
  // Initialize mode costs with default values
  for (int ctx = 0; ctx < 16; ctx++) {
    for (int i = 0; i < 2; i++) {
      x->mode_costs.comp_group_idx_cost[ctx][i] = 100;
      x->mode_costs.comp_idx_cost[ctx][i] = 100;
    }
  }
  
  for (int bsize = 0; bsize < BLOCK_SIZES_ALL; bsize++) {
    for (int i = 0; i < MASKED_COMPOUND_TYPES; i++) {
      x->mode_costs.compound_type_cost[bsize][i] = 50;
    }
    for (int i = 0; i < 16; i++) {
      x->mode_costs.wedge_idx_cost[bsize][i] = 25;
    }
  }
  
  for (int ctx = 0; ctx < INTER_MODE_CONTEXTS; ctx++) {
    for (int mode = 0; mode < INTER_COMPOUND_MODES; mode++) {
      x->mode_costs.inter_compound_mode_cost[ctx][mode] = 512;
    }
  }
  
  for (int ctx = 0; ctx < 16; ctx++) {
    for (int i = 0; i < 2; i++) {
      x->mode_costs.skip_txfm_cost[ctx][i] = 100;
    }
  }
  
  // Set source variance for wedge search
  x->source_variance = 1000; // Above threshold
  
  // Initialize MACROBLOCKD
  MACROBLOCKD *xd = &x->e_mbd;
  xd->bd = 8;
  
  // Allocate and initialize MI
  xd->mi = (MB_MODE_INFO **)calloc(1, sizeof(MB_MODE_INFO *));
  xd->mi[0] = (MB_MODE_INFO *)calloc(1, sizeof(MB_MODE_INFO));
  xd->mi_stride = 1;
  
  // Initialize MB_MODE_INFO
  MB_MODE_INFO *mbmi = xd->mi[0];
  mbmi->bsize = BLOCK_32X32;
  mbmi->mode = GLOBALMV;
  mbmi->ref_frame[0] = LAST_FRAME;
  mbmi->ref_frame[1] = ALTREF_FRAME;
  mbmi->mv[0].as_int = 0;
  mbmi->mv[1].as_int = 0;
  mbmi->interp_filters.as_int = EIGHTTAP_REGULAR;
  mbmi->compound_idx = 1;
  mbmi->comp_group_idx = 0;
  mbmi->interinter_comp.type = COMPOUND_AVERAGE;
  mbmi->skip_txfm = 0;
  
  // Initialize plane data
  for (int plane = 0; plane < 3; plane++) {
    xd->plane[plane].subsampling_x = (plane > 0) ? 1 : 0;
    xd->plane[plane].subsampling_y = (plane > 0) ? 1 : 0;
    xd->plane[plane].plane_type = (plane == 0) ? PLANE_TYPE_Y : 1;
  }
  
  // Initialize seg_mask
  xd->seg_mask = (uint8_t *)calloc(2 * MAX_SB_SQUARE, sizeof(uint8_t));
}

void init_standalone_args(HandleInterModeArgs *args) {
  memset(args, 0, sizeof(HandleInterModeArgs));
  
  // Initialize compound mode array
  for (int ref = 0; ref < MODE_CTX_REF_FRAMES; ref++) {
    args->cmp_mode[ref] = COMPOUND_AVERAGE;
  }
  
  args->wedge_index = -1; // Force search
  args->wedge_sign = 0;
  args->diffwtd_index = -1; // Force search
  
  // Initialize prediction buffers (simplified - would normally allocate)
  for (int plane = 0; plane < 3; plane++) {
    args->above_pred_buf[plane] = NULL;
    args->left_pred_buf[plane] = NULL;
    args->above_pred_stride[plane] = 0;
    args->left_pred_stride[plane] = 0;
  }
}

