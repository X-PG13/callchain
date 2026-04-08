# Smoke Repo

This example project is the default install-smoke target for CallChain.

It is deliberately small:

- `app/__init__.py` re-exports a function
- `app/math_ops.py` contains a tiny call chain
- `tests/test_math_ops.py` acts as a cross-file caller

Expected behavior:

- language detection should report Python
- the analysis report should contain at least one file, function, edge, and chain
- parse errors should stay empty

Useful commands:

```bash
callchain analyze examples/smoke_repo
callchain analyze examples/smoke_repo --format json --output build/examples-smoke.json
callchain analyze examples/smoke_repo --format html --output build/examples-smoke.html
```

If you want a more realistic walkthrough after the first smoke run, continue with:

- `examples/python_service` for a layered Python backend
- `examples/ts_dashboard` for a TypeScript frontend-style flow
- `examples/cpp_library` for a tiny C++ library layout
