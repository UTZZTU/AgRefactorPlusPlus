#include <stdint.h>
#include <string.h>

typedef struct { unsigned long long x, y; } mm128_t;

static const char LogTable256[256] = {((char )(- 1)), ((char )0), ((char )1), ((char )1), ((char )2), ((char )2), ((char )2), ((char )2), ((char )3), ((char )3), ((char )3), ((char )3), ((char )3), ((char )3), ((char )3), ((char )3), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )4), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )5), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )6), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7), ((char )7)
#define LT(n) n, n, n, n, n, n, n, n, n, n, n, n, n, n, n, n
};

static int ilog2_32(unsigned int v)
{
  unsigned int t;
  unsigned int tt;
  if (tt = v >> 16) 
    return (t = tt >> 8)?24 + ((int )LogTable256[t]) : 16 + ((int )LogTable256[tt]);
  return (t = v >> 8)?8 + ((int )LogTable256[t]) : ((int )LogTable256[v]);
}

void top(int max_dist_x,int max_dist_y,int bw,int max_skip,int min_cnt,int min_sc,int is_cdna,int n_segs,long n,const mm128_t a_in[128],int n_u_out[1],unsigned long u_out[128],mm128_t b_out[128])
{
// Local fixed-size buffers
  int f[128];
  int p[128];
  int t[128];
  int v[128];
  unsigned long u[128];
  unsigned long u2[128];
  mm128_t a[128];
  mm128_t b[128];
  mm128_t w[128];
// Safety: if n is zero or negative, return empty outputs
  if (n <= ((long )0)) {
    n_u_out[0] = 0;
    return ;
  }
// Bound n to TB_MAX_ANCHORS
  int nn = n > ((long )128)?128 : ((int )n);
// Copy input anchors into local array a
  for (int i = 0; i < 128; ++i) {
    if (i < nn) {
      a[i] = a_in[i];
    }
     else {
      a[i] . x = ((unsigned long )0);
      a[i] . y = ((unsigned long )0);
    }
  }
// Initialize arrays
  for (int i = 0; i < 128; ++i) {
    f[i] = 0;
    p[i] = - 1;
    t[i] = 0;
    v[i] = 0;
    u[i] = ((unsigned long )0);
    u2[i] = ((unsigned long )0);
    b[i] . x = b[i] . y = ((unsigned long )0);
    w[i] . x = w[i] . y = ((unsigned long )0);
  }
// Compute avg_qspan
  unsigned long sum_qspan = (unsigned long )0;

  for (int i = 0; i < nn; ++i) {
    
#pragma HLS loop_tripcount min=128 max=128
    unsigned int qspan = (unsigned int )(a[i] . y >> 32 & ((unsigned long )0xff));
    sum_qspan += ((unsigned long )qspan);
  }
  float avg_qspan = nn > 0?((float )sum_qspan) / ((float )nn) : 0.0f;
// Main DP loop: fill f, p, v
  int st = 0;
  for (int ii = 0; ii < 128; ++ii) {
    if (ii >= nn) {
      break; 
    }
    unsigned long ri = a[ii] . x;
    long max_j = 0;
    int qi = (int )a[ii] . y;
    int q_span = (int )(a[ii] . y >> 32 & ((unsigned long )0xff));
    int max_f = q_span;
    while(st < ii && ri > a[st] . x + ((unsigned long )max_dist_x)){
      
#pragma HLS loop_tripcount min=2 max=2
      ++st;
    }
    int h = 65;
    for (int jjj = 1; jjj < 65; ++jjj) {
      if (ii - jjj < st) {
        break; 
      }
      unsigned long dr = ri - a[ii - jjj] . x;
      int dq = qi - ((int )a[ii - jjj] . y);
      int dd;
      int sc;
      int log_dd;
      if (dr == ((unsigned long )0) || dq <= 0) {
        continue; 
      }
      if (dq > max_dist_y || dq > max_dist_x) {
        continue; 
      }
      if (dr > ((unsigned long )dq)) {
        dd = ((int )(dr - ((unsigned long )dq)));
      }
       else {
        dd = dq - ((int )dr);
      }
      if (dd > bw) {
        continue; 
      }
      if (n_segs > 1 && dr > ((unsigned long )max_dist_y)) {
        continue; 
      }
      int min_d = dq < ((int )dr)?dq : ((int )dr);
      sc = (min_d > q_span?q_span : ((dq < ((int )dr)?dq : ((int )dr))));
      log_dd = (dd?ilog2_32((unsigned int )dd) : 0);
{
        int c_lin = (int )(((float )dd) * .01f * avg_qspan);
        int c_log = log_dd;
        sc -= (c_lin < c_log?c_lin : c_log);
      }
      sc += f[ii - jjj];
      if (sc > max_f) {
        max_f = sc;
        max_j = ((long )(ii - jjj));
      }
    }
    f[ii] = max_f;
    p[ii] = ((int )max_j);
    v[ii] = (max_j >= ((long )0) && v[max_j] > max_f?v[max_j] : max_f);
  }
// find ending positions of chains
  for (int i = 0; i < 128; ++i) {
    if (i >= nn) {
      break; 
    }
    t[i] = 0;
  }
  for (int i = 0; i < 128; ++i) {
    if (i >= nn) {
      break; 
    }
    if (p[i] >= 0 && p[i] < 128) {
      t[p[i]] = 1;
    }
  }
  int n_u = 0;
  for (int i = 0; i < 128; ++i) {
    if (i >= nn) {
      break; 
    }
    if (t[i] == 0 && v[i] >= min_sc) {
      ++n_u;
    }
  }
  if (n_u == 0) {
    n_u_out[0] = 0;
    return ;
  }
// collect chain ends into u as (score<<32) | index
  int uu = 0;
  for (int i = 0; i < 128; ++i) {
    if (i >= nn) {
      break; 
    }
    if (t[i] == 0 && v[i] >= min_sc) {
      int j = i;
      while(j >= 0 && f[j] < v[j]){
        
#pragma HLS loop_tripcount min=128 max=128
        int tmp = p[j];
        if (tmp == j) {
          break; 
        }
        j = tmp;
      }
      if (j < 0) {
        j = i;
      }
      u[uu++] = ((unsigned long )f[j]) << 32 | ((unsigned long )((unsigned int )j));
    }
  }
  int cur_nu = uu;
// Sort u[0..cur_nu-1] descending by full 64-bit value (score then index)
// Simple insertion sort (descending)
  for (int i = 1; i < 128; ++i) {
    if (i >= cur_nu) {
      break; 
    }
    unsigned long key = u[i];
    int j = i - 1;
    while(j >= 0 && u[j] < key){
      
#pragma HLS loop_tripcount min=128 max=128
      u[j + 1] = u[j];
      --j;
    }
    u[j + 1] = key;
  }
// Backtrack to generate chains; produce u entries as (score<<32)|chain_len
  for (int i = 0; i < 128; ++i) {
    if (i >= nn) {
      break; 
    }
    t[i] = 0;
  }
  int n_v = 0;
  int k = 0;
  for (int i = 0; i < 128; ++i) {
    if (i >= cur_nu) {
      break; 
    }
    int n_v0 = n_v;
    int k0 = k;
    int j = (int )(u[i] & ((unsigned long )0xffffffffu));
    while(j >= 0 && j < 128 && t[j] == 0){
      
#pragma HLS loop_tripcount min=128 max=128
      v[n_v++] = j;
      t[j] = 1;
      j = p[j];
    }
    if (j < 0) {
      if (n_v - n_v0 >= min_cnt) {
        unsigned long score = u[i] >> 32;
        u[k++] = score << 32 | ((unsigned long )((unsigned int )(n_v - n_v0)));
      }
    }
     else {
      unsigned long score_i = u[i] >> 32;
      int diff = ((int )score_i) - f[j];
      if (diff >= min_sc) {
        if (n_v - n_v0 >= min_cnt) {
          u[k++] = ((unsigned long )diff) << 32 | ((unsigned long )((unsigned int )(n_v - n_v0)));
        }
      }
    }
    if (k0 == k) {
// discard collected nodes
      n_v = n_v0;
    }
  }
  int final_n_u = k;
  n_u_out[0] = final_n_u;
// free f,p,t equivalent by leaving them; now create b array from v and u
// b will hold concatenated chains in the current order (u[0..final_n_u-1])
  int kout = 0;
  for (int i = 0; i < 128; ++i) {
    if (i >= final_n_u) {
      break; 
    }
    unsigned int ni = (unsigned int )(u[i] & ((unsigned long )0xffffffffu));
    int k0 = kout;
    for (unsigned int jj = (unsigned int )0; jj < ni; ++jj) {
      
#pragma HLS loop_tripcount min=10 max=10
      int idx = v[((unsigned int )k0) + (ni - jj - ((unsigned int )1))];
      if (idx >= 0 && idx < 128) {
        b[kout] = a[idx];
      }
       else {
        b[kout] . x = ((unsigned long )0);
        b[kout] . y = ((unsigned long )0);
      }
      ++kout;
    }
  }
  int total_b = kout;
// Now reorder chains by first anchor x ascending
// Build w: w[i].x = b[k0].x (first anchor of chain), w[i].y = ((uint64_t)k0 << 32) | i
  int kptr = 0;
  for (int i = 0; i < final_n_u; ++i) {
    
#pragma HLS loop_tripcount min=128 max=128
    unsigned int ni = (unsigned int )(u[i] & ((unsigned long )0xffffffffu));
    unsigned long first_x = (unsigned long )0;
    if (ni > ((unsigned int )0)) {
      if (kptr >= 0 && kptr < 128) {
        first_x = b[kptr] . x;
      }
    }
    w[i] . x = first_x;
    w[i] . y = ((unsigned long )kptr) << 32 | ((unsigned long )((unsigned int )i));
    kptr += ni;
  }
// Sort w[0..final_n_u-1] ascending by x using insertion sort
  for (int i = 1; i < final_n_u; ++i) {
    
#pragma HLS loop_tripcount min=128 max=128
    mm128_t key = w[i];
    int j = i - 1;
    while(j >= 0 && w[j] . x > key . x){
      
#pragma HLS LOOP_TRIPCOUNT MAX=64 MIN=64
      w[j + 1] = w[j];
      --j;
    }
    w[j + 1] = key;
  }
// Reorder u into u2 and reorder chains into a temporary area then copy into b_out
  int kout2 = 0;
  for (int i = 0; i < final_n_u; ++i) {
    
#pragma HLS loop_tripcount min=128 max=128
// original chain index
    unsigned int j = (unsigned int )(w[i] . y & ((unsigned long )0xffffffffu));
    unsigned int kstart = (unsigned int )(w[i] . y >> 32);
    unsigned int nchain = (unsigned int )(u[j] & ((unsigned long )0xffffffffu));
    u2[i] = u[j];
// copy chain from b[kstart .. kstart+nchain-1] into a[kout2 ..]
    for (unsigned int jj = (unsigned int )0; jj < nchain; ++jj) {
      
#pragma HLS loop_tripcount min=10 max=10
      if (kstart + jj < ((unsigned int )128)) {
        a[((unsigned int )kout2) + jj] = b[kstart + jj];
      }
       else {
        a[((unsigned int )kout2) + jj] . x = ((unsigned long )0);
        a[((unsigned int )kout2) + jj] . y = ((unsigned long )0);
      }
    }
    kout2 += nchain;
  }
// copy u2 back to u_out and b_out
  for (int i = 0; i < final_n_u; ++i) {
    
#pragma HLS loop_tripcount min=128 max=128
    if (i < 128) {
      u_out[i] = u2[i];
    }
  }
  for (int i = 0; i < 128; ++i) {
    if (i < kout2) {
      b_out[i] = a[i];
    }
     else {}
  }
// done
}