#include <cstdlib>
#include <fstream>
#include <string>
extern "C" void vector_add(const int[4], const int[4], int[4]);
void vector_add_reference(const int[4], const int[4], int[4]);
static void write_outcome(const char* path, const char* status, const char* kind, const char* owner, const char* reason) {
    if (path == nullptr || path[0] == '\0') return;
    std::ofstream out(path);
    if (!out) return;
    out << "{\"schema_version\":1,\"status\":\"" << status
        << "\",\"failure_kind\":\"" << kind
        << "\",\"failure_owner\":\"" << owner
        << "\",\"reason_code\":\"" << reason << "\"}\n";
}
int main(int argc, char** argv) {
    const char* outcome_path = argc >= 2 ? argv[1] : nullptr;
    int a[4] = {1,2,3,4}, b[4] = {5,6,7,8}, got[4] = {}, expected[4] = {};
    vector_add(a,b,got); vector_add_reference(a,b,expected);
    for (int i=0;i<4;++i) if (got[i] != expected[i]) {
        write_outcome(outcome_path, "failed", "candidate_rtl_functional_failure", "candidate", "public_rtl_mismatch");
        return 2;
    }
    write_outcome(outcome_path, "passed", "", "none", "cosim_passed");
    return 0;
}
