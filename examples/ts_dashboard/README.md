# TypeScript Dashboard Example

This example imitates a small frontend data flow:

- `src/main.ts` is the page bootstrap
- `src/screens/dashboard.ts` coordinates data loading
- `src/api/client.ts` is the API boundary
- `src/lib/format.ts` turns raw data into UI text

Useful commands:

```bash
callchain analyze examples/ts_dashboard --lang typescript
callchain analyze examples/ts_dashboard --format mermaid --output build/ts-dashboard.md
callchain analyze examples/ts_dashboard --format html --output build/ts-dashboard.html
```
