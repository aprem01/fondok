#!/usr/bin/env node
// Launch-day: remove Fondok staff's standing memberships from every CLIENT firm.
//
// Dry-run by default; pass --apply to remove. Reads the firm registry from the
// worker so internal (Fondok) organizations are skipped and each removal is
// written to the firm's own audit log (support.staff_left).
//
//   CLERK_SECRET_KEY=sk_... WORKER_URL=https://... WORKER_TOKEN=<staff worker JWT> \
//     node scripts/remove-staff-memberships.mjs [--apply] [--keep org_xxx ...]
//
// Staff = users whose Clerk public_metadata.fondok_staff is set, or whose email
// is listed in --staff a@fondok.ai,b@fondok.ai. --keep org ids are never touched.
// Never prints secrets. Exit code 1 if any removal failed or went unlogged.

const args = process.argv.slice(2);
const apply = args.includes('--apply');
const keep = new Set();
let staffEmails = new Set();
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--keep' && args[i + 1]) keep.add(args[++i]);
  if (args[i] === '--staff' && args[i + 1]) staffEmails = new Set(args[++i].split(',').map((s) => s.trim().toLowerCase()));
}
const { CLERK_SECRET_KEY, WORKER_URL, WORKER_TOKEN } = process.env;
if (!CLERK_SECRET_KEY) die('CLERK_SECRET_KEY is required');
if (!WORKER_URL || !WORKER_TOKEN) die('WORKER_URL and WORKER_TOKEN (a Fondok staff worker JWT) are required so removals are logged');

const clerk = async (path, init = {}) => {
  const res = await fetch(`https://api.clerk.com/v1${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${CLERK_SECRET_KEY}`, 'content-type': 'application/json', ...(init.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`Clerk ${init.method ?? 'GET'} ${path} -> ${res.status}`);
  return res.status === 204 ? null : res.json();
};
const worker = async (path, init = {}) =>
  fetch(`${WORKER_URL.replace(/\/+$/, '')}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${WORKER_TOKEN}`, 'content-type': 'application/json' },
  });

function die(msg) {
  console.error(msg);
  process.exit(2);
}

const reg = await worker('/admin/tenants');
if (!reg.ok) die(`worker /admin/tenants -> ${reg.status} (token must be a Fondok staff admin)`);
const registry = await reg.json();
if (registry.scope !== 'all') die('worker token is not a Fondok admin (scope != all)');
const firms = registry.tenants.filter((t) => t.kind === 'external' && !keep.has(t.clerk_org_id));
console.log(`${apply ? 'APPLY' : 'DRY RUN'}: ${firms.length} client firm(s); keeping ${keep.size} org(s) untouched`);

let failures = 0;
let removed = 0;
for (const firm of firms) {
  const page = await clerk(`/organizations/${firm.clerk_org_id}/memberships?limit=100`);
  for (const m of page.data ?? []) {
    const uid = m.public_user_data?.user_id;
    const email = (m.public_user_data?.identifier ?? '').toLowerCase();
    if (!uid) continue;
    const user = await clerk(`/users/${uid}`);
    const isStaff = Boolean(user.public_metadata?.fondok_staff) || staffEmails.has(email);
    if (!isStaff) continue;
    console.log(`${firm.name}: ${email} (${m.role})${apply ? ' -> remove' : ' would be removed'}`);
    if (!apply) continue;
    try {
      await clerk(`/organizations/${firm.clerk_org_id}/memberships/${uid}`, { method: 'DELETE' });
      removed++;
    } catch (e) {
      failures++;
      console.error(`  FAILED: ${e.message}`);
      continue;
    }
    const log = await worker('/admin/support-access/membership-event', {
      method: 'POST',
      body: JSON.stringify({ tenant_id: firm.id, joined: false, user_id: uid, email }),
    });
    if (!log.ok) {
      failures++;
      console.error(`  removed but NOT logged in ${firm.name}'s audit log (worker ${log.status})`);
    }
  }
}
console.log(`${apply ? `removed ${removed}` : 'nothing changed'}; failures: ${failures}`);
process.exit(failures ? 1 : 0);
