'use client';

import { SignUp } from '@clerk/nextjs';
import { isClerkConfigured } from '@/lib/auth';
import AuthFrame, { clerkAppearance } from '@/components/landing/AuthFrame';
import { REQUEST_ACCESS_HREF } from '@/components/landing/requestAccess';

// Sign-up is invitation-only (Clerk sign_up_mode: restricted). This page
// exists for invitation links; anyone else is pointed to Request access.
export default function SignUpPage() {
  if (!isClerkConfigured || typeof SignUp !== 'function') {
    return (
      <AuthFrame subtitle="Request access">
        <div className="p-6 text-center">
          <p className="text-[14px] leading-[1.6] text-[#4a5b55]">
            Fondok onboards each firm directly. Your team then joins by invitation.
          </p>
          <a
            href={REQUEST_ACCESS_HREF}
            className="mt-5 inline-flex items-center justify-center rounded-md bg-[#0b5d45] px-4 py-2.5 text-[14px] font-semibold text-white hover:bg-[#094d3a]"
          >
            Request access for your firm
          </a>
        </div>
      </AuthFrame>
    );
  }

  return (
    <AuthFrame subtitle="Join your firm on Fondok">
      <SignUp appearance={{ ...clerkAppearance, elements: { ...clerkAppearance.elements, header: 'hidden' } }} signInUrl="/sign-in" />
      <p className="px-6 pb-5 text-center text-[12.5px] text-[#6b7c76]">
        Accounts are by invitation. No invitation?{' '}
        <a href={REQUEST_ACCESS_HREF} className="font-medium text-[#0b5d45] hover:underline">
          Request access for your firm
        </a>
        .
      </p>
    </AuthFrame>
  );
}
