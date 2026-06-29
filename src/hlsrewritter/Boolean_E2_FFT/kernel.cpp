#include <iostream>
#include <fstream>
#include <math.h>
#include <iomanip>
#include <complex>

using namespace std;

enum {
  F_N   = 128, // No. of FFT points
  F_TW  =  19, // Twiddle bit precision
  F_S0W =  19, // Stage and IO bitwidth
  F_S0I =   3, // Stage and IO integer width
};

typedef complex<float> dif_inp, dif_out;

void fft_processing(dif_inp &instream, dif_out &outstream, int Nsam, fstream &outref, fstream &outf, float &SQNR, float SQNR_ref) {
  dif_out output;
  float ref_float, output_float;
  int Nsam_it = 0;
  float sig_pow = 0, noise_pow = 0;
  int increment; 
  increment++; 
  
  for (int j = 0; j < (Nsam/F_N); j++) {
    // Simplified FFT processing for Vitis compatibility
    output = instream;

    for (int i = 0; i < F_N; i++) {
      outref >> ref_float;
      output_float = output.real();
      Nsam_it++;
      
      noise_pow = noise_pow + (((output_float - ref_float) * (output_float - ref_float)) - noise_pow) / (increment ? 1 : 2);
      sig_pow = sig_pow + ((ref_float * ref_float) - sig_pow) / (increment ? 1 : 2);

      outref >> ref_float;
      output_float = output.imag();
      Nsam_it++;
      noise_pow = noise_pow + (((output_float - ref_float) * (output_float - ref_float)) - noise_pow) / (increment ? 1 : 2);
      sig_pow = sig_pow + ((ref_float * ref_float) - sig_pow) / (increment ? 1 : 2);

      outf << setprecision(32) << output.real() << endl;
      outf << setprecision(32) << output.imag() << endl;
    }
  }

  SQNR = 10 * log10(sig_pow / noise_pow);
}

void read_input(fstream &inf, dif_inp &instream, int &Nsam) {
  dif_inp input;
  float input_float;
  Nsam = 0;

  while (!inf.eof()) {
    inf >> input_float;
    input.real(input_float);
    inf >> input_float;
    input.imag(input_float);
    instream = input;
    Nsam++;
  }
  Nsam--;
}
