# C++ Library Example

This example imitates a tiny C++ library with a header, implementation, and orchestration layer:

- `include/pipeline.hpp` exposes the public API
- `src/pipeline.cpp` wires calls together
- `src/metrics.cpp` holds calculation helpers

Useful commands:

```bash
callchain analyze examples/cpp_library --lang cpp
callchain analyze examples/cpp_library --only-cross-file
callchain analyze examples/cpp_library --format html --output build/cpp-library.html
```
