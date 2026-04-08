export function formatSummary(summary: { totalUsers: number; activeUsers: number }): string {
  return `${summary.activeUsers}/${summary.totalUsers} active`;
}

