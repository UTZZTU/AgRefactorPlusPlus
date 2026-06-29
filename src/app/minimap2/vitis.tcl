open_project csyn
set_top mm_chain_dp_hls
add_files "gemini_1.cpp" -cflags " -O3 -D XILINX "
open_solution -flow_target vitis solution
set_part xcu200-fsgd2104-2-e
create_clock -period 200MHz -name default
csynth_design
close_project
exit