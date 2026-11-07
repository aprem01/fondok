'use client';

import Link from 'next/link';
import { SignIn } from '@clerk/nextjs';
import { isClerkConfigured } from '@/lib/auth';
import AuthFrame, { clerkAppearance } from '@/components/landing/AuthFrame';

export default function SignInPage() {
  // Treat a missing Clerk export the same as "not configured" so a
  // partially-loaded SDK doesn't render <undefined> and crash with #130.
  // Demo mode (no auth backend) exists only in local development; on the
  // production deploy the middleware refuses every app route instead.
  if (!isClerkConfigured || typeof SignIn !== 'function') {
    return (
      <AuthFrame subtitle="Welcome back">
        <div className="p-6 text-center">
          <p className="text-[14px] text-[#4a5b55]">Local demo mode: there is no sign-in on this copy.</p>
          <Link
            href="/dashboard"
            className="mt-5 inline-flex items-center justify-center rounded-md bg-[#0b5d45] px-4 py-2.5 text-[14px] font-semibold text-white hover:bg-[#094d3a]"
          >
            Continue to dashboard
          </Link>
        </div>
      </AuthFrame>
    );
  }

  return (
    <AuthFrame subtitle="Sign in to Fondok">
      {/* No sign-up link: accounts are created by invitation only. */}
      <SignIn appearance={{ ...clerkAppearance, elements: { ...clerkAppearance.elements, footerAction: 'hidden', header: 'hidden' } }} fallbackRedirectUrl="/dashboard" />
    </AuthFrame>
  );
}
