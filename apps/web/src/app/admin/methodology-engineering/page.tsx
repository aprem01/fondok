import { notFound } from 'next/navigation';
import EngineeringMethodology from '@/components/methodology/EngineeringMethodology';

// The engineering reference behind /methodology — engine field paths, API
// routes, environment variables, repository paths, reason codes. Gated exactly
// like /diag: when NEXT_PUBLIC_SHOW_DIAG is not 'true' the route is a 404, so a
// client deployment never serves developer vocabulary at a guessable URL.
const DIAG_ENABLED = process.env.NEXT_PUBLIC_SHOW_DIAG === 'true';

export default function MethodologyEngineeringPage() {
  if (!DIAG_ENABLED) notFound();
  return <EngineeringMethodology />;
}
