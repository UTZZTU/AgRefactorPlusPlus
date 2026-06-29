#include <iostream>
#include <cmath>

template <typename T>
void diagonal_PE (T x, T y, T &c, T &s)
{
  T sqr;
  T root;
  sqr = x*x + y*y;
  root = sqrt(sqr);
  c = y/root;
  s = -x/root;
}

template<int M, typename T>
void offdiagonal_PE_tb (T (&A)[M][2*M], unsigned pivot, T c, T s, unsigned j)
{
  T row1[2][1];
  T row2[2][1];

  for (int i=0; i<2*M; i++) {
    row1[0][0] = A[pivot-1][i];
    row1[1][0] = A[pivot][i];
    row2[0][0] = c*row1[0][0]-s*row1[1][0];
    row2[1][0] = c*row1[1][0]+s*row1[0][0];
    A[pivot-1][i] = row2[0][0];
    A[pivot][i] = row2[1][0];
  }
}

template<int M, typename T>
void matrixmul_double (T (&A)[M][M], T (&B)[M][M], T (&C)[M][M])
{
  T sum = 0;
  for (unsigned i = 0; i<M; i++) {
    for (unsigned j = 0; j<M; j++) {
      for (unsigned k = 0; k<M; k++) {
        sum = sum + A[i][k] * B[k][j];
      }
      C[i][j] = sum;
      sum = 0;
    }
  }
}

template <int M, typename T>
void qrd_compute (T A[M][M], T (&Q)[M][M], T (&R)[M][M])
{
  T  c;
  T  s;
  T  s1;
  T A1[M][2*M];
  T ans[M][M];
  for (int i=0; i<M; i++) {
    for (int j=0; j<M; j++) {
      A1[i][j] = A[i][j];
      A1[i][j+M] = (j+M-i == M) ? 1: 0;
    }
  }

  for (int j=0; j<M; j++) {
    for (int k=M-1; k>j; k--) {
      diagonal_PE<T>(A1[k][j], A1[k-1][j], c, s);
      offdiagonal_PE_tb<M,T>(A1,k,c,s,j);
    }
  }

  for (int i=0; i<M; i++) {
    for (int j=0; j<M; j++) {
      Q[j][i] = A1[i][j+M];
      R[i][j] = A1[i][j];
    }
  }
}

class ExceptionHandler { //Need to insert an exception module to terminal other modules
};
