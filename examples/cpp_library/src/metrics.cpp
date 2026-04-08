#include "../include/pipeline.hpp"

namespace metrics {

int compute_total(int base, int modifier) {
    return base + modifier;
}

int normalize(int total) {
    return total / 2;
}

}  // namespace metrics

