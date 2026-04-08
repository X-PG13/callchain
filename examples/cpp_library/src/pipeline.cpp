#include "../include/pipeline.hpp"

namespace pipeline {

int run_pipeline(int base, int modifier) {
    int total = metrics::compute_total(base, modifier);
    return metrics::normalize(total);
}

}  // namespace pipeline
