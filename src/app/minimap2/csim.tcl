open_project csim
set_top csim
add_files "mm_chain_dp_orig.cpp" -cflags " -O3 "
add_files "mm_chain_dp_new.cpp" -cflags " -O3 "
add_files -tb "csim.cpp"
open_solution -flow_target vitis solution
set_part xcu200-fsgd2104-2-e
create_clock -period 200MHz -name default
csim_design
close_project
exit