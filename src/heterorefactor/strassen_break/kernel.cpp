#include <cstdlib>

bool g_fallback = false;

// ---------- Utility Helpers ----------

int** allocateMatrix(int rows, int cols) {
    int** mat = (int**)malloc(sizeof(int*) * rows);
    if (!mat) { g_fallback = true; return NULL; }
    for (int i = 0; i < rows; i++) {
        mat[i] = (int*)malloc(sizeof(int) * cols);
        if (!mat[i]) { g_fallback = true; return NULL; }
        for (int j = 0; j < cols; j++) mat[i][j] = 0;
    }
    return mat;
}

void freeMatrix(int** mat, int rows) {
    for (int i = 0; i < rows; i++) free(mat[i]);
    free(mat);
}

int** addMatrix(int** A, int** B, int n, int m) {
    int** C = allocateMatrix(n, m);
    for (int i = 0; i < n; i++)
        for (int j = 0; j < m; j++)
            C[i][j] = A[i][j] + B[i][j];
    return C;
}

int** subMatrix(int** A, int** B, int n, int m) {
    int** C = allocateMatrix(n, m);
    for (int i = 0; i < n; i++)
        for (int j = 0; j < m; j++)
            C[i][j] = A[i][j] - B[i][j];
    return C;
}

// Direct O(n³) multiply
int** MatrixMultiply(int** a, int** b, int n, int l, int m) {
    int** c = allocateMatrix(n, m);
    for (int i = 0; i < n; i++)
        for (int j = 0; j < m; j++)
            for (int k = 0; k < l; k++)
                c[i][j] += a[i][k] * b[k][j];
    return c;
}

// ---------- Partitioning Helpers ----------

// Extract a quadrant with zero padding if out of bounds
int** extractBlock(int** A, int rowOff, int colOff, int rows, int cols, int maxR, int maxC) {
    int** block = allocateMatrix(rows, cols);
    for (int i = 0; i < rows; i++)
        for (int j = 0; j < cols; j++) {
            int r = i + rowOff, c = j + colOff;
            block[i][j] = (r < maxR && c < maxC) ? A[r][c] : 0;
        }
    return block;
}

// Place 4 quadrants back into a full matrix
void combineQuadrants(int** C, int** C11, int** C12, int** C21, int** C22,
                      int n, int m, int halfN, int halfM) {
    for (int i = 0; i < halfN; i++) {
        for (int j = 0; j < halfM; j++) {
            C[i][j] = C11[i][j];
            if (j + halfM < m) C[i][j + halfM] = C12[i][j];
            if (i + halfN < n) C[i + halfN][j] = C21[i][j];
            if (i + halfN < n && j + halfM < m) C[i + halfN][j + halfM] = C22[i][j];
        }
    }
}

// ---------- Strassen Algorithm ----------

int** Strassen(int** A, int** B, int n, int l, int m) {
    if (n == 1 || l == 1 || m == 1) 
        return MatrixMultiply(A, B, n, l, m);

    int halfN = (n >> 1) + (n & 1);
    int halfL = (l >> 1) + (l & 1);
    int halfM = (m >> 1) + (m & 1);

    // Partition A and B
    int** A11 = extractBlock(A, 0, 0, halfN, halfL, n, l);
    int** A12 = extractBlock(A, 0, halfL, halfN, halfL, n, l);
    int** A21 = extractBlock(A, halfN, 0, halfN, halfL, n, l);
    int** A22 = extractBlock(A, halfN, halfL, halfN, halfL, n, l);

    int** B11 = extractBlock(B, 0, 0, halfL, halfM, l, m);
    int** B12 = extractBlock(B, 0, halfM, halfL, halfM, l, m);
    int** B21 = extractBlock(B, halfL, 0, halfL, halfM, l, m);
    int** B22 = extractBlock(B, halfL, halfM, halfL, halfM, l, m);

    // Compute Strassen’s 7 products
    int** P1 = Strassen(A11, subMatrix(B12, B22, halfL, halfM), halfN, halfL, halfM);
    int** P2 = Strassen(addMatrix(A11, A12, halfN, halfL), B22, halfN, halfL, halfM);
    int** P3 = Strassen(addMatrix(A21, A22, halfN, halfL), B11, halfN, halfL, halfM);
    int** P4 = Strassen(A22, subMatrix(B21, B11, halfL, halfM), halfN, halfL, halfM);
    int** P5 = Strassen(addMatrix(A11, A22, halfN, halfL), addMatrix(B11, B22, halfL, halfM), halfN, halfL, halfM);
    int** P6 = Strassen(subMatrix(A12, A22, halfN, halfL), addMatrix(B21, B22, halfL, halfM), halfN, halfL, halfM);
    int** P7 = Strassen(subMatrix(A11, A21, halfN, halfL), addMatrix(B11, B12, halfL, halfM), halfN, halfL, halfM);

    // Combine into C quadrants
    int** C11 = allocateMatrix(halfN, halfM);
    int** C12 = allocateMatrix(halfN, halfM);
    int** C21 = allocateMatrix(halfN, halfM);
    int** C22 = allocateMatrix(halfN, halfM);

    for (int i = 0; i < halfN; i++)
        for (int j = 0; j < halfM; j++) {
            C11[i][j] = P5[i][j] + P4[i][j] - P2[i][j] + P6[i][j];
            C12[i][j] = P1[i][j] + P2[i][j];
            C21[i][j] = P3[i][j] + P4[i][j];
            C22[i][j] = P5[i][j] + P1[i][j] - P3[i][j] - P7[i][j];
        }

    // Assemble result
    int** C = allocateMatrix(n, m);
    combineQuadrants(C, C11, C12, C21, C22, n, m, halfN, halfM);

    return C;
}

// ---------- Entry Helper ----------

void process_top(int *np, int *lp, int *mp,
                 int *mat1, int *mat2, int *mat3, int *fallback) {
    int n = *np, l = *lp, m = *mp;

    int** A = allocateMatrix(n, l);
    int** B = allocateMatrix(l, m);

    for (int i = 0; i < n; i++)
        for (int j = 0; j < l; j++)
            A[i][j] = mat1[i * l + j];

    for (int i = 0; i < l; i++)
        for (int j = 0; j < m; j++)
            B[i][j] = mat2[i * m + j];

    int** C = Strassen(A, B, n, l, m);

    if (C) {
        for (int i = 0; i < n; i++)
            for (int j = 0; j < m; j++)
                mat3[i * m + j] = C[i][j];
    }

    *fallback = g_fallback;
}
