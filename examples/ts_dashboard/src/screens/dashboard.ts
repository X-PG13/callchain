import { fetchSummary } from "../api/client";
import { formatSummary } from "../lib/format";

export function renderDashboard(): string {
  const summary = fetchSummary();
  return formatSummary(summary);
}

