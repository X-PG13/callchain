# Examples

This directory contains example projects meant for real user workflows rather than parser fixtures.

## Example Map

| Example | Primary language(s) | Scenario | First command |
| --- | --- | --- | --- |
| `smoke_repo` | Python | First-run sanity check and packaged-install smoke | `callchain analyze examples/smoke_repo` |
| `python_service` | Python | Layered backend service with handlers, services, repositories, and tests | `callchain analyze examples/python_service` |
| `ts_dashboard` | TypeScript | Frontend-style screen, API client, and formatting flow | `callchain analyze examples/ts_dashboard --lang typescript` |
| `cpp_library` | C++ | Tiny library-style header/implementation/orchestration layout | `callchain analyze examples/cpp_library --lang cpp` |

## Recommended Path

1. Start with `smoke_repo` to verify the CLI and output formats.
2. Move to `python_service` if you want to inspect layered Python call flow.
3. Move to `ts_dashboard` if you want to inspect a small frontend-style TypeScript flow.
4. Move to `cpp_library` if you want to inspect C++ cross-file resolution.

## Packaged-install Validation

```bash
python -m build
python scripts/install_smoke.py --dist-dir dist --example examples/smoke_repo
```

That install-smoke flow creates a fresh virtual environment, installs the built artifact, runs `callchain --version`, analyzes the example project, and verifies both JSON and HTML reports.
