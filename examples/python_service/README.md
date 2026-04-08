# Python Service Example

This example imitates a small layered Python backend:

- `service/http.py` is the entrypoint layer
- `service/handlers.py` coordinates request-level work
- `service/services/users.py` holds business logic
- `service/repositories/users.py` wraps persistence access
- `tests/test_http.py` acts as an external caller

Useful commands:

```bash
callchain analyze examples/python_service
callchain analyze examples/python_service --restrict-dir service
callchain analyze examples/python_service --only-cross-file
callchain analyze examples/python_service --format html --output build/python-service.html
```
