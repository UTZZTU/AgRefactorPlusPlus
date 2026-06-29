#ifndef FALCONGENOMICS_COMMON_H
#define FALCONGENOMICS_COMMON_H

#include <jni.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <time.h>
#include <mutex>
#include <thread>
#include <map>

#include <stdexcept>
#include <string>
#include <glog/logging.h>

#endif

#ifndef BQSR_MATH_UTILS_H
#define BQSR_MATH_UTILS_H

#include <limits>

// Implementation of selected routines in
// GATK MathUtils and QualityUtils used for BQSR and PR
class MathUtils {
  public:
    // initialize caches
    MathUtils();

    ~MathUtils();

    double bayesianEstimateOfEmpiricalQuality(
          uint64_t nObservations,
          uint64_t nErrors,
          double QReported);

    double log10QempPrior(double Qempirical, double Qreported);
    double log10QempLikelihood(double Qempirical,
              uint64_t nObservations, uint64_t nErrors);

  protected:
    inline double log10BinomialCoefficient(int n, int k);
    inline double log10BinomialProbability(int n, int k, double log10p);
    inline double log10Factorial(int x);

    const int MAX_PHRED_SCORE = 93;
    const double RESOLUTION_BINS_PER_QUAL = 1.0;
    const int8_t MAX_REASONABLE_Q_SCORE = 60;
    const int8_t MAX_GATK_USABLE_Q_SCORE = 40;

    double* log10QempPriorCache;
    double* Log10FactorialCache;
    const int Log10FactorialCacheSize = 1024*1024;

    SimpleTimer timer_;
};
#endif

#define _USE_MATH_DEFINES
#include <cmath>
#include <cstdlib>
#include <limits>
#include <stdexcept>

MathUtils::MathUtils() {
  log10QempPriorCache = new double[MAX_GATK_USABLE_Q_SCORE + 1];

  const double GF_a = 0.0;
  const double GF_b = 0.9;
  const double GF_c = 0.0;
  const double GF_d = 0.5;

  for (int i = 0; i <= MAX_GATK_USABLE_Q_SCORE; i++) {
    double iMc = (double)(i) - GF_c;
    double value = GF_a + GF_b*exp(-1*iMc * iMc / (2.0 * (GF_d * GF_d)));
    double log10Prior = log10(value);
    if (std::isinf(log10Prior)) {
      log10Prior = -(std::numeric_limits<double>::max());
    }
    log10QempPriorCache[i] = log10Prior;
  }

  Log10FactorialCache = new double[Log10FactorialCacheSize];
  Log10FactorialCache[0] = 0.0;
  for (int i = 1; i < Log10FactorialCacheSize; i++) {
    Log10FactorialCache[i] = Log10FactorialCache[i-1] + std::log10(i);
  }
}

MathUtils::~MathUtils() {
  delete [] log10QempPriorCache;
  delete [] Log10FactorialCache;
}

double MathUtils::bayesianEstimateOfEmpiricalQuality(
    uint64_t nObservations,
    uint64_t nErrors,
    double QReported)
{
  int numBins = (MAX_REASONABLE_Q_SCORE + 1) * (int)RESOLUTION_BINS_PER_QUAL;

  double* log10Posteriors = (double*)malloc(numBins*sizeof(double));

  // NOTE: based on timer setup, this for loop takes most of the time.
  double max_val = -(std::numeric_limits<double>::max());
  for (int bin = 0; bin < numBins; bin++) {
    double QEmpOfBin = (double)bin / RESOLUTION_BINS_PER_QUAL;
    log10Posteriors[bin] = log10QempPrior(QEmpOfBin, QReported) +
                           log10QempLikelihood(QEmpOfBin, nObservations, nErrors);
    if (max_val < log10Posteriors[bin]) {
      max_val = log10Posteriors[bin];
    }
  }

  // double[] normalizedPosteriors = MathUtils.normalizeFromLog10(log10Posteriors);
  // int MLEbin = MathUtils.maxElementIndex(normalizedPosteriors);
  double sum = 0.0;
  for (int bin = 0; bin < numBins; bin++) {
    log10Posteriors[bin] = std::pow(10, log10Posteriors[bin] - max_val);
    sum += log10Posteriors[bin];
  }
  int MLEbin = 0;
  for (int bin = 0; bin < numBins; bin++) {
    log10Posteriors[bin] = log10Posteriors[bin] / sum;
    if (log10Posteriors[bin] > log10Posteriors[MLEbin]) {
      MLEbin = bin;
    }
  }
  free(log10Posteriors);

  return MLEbin / RESOLUTION_BINS_PER_QUAL;
}


double MathUtils::log10QempPrior(double Qempirical, double Qreported) {
  int difference = std::min(std::abs((int)(Qempirical - Qreported)),
                            (int)MAX_GATK_USABLE_Q_SCORE);
  return log10QempPriorCache[difference];
}

static inline double qualToErrorProbLog10(double qual) {
  return qual * -0.1;
}

static inline int HI(double x) {
  uint64_t ret;
  // copy the bits of x to result
  memcpy(&ret, &x, sizeof(x));
  return (int)(ret >> 32);
}

static inline int LO(double x) {
  uint64_t ret;
  // copy the bits of x to result
  memcpy(&ret, &x, sizeof(x));
  return (int)(ret);
}

static double zero = 0.0, one = 1.0, half = .5, a0 = 7.72156649015328655494e-02, a1 = 3.22467033424113591611e-01, a2 = 6.73523010531292681824e-02, a3 = 2.05808084325167332806e-02, a4 = 7.38555086081402883957e-03, a5 = 2.89051383673415629091e-03, a6 = 1.19270763183362067845e-03, a7 = 5.10069792153511336608e-04, a8 = 2.20862790713908385557e-04, a9 = 1.08011567247583939954e-04, a10 = 2.52144565451257326939e-05, a11 = 4.48640949618915160150e-05, tc = 1.46163214496836224576e+00, tf = -1.21486290535849611461e-01, tt = -3.63867699703950536541e-18, t0 = 4.83836122723810047042e-01, t1 = -1.47587722994593911752e-01, t2 = 6.46249402391333854778e-02, t3 = -3.27885410759859649565e-02, t4 = 1.79706750811820387126e-02, t5 = -1.03142241298341437450e-02, t6 = 6.10053870246291332635e-03, t7 = -3.68452016781138256760e-03, t8 = 2.25964780900612472250e-03, t9 = -1.40346469989232843813e-03, t10 = 8.81081882437654011382e-04, t11 = -5.38595305356740546715e-04, t12 = 3.15632070903625950361e-04, t13 = -3.12754168375120860518e-04, t14 = 3.35529192635519073543e-04, u0 = -7.72156649015328655494e-02, u1 = 6.32827064025093366517e-01, u2 = 1.45492250137234768737e+00, u3 = 9.77717527963372745603e-01, u4 = 2.28963728064692451092e-01, u5 = 1.33810918536787660377e-02, v1 = 2.45597793713041134822e+00, v2 = 2.12848976379893395361e+00, v3 = 7.69285150456672783825e-01, v4 = 1.04222645593369134254e-01, v5 = 3.21709242282423911810e-03, s0 = -7.72156649015328655494e-02, s1 = 2.14982415960608852501e-01, s2 = 3.25778796408930981787e-01, s3 = 1.46350472652464452805e-01, s4 = 2.66422703033638609560e-02, s5 = 1.84028451407337715652e-03, s6 = 3.19475326584100867617e-05, r1 = 1.39200533467621045958e+00, r2 = 7.21935547567138069525e-01, r3 = 1.71933865632803078993e-01, r4 = 1.86459191715652901344e-02, r5 = 7.77942496381893596434e-04, r6 = 7.32668430744625636189e-06, w0 = 4.18938533204672725052e-01, w1 = 8.33333333333329678849e-02, w2 = -2.77777777728775536470e-03, w3 = 7.93650558643019558500e-04, w4 = -5.95187557450339963135e-04, w5 = 8.36339918996282139126e-04, w6 = -1.63092934096575273989e-03;

static inline double lnGamma(double x) {
  double t, y, z, p, p1, p2, p3, q, r, w;
  int i;

  int hx = HI(x);
  int lx = LO(x);

  /* purge off +-inf, NaN, +-0, and negative arguments */
  int ix = hx & 0x7fffffff;
  if (ix >= 0x7ff00000)
    return std::numeric_limits<double>::infinity();

  if ((ix | lx) == 0 || hx < 0)
    return std::numeric_limits<double>::signaling_NaN();

  if (ix < 0x3b900000) {    /* |x|<2**-70, return -log(|x|) */
    return -std::log(x);
  }

  /* purge off 1 and 2 */
  if ((((ix - 0x3ff00000) | lx) == 0) || (((ix - 0x40000000) | lx) == 0))
  r = 0;
  /* for x < 2.0 */
  else if (ix < 0x40000000) {
    if (ix <= 0x3feccccc) {     /* lgamma(x) = lgamma(x+1)-log(x) */
      r = -std::log(x);
      if (ix >= 0x3FE76944) {
        y = one - x;
        i = 0;
      }
      else if (ix >= 0x3FCDA661) {
        y = x - (tc - one);
        i = 1;
      }
      else {
        y = x;
        i = 2;
      }
    }
    else {
      r = zero;
      if (ix >= 0x3FFBB4C3) {
        y = 2.0 - x;
        i = 0;
      } /* [1.7316,2] */
      else if (ix >= 0x3FF3B4C4) {
        y = x - tc;
        i = 1;
      } /* [1.23,1.73] */
      else {
        y = x - one;
        i = 2;
      }
    }

    switch (i) {
      case 0:
        z = y * y;
        p1 = a0 + z * (a2 + z * (a4 + z * (a6 + z * (a8 + z * a10))));
        p2 = z * (a1 + z * (a3 + z * (a5 + z * (a7 + z * (a9 + z * a11)))));
        p = y * p1 + p2;
        r += (p - 0.5 * y);
        break;
      case 1:
        z = y * y;
        w = z * y;
        p1 = t0 + w * (t3 + w * (t6 + w * (t9 + w * t12)));    /* parallel comp */
        p2 = t1 + w * (t4 + w * (t7 + w * (t10 + w * t13)));
        p3 = t2 + w * (t5 + w * (t8 + w * (t11 + w * t14)));
        p = z * p1 - (tt - w * (p2 + y * p3));
        r += (tf + p);
        break;
      case 2:
        p1 = y * (u0 + y * (u1 + y * (u2 + y * (u3 + y * (u4 + y * u5)))));
        p2 = one + y * (v1 + y * (v2 + y * (v3 + y * (v4 + y * v5))));
        r += (-0.5 * y + p1 / p2);
        break;
      default: ;
    }
  }
  else if (ix < 0x40200000) {             /* x < 8.0 */
    i = (int) x;
    t = zero;
    y = x - (double) i;
    p = y * (s0 + y * (s1 + y * (s2 + y * (s3 + y * (s4 + y * (s5 + y * s6))))));
    q = one + y * (r1 + y * (r2 + y * (r3 + y * (r4 + y * (r5 + y * r6)))));
    r = half * y + p / q;
    z = one;    /* lgamma(1+s) = log(s) + lgamma(s) */
    switch (i) {
      case 7:
        z *= (y + 6.0);    /* FALLTHRU */
      case 6:
        z *= (y + 5.0);    /* FALLTHRU */
      case 5:
        z *= (y + 4.0);    /* FALLTHRU */
      case 4:
        z *= (y + 3.0);    /* FALLTHRU */
      case 3:
        z *= (y + 2.0);    /* FALLTHRU */
        r += std::log(z);
      break;
    }
    /* 8.0 <= x < 2**58 */
  }
  else if (ix < 0x43900000) {
    t = std::log(x);
    z = one / x;
    y = z * z;
    w = w0 + z * (w1 + y * (w2 + y * (w3 + y * (w4 + y * (w5 + y * w6)))));
    r = (x - half) * (t - one) + w;
  }
  else {
    /* 2**58 <= x <= inf */
    r = x * (std::log(x) - one);
  }
  return r;
}

static inline double log10Gamma(double x) {
  return lnGamma(x) * std::log10(M_E);
}

inline double MathUtils::log10Factorial(int x) {
  if (x >= Log10FactorialCacheSize || x < 0) {
    return log10Gamma(x+1);
  }
  else {
    return Log10FactorialCache[x];
  }
}

inline double MathUtils::log10BinomialCoefficient(int n, int k) {
  if (n < 0) {
    throw new std::runtime_error("n: Must have non-negative number of trials");
  }
  if (k > n || k < 0) {
    throw new std::runtime_error("k: Must have non-negative number of successes, and no more successes than number of trials");
  }
  return log10Factorial(n) - log10Factorial(k) - log10Factorial(n - k);
}

double MathUtils::log10BinomialProbability(int n, int k, double log10p) {
  if (log10p > 1e-18) {
    throw new std::runtime_error("log10p: Log-probability must be 0 or less");
  }
  double log10OneMinusP = std::log10(1 - std::pow(10, log10p));
  return log10BinomialCoefficient(n, k) + log10p * k + log10OneMinusP * (n - k);
}

double MathUtils::log10QempLikelihood(double Qempirical,
                            uint64_t nObservations,
                            uint64_t nErrors)
{
  if (nObservations == 0) return 0.0;

  // the binomial code requires ints as input (because it does caching).  This should theoretically be fine because
  // there is plenty of precision in 2^31 observations, but we need to make sure that we don't have overflow
  // before casting down to an int.
  uint64_t MAX_NUMBER_OF_OBSERVATIONS = std::numeric_limits<int>::max() - 1;
  if (nObservations > MAX_NUMBER_OF_OBSERVATIONS) {
    // we need to decrease nErrors by the same fraction that we are decreasing nObservations
    double fraction = (double)MAX_NUMBER_OF_OBSERVATIONS / (double)nObservations;
    nErrors = std::round((double)nErrors * fraction);
    nObservations = MAX_NUMBER_OF_OBSERVATIONS;
  }

  // this is just a straight binomial PDF
  double log10Prob = log10BinomialProbability((int)nObservations, (int)nErrors,
                        qualToErrorProbLog10(Qempirical));
  if (std::isinf(log10Prob) || std::isnan(log10Prob)) {
    log10Prob = -(std::numeric_limits<double>::max());
  }
  return log10Prob;
}

#include <cstdint>
#include <vector>

// This struct is used table construct to save space
typedef struct {
  uint64_t numOccurance;
  double   numMismatches;
} Datum;

// this struct is used in table lookup after table is constructed
typedef struct {
  uint64_t numOccurance;
  double   numMismatches;
  double   estimatedQReported;
  double   empiricalQuality;
} RecalDatum;

class RecalibrationTable {
  public:
    // construct an empty table for the recalibrator
    RecalibrationTable(int  numReadGroups,
                       int  numEvents,
                       int  numCovariates,
                       int* dims);

    RecalibrationTable(int  numReadGroups,
                       int  numEvents,
                       int  numCovariates,
                       int* dims,
                       int quantTableSize,
                       int8_t* quantizationTable,
                       int staticQuantizedMappingSize,
                       int8_t* staticQuantizedMapping,
                       bool disableIndelQuals,
                       int preserveQLessThan,
                       double globalQScorePrior,
                       bool emitOriginalQuals);

    ~RecalibrationTable();

    // insert a recal datum into the
    // - keys: the covariates organizing using GATK format,
    //         rg, qual, cov1/cov2, numEvents_
    // - cov_idx: index to the covariates
    void put(RecalDatum& e, int* keys, int cov_idx);

    // histogram from input read
    // used in GATK BaseRecalibrator
    void update(int      readLength,
                int*     keys,
                uint8_t* skips,
                //uint8_t** quals,
                double** isErrors);

    // recalibrate read by contents in the table
    // used in GATK PrintReads
    int recalibrate(int readLength,
                    int* keys,       // computed by Covariates.compute()
                    int8_t** quals,
                    int8_t** recal_quals); // output quals (snp, indel, delete)

    Datum* getTable(int idx);
    RecalDatum* getFullTable(int idx);

    int getTableSize(int idx);
    std::vector<int> getTableDimensions(int idx);

  private:
    // Peipei : add numReadsProcessed;
    long long numReadsProcessed;
    // helper function for update() and recalibrate()
    inline int keysToIndex(int* keys,
        int cov_idx, int rd_idx, int event_idx);

    // helper functions for recalibrate()
    inline double getEmpiricalQuality(
        int cov_idx, int idx, double prior);

    inline double hierarchicalBayesianQualityEstimate(
        double epsilon, int* datum_indexes);

    // used for math routines in this class
    MathUtils math_utils_;

    std::vector<Datum*> DatumTables_;
    std::vector<RecalDatum*> RecalDatumTables_;
    std::vector<std::vector<int> > DatumTableDimensions_;
    std::vector<int> tableSizes_;

    int numReadGroups_;
    int numEvents_;
    int numCovariates_;

    int8_t* quantizationTable_      = NULL;
    int8_t* staticQuantizedMapping_ = NULL;

    // parameters for recalibrate
    bool   disableIndelQuals_;
    int    preserveQLessThan_;
    double globalQScorePrior_;
    bool   emitOriginalQuals_;

    SimpleTimer timer_;
};

#include <cstdlib>
#include <stdexcept>

RecalibrationTable::RecalibrationTable(
    int numReadGroups,
    int numEvents,
    int numCovariates,
    int* dims): math_utils_(),
                DatumTables_(numCovariates, NULL),
                RecalDatumTables_(numCovariates, NULL),
                DatumTableDimensions_(numCovariates),
                tableSizes_(numCovariates),
                numReadGroups_(numReadGroups),
                numEvents_(numEvents),
                numCovariates_(numCovariates) {

  //
  numReadsProcessed = 0;

  int qualDimension = dims[1];
  // skip the first RG table
  for (int i = 0; i < numCovariates; i++) {

    // all tables starts with numReadGroups x numEvents
    DatumTableDimensions_[i].push_back(numEvents);
    DatumTableDimensions_[i].push_back(numReadGroups);
    if (i > 0) DatumTableDimensions_[i].push_back(qualDimension);
    if (i > 1) DatumTableDimensions_[i].push_back(dims[i]);

    // allocate RecalDatum tables
    uint64_t tableSize = 1;
    for (auto dim : DatumTableDimensions_[i]) {
      tableSize *= dim;
    }
    tableSizes_[i] = tableSize;
    // skip the RG table here since we are doing recalibration
    if (i == 0) continue;
    DatumTables_[i] = (Datum*)calloc(tableSize, sizeof(Datum));
  }
  DLOG(INFO) << "Initialized RecalibrationTable";
}

RecalibrationTable::RecalibrationTable(
    int  numReadGroups,
    int  numEvents,
    int  numCovariates,
    int* dims,
    int  quantTableSize,
    int8_t* quantizationTable,
    int staticQuantizedMappingSize,
    int8_t* staticQuantizedMapping,
    bool    disableIndelQuals,
    int     preserveQLessThan,
    double  globalQScorePrior,
    bool    emitOriginalQuals):
        RecalibrationTable(numReadGroups, numEvents, numCovariates, dims)
{
  numReadsProcessed = 0;
  disableIndelQuals_ = disableIndelQuals;
  preserveQLessThan_ = preserveQLessThan;
  globalQScorePrior_ = globalQScorePrior;
  emitOriginalQuals_ = emitOriginalQuals;

  // all other fields should be initialized by overloaded constructor
  if (quantTableSize) {
    quantizationTable_ = (int8_t*)malloc(quantTableSize);
    memcpy(quantizationTable_, quantizationTable, quantTableSize);
  }
  if (staticQuantizedMapping) {
    staticQuantizedMapping_ = (int8_t*)malloc(staticQuantizedMappingSize);
    memcpy(staticQuantizedMapping_, staticQuantizedMapping, staticQuantizedMappingSize);
  }

  for (int i = 0; i < numCovariates; i++) {
    // free DatumTable to save space
    if (i > 0) {
      free(DatumTables_[i]);
      DatumTables_[i] = NULL;
    }

    RecalDatumTables_[i] = (RecalDatum*)calloc(tableSizes_[i], sizeof(RecalDatum));
    for (int k = 0; k < tableSizes_[i]; k++) {
      // mark empiricalQuality as uninitialized
      RecalDatumTables_[i][k].numOccurance = 0;
      RecalDatumTables_[i][k].numMismatches = .0;
      RecalDatumTables_[i][k].estimatedQReported = .0;
      RecalDatumTables_[i][k].empiricalQuality = -1.0;
    }
  }

  DLOG(INFO) << "Initialized RecalibrationTable";
}

RecalibrationTable::~RecalibrationTable() {
  // release DatumTables_
  for (auto table = DatumTables_.begin(); table != DatumTables_.end(); ++table) {
    if (*table) {
      free(*table);
      *table = NULL;
    }
  }

  // release RecalDatumTables_
  for (auto table = RecalDatumTables_.begin(); 
       table != RecalDatumTables_.end(); ++table) 
  {
    if (*table) {
      free(*table);
      *table = NULL;
    }
  }

  if (quantizationTable_) free(quantizationTable_);
  if (staticQuantizedMapping_) free(staticQuantizedMapping_);

  DLOG(INFO) << "Free RecalibrationTable";
}

void RecalibrationTable::put(RecalDatum &e, int* keys, int cov_idx) {
  if (cov_idx >= numCovariates_) {
    throw std::runtime_error("invalid table index");
  }
  // calculate index from keys
  // we need to change the layout a little, putting numEvents from
  // the last element to the first
  int num_dims = 2;

  // for covariate 1 (qual), the dimensions are 3
  if (cov_idx > 0) num_dims ++;

  // for covariates after 1, the dimensions are 4
  if (cov_idx > 1) num_dims ++;

  // shifting numEvents from last element to first
  // then start the idx calculation
  int idx = keys[num_dims - 1];
  int pitch = numEvents_;
  for (int i = 0; i < num_dims - 1; i++) {
    idx += pitch*keys[i];
    pitch *= DatumTableDimensions_[cov_idx][i+1];
  }
  if (idx >= tableSizes_[cov_idx]) {
    DLOG(ERROR) << "invalid idx = " << idx << " for cov#" << cov_idx;
  }

  RecalDatumTables_[cov_idx][idx].numOccurance       = e.numOccurance;
  RecalDatumTables_[cov_idx][idx].numMismatches      = e.numMismatches;
  RecalDatumTables_[cov_idx][idx].estimatedQReported = e.estimatedQReported;
}

// Convert keys from computeCovariates to index to recaltables
// this function is used by both update() and recalibrate()
inline int RecalibrationTable::keysToIndex(int* keys,
      int cov_idx, int rd_idx, int event_idx)
{
  int dims[3] = {0};
  dims[0] = keys[rd_idx*numCovariates_*numEvents_ + 0*numEvents_ + event_idx];
  dims[1] = keys[rd_idx*numCovariates_*numEvents_ + 1*numEvents_ + event_idx];
  if (cov_idx > 1) {
    dims[2] = keys[rd_idx*numCovariates_*numEvents_ + cov_idx*numEvents_ + event_idx];
  }
  int idx = event_idx;
  int pitch = 1;
  int num_dims = DatumTableDimensions_[cov_idx].size()-1;
  for (int d = 0; d < num_dims; d++) {
    if (dims[d] < 0) return -1; // negative index means negative keys
    pitch *= DatumTableDimensions_[cov_idx][d];
    idx += dims[d]*pitch;
  }
  return idx;
}

void RecalibrationTable::update(int readLength,
    int*    keys,
    uint8_t*  skips,
    //uint8_t** quals,
    double**  isErrors) {
  /**
   * Covariates:
   *  - RGCov (omitted here)
   *  - QualCov: 3-dimensions, Qual x RG x Events
   *  - OptCov: 4-dimensions, Cov x Qual x RG x Events
   * keys: readLength x [RG, Qual, Cov1, Cov2] x numEvents
   *  - select two or three from keys to update different cov tables
   * skips: numEvents x readLength
   * DatumTables: numCovariates x [[Cov] x Qual x RG x Events]
   * DatumTablesDim: numCovariates x [Events, RG, Qual, [Cov]]
   */
    for (int i = 1; i < numCovariates_; i++) {

    for (int j = 0; j < readLength; j++) {
      if (skips[j]) continue;
       for(int k = 0; k < numEvents_; k++){ 
        int idx = keysToIndex(keys, i, j, k);
        if (idx < 0) continue;

        DatumTables_[i][idx].numOccurance += 1;
        DatumTables_[i][idx].numMismatches += isErrors[k][j];

      }

    }

  }

  numReadsProcessed++;


}

// const number in bayesian calculations below
const int MAX_PHRED_SCORE = 93;
const int8_t MAX_RECALIBRATED_Q_SCORE = MAX_PHRED_SCORE;

inline double RecalibrationTable::getEmpiricalQuality(
      int cov_idx, int idx, double prior)
{
  const int SMOOTHING_CONSTANT = 1;

  RecalDatum* q = &RecalDatumTables_[cov_idx][idx];
  if (q->empiricalQuality == -1.0) {
    // calculate empiricalQuality
    // smoothing is one error and one non-error observation
    uint64_t mismatches = (uint64_t)(q->numMismatches + 0.5) + SMOOTHING_CONSTANT;
    uint64_t observations = q->numOccurance + SMOOTHING_CONSTANT + SMOOTHING_CONSTANT;

    //timer_.start(1);
    double empiricalQual = math_utils_.bayesianEstimateOfEmpiricalQuality(
                             observations, mismatches, prior);
    //timer_.stop(1);

    // This is the old and busted point estimate approach:
    //final double empiricalQual = -10 * Math.log10(getEmpiricalErrorRate());
     q->empiricalQuality = std::min(empiricalQual, (double)MAX_RECALIBRATED_Q_SCORE);
   }
   return q->empiricalQuality;
}

inline double RecalibrationTable::hierarchicalBayesianQualityEstimate(
          double epsilon, int* datum_indexes) {

  double ret = epsilon;
  double delta_prior = epsilon;

  for (int i = 0; i < numCovariates_; i++) {
    if (datum_indexes[i] < tableSizes_[i]) {
      int idx = datum_indexes[i];
      double q = idx < 0 ? 0.0 : getEmpiricalQuality(i, idx, delta_prior)
                 - delta_prior;
      ret += q;
      if (i < 2) delta_prior += q; // only need to add delta Qs for RG and Qual
    }
  }
  return ret;
}

static inline int fastRound(double d) {
  return (d > 0.0) ? (int)(d + 0.5) : (int)(d - 0.5);
}

static inline int8_t boundQual(int qual, int8_t maxQual) {
  return std::max(std::min(qual, maxQual & 0xFF), 1) & 0xFF;
}

// recalibrate read's qualities using RecalibrationTables
// quals: numEvents x readLength
//        input qualities for different events
// recal_quals: output results, allocated here
int RecalibrationTable::recalibrate(int readLength,
    int* keys,
    int8_t** quals,
    int8_t** recal_quals) {

    for (int k = 0; k < numEvents_; k++) {
        if (disableIndelQuals_ && k > 0) {
      // skip events 1 and 2, which means:
      // BASE_INSERTION and BASE_DELETION
      continue;
    }
    // the rg key is constant over the whole read, the global deltaQ is too
    int rgIdx = keysToIndex(keys, 0, 0, k); // keys[0][0][event]
    if (rgIdx >= tableSizes_[0] || rgIdx < 0) {
      DLOG(INFO) << "invalid read group";
      // equivalent to empiricalQualRG == null
      memcpy(recal_quals[k], quals[k], readLength);
      continue;
    }
    RecalDatum empiricalQualRG = RecalDatumTables_[0][rgIdx];
    double epsilon = (globalQScorePrior_ > 0.0 &&
                      k == 0 // event = BASE_SUBSTITUTION
                     ) ? globalQScorePrior_ :
                         empiricalQualRG.estimatedQReported;

    //DLOG(INFO) << "epsilon = " << epsilon;

    int* empirical_quals_idx = (int*)malloc(numCovariates_*sizeof(int));
    for (int j = 0; j < readLength; j++) {
      if (quals[k][j] < preserveQLessThan_) {
        recal_quals[k][j] = quals[k][j];
        continue;
      }

      // here in hierarchicalBayesianQualityEstimate() function,
      // the values of RecalDatum will be modified, and reused in future
      // references, therefore we pass the idx rather than actual values
      // to the function.
      for (int i = 0; i < numCovariates_; i++) {
        empirical_quals_idx[i] = keysToIndex(keys, i, j, k);
      }
      //timer_.start(0);
      double recalibratedQualDouble = hierarchicalBayesianQualityEstimate(
            epsilon, empirical_quals_idx);
      //timer_.stop(0);
      //DLOG(INFO) << "recalibratedQualDouble = " << recalibratedQualDouble;


      // recalibrated quality is bound between 1 and MAX_QUAL
      int8_t recalibratedQual = boundQual(fastRound(recalibratedQualDouble),
                                          MAX_RECALIBRATED_Q_SCORE);

      // return the quantized version of the recalibrated quality
      int8_t recalibratedQualityScore = quantizationTable_[recalibratedQual];

      // Bin to static quals
      if(staticQuantizedMapping_ != NULL) {
        recal_quals[k][j] = staticQuantizedMapping_[recalibratedQualityScore];
      }
      else {
        recal_quals[k][j] = recalibratedQualityScore;
      }
    }
    free(empirical_quals_idx);
  }

  return 0;
}

Datum* RecalibrationTable::getTable(int idx) {
  if (idx >= (int)DatumTables_.size()) {
    throw std::runtime_error("invalid table size");
  }
  return DatumTables_[idx];
}

RecalDatum* RecalibrationTable::getFullTable(int idx) {
  if (idx >= (int)RecalDatumTables_.size()) {
    throw std::runtime_error("invalid table size");
  }
  return RecalDatumTables_[idx];
}

int RecalibrationTable::getTableSize(int idx) {
  return tableSizes_[idx];
}

std::vector<int> RecalibrationTable::getTableDimensions(int idx) {
  return DatumTableDimensions_[idx];
}

#ifndef ACCLIB_TIMER_H
#define ACCLIB_TIMER_H

#include <map>
#include <stdexcept>
#include <stdio.h>
#include <string>
#include <string.h>
#include <sys/time.h>
#include <time.h>

#ifndef TIMER_VERBOSE
#define TIMER_VERBOSE 0
#endif

#ifndef TIMER_REPORT
#define TIMER_REPORT true
#endif

extern std::map<std::string, uint64_t> g_total_time;
extern std::map<std::string, uint64_t> g_last_time;

inline uint64_t getUsTimer() {
  struct timespec tr;
  clock_gettime(CLOCK_REALTIME, &tr);
  return (uint64_t)tr.tv_sec*1e6 + tr.tv_nsec/1e3;
}

static void print_global_timers() {
  for (auto p : g_total_time) {
    fprintf(stderr, "\"%s\", %d\n",
        p.first.c_str(),
        p.second);
  }
}

static uint64_t get_global_etime(std::string s) {
  if (g_total_time.count(s)) return g_total_time[s];
  else return 0;
}

// unit: us
static void add_global_etime(std::string s, uint64_t t) {
  if (g_total_time.count(s)) g_total_time[s] += t;
  else g_total_time[s] = t;
}

// unit: us
static uint64_t get_last_etime(std::string s) {
  if (g_last_time.count(s)) return g_last_time[s];
  else 0;
}

static void add_last_etime(std::string s, uint64_t t) {
  if (g_last_time.count(s)) g_last_time[s] += t;
  else g_last_time[s] = t;
}

class Timer {
 public: 
  Timer(std::string func = "-", 
      bool flag_report = TIMER_REPORT,
      int verbose = TIMER_VERBOSE): 
    func_(func), verbose_(verbose), flag_report_(flag_report)
  {
    if (func.empty()) {
      throw std::runtime_error("Timer::Timer(): timer name cannot be empty");
    }
    start_ts_ = getUsTimer(); 
  }

  ~Timer() {
    uint64_t e_time = getUsTimer()-start_ts_;
    if (verbose_ > 0) {
      fprintf(stderr, "[Timer]: %s takes %ld us\n", func_.c_str(), e_time);
    }
    if (flag_report_) {
      add_global_etime(func_, e_time);
      add_last_etime(func_, e_time);
    }
  }
  
 private:
  int verbose_;
  bool flag_report_;
  std::string func_;
  uint64_t start_ts_;
};

// only suppose to call once
#define DEFINE_GLOBAL_TIMER std::map<std::string, uint64_t> g_total_time; \
  std::map<std::string, uint64_t> g_last_time; 

#define PLACE_TIMER Timer __timer_obj(__func__);
#define CONCAT_FNAME(A, B) (std::string(A) + "::" + std::string(B))
#define PLACE_TIMER1(s) Timer __timer_obj(CONCAT_FNAME(__func__, s));

#endif

#include <jni.h>
/* Header for class com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine */

#ifndef _Included_com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine
#define _Included_com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine
#ifdef __cplusplus
extern "C" {
#endif
#undef com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine_minBaseQual
#define com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine_minBaseQual 4L


/*
 * Class:     com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine
 * Method:    recalibrateNative
 * Signature: ([B[B[B[BLjava/lang/String;ZZZI)[[B
 */
JNIEXPORT jobjectArray JNICALL Java_com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine_recalibrateNative
  (JNIEnv *, jobject, jbyteArray, jbyteArray, jbyteArray, jbyteArray, jstring, jboolean, jboolean, jboolean, jint);

#ifdef __cplusplus
}
#endif
#endif


int g_numReadGroups = 0;
int g_numEvents = 0;
int g_numCovariates = 0;

RecalibrationTable* table = NULL;
Covariates*         cov   = NULL;
BAQ*                baq   = NULL;

// for performance measuring
uint64_t total_baq_time = 0;
uint64_t total_baq_compute_time = 0;

uint64_t total_update_baq_time = 0;
uint64_t total_update_covariate_time = 0;
uint64_t total_update_compute_time = 0;
uint64_t total_update_total_time = 0;
int      total_update_num_calls = 0;

int lap_recalibrate_num_calls = 0;
int total_recalibrate_num_calls = 0;

SimpleTimer timer;
DEFINE_GLOBAL_TIMER ;


// top function defined here
JNIEXPORT jobjectArray JNICALL Java_com_falconcomputing_genomics_bqsr_FalconRecalibrationEngine_recalibrateNative(
    JNIEnv *env, jobject obj,
    jbyteArray jbases,
    jbyteArray jbaseQuals,
    jbyteArray jinsertionQuals,
    jbyteArray jdeletionQuals,
    jstring  jreadGroup,
    jboolean isNegativeStrand,
    jboolean isReadPaired,
    jboolean isSecondOfPair,
    jint platformType)
{
  //{

    //PLACE_TIMER;
  //}
  timer.start(0);
  int readLength = env->GetArrayLength(jbases);
  int8_t* bases = (int8_t*)env->GetByteArrayElements(jbases, 0);
  int8_t** quals = (int8_t**)malloc(g_numEvents*sizeof(int8_t*));
  quals[0] = (int8_t*)env->GetByteArrayElements(jbaseQuals, 0);
  quals[1] = (int8_t*)env->GetByteArrayElements(jinsertionQuals, 0);
  quals[2] = (int8_t*)env->GetByteArrayElements(jdeletionQuals, 0);

  const char* readGroup = env->GetStringUTFChars(jreadGroup, NULL);

  timer.start(1);
  // compute covariates
  int* keys = (int*)malloc(g_numEvents*readLength*g_numCovariates*sizeof(int));
  try {
    cov->compute(keys, readLength, std::string(readGroup),
          bases, quals[0], quals[1], quals[2],
          platformType,
          isNegativeStrand, isReadPaired, isSecondOfPair);
  } catch (std::runtime_error &e) {
    throwAccError(env, e.what());
    return 0;
  }

  timer.stop(1);

  // recalibrate
  int8_t** recal_quals = (int8_t**)malloc(g_numEvents*sizeof(int8_t*));
  for (int i = 0; i < g_numEvents; i++) {
    // the recal table should not need to be freed since it's
    // returned to java
    recal_quals[i] = (int8_t*)malloc(readLength*sizeof(int8_t));
  }

  timer.start(2);
  table->recalibrate(readLength, keys, quals, recal_quals);
  timer.stop(2);

  // return value is byte[][] --> B]]
  jclass byte_2d_cls = env->FindClass("[B");
  if (!byte_2d_cls) DLOG(ERROR) << "cannot find byte array class";

  env->ReleaseByteArrayElements(jbases, bases, 0);
  env->ReleaseByteArrayElements(jbaseQuals, quals[0], 0);
  env->ReleaseByteArrayElements(jinsertionQuals, quals[1], 0);
  env->ReleaseByteArrayElements(jdeletionQuals, quals[2], 0);
  env->ReleaseStringUTFChars(jreadGroup, readGroup);
  free(quals);
  free(keys);

  jobjectArray ret = env->NewObjectArray(g_numEvents, byte_2d_cls, NULL);

  for (int i = 0; i < g_numEvents; i++) {
    jbyteArray array = env->NewByteArray(readLength);
    env->SetByteArrayRegion(array, 0, readLength, recal_quals[i]);
    env->SetObjectArrayElement(ret, i, array);
    env->ReleaseByteArrayElements(array, recal_quals[i], 0);
  }

  free(recal_quals);
  timer.stop(0);

  return ret;
}
