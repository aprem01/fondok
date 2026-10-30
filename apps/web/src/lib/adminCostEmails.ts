/**
 * Who may open /admin/cost — the comma-separated NEXT_PUBLIC_ADMIN_COST_EMAILS
 * allowlist, lower-cased and trimmed. Empty when the variable is unset, so
 * nobody is admitted by default. Lives outside the page module because a
 * Next.js page may export only its component and route config.
 */
export function adminCostEmails(
  raw: string | undefined = process.env.NEXT_PUBLIC_ADMIN_COST_EMAILS,
): string[] {
  return (raw ?? '')
    .split(',')
    .map((e) => e.trim().toLowerCase())
    .filter((e) => e.length > 0);
}
