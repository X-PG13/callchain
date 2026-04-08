#pragma once

namespace metrics {

int compute_total(int base, int modifier);
int normalize(int total);

}  // namespace metrics

namespace pipeline {

int run_pipeline(int base, int modifier);

}  // namespace pipeline

