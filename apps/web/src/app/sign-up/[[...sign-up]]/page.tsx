'use client';

import Link from 'next/link';
import { SignUp } from '@clerk/nextjs';
import { Sparkles } from 'lucide-react';
import { isClerkConfigured } from '@/lib/auth';

export default function SignUpPage() {
  if (!isClerkConfigured) {
    return (
      <main className="min-h-screen flex items-center justify-center bg-bg px-6">
        <div className="max-w-md w-full text-center">
          <div className="mx-auto mb-6 w-12 h-12 rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center shadow-sm">
            <Sparkles size={22} className="text-white" />
          </div>
          <h1 className="text-2xl font-semibold text-ink-900">Fondok AI</h1>
          <p className="mt-2 text-sm text-ink-700">
            Sign-up is not configured for this deployment.
          </p>
          <p className="mt-1 text-sm text-ink-500">
            The app is running in demo mode — explore as <span className="font-medium text-ink-700">Eshan Mehta</span> at Brookfield Real Estate.
          </p>
          <Link
            href="/dashboard"
            className="mt-6 inline-flex items-center justify-center px-4 py-2 rounded-md bg-brand-500 hover:bg-brand-700 text-white text-sm font-medium transition-colors"
          >
            Continue to dashboard
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-bg px-6 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-4 w-12 h-12 rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center shadow-sm">
            <Sparkles size={22} className="text-white" />
          </div>
          <h1 className="text-2xl font-semibold text-ink-900">Fondok AI</h1>
          <p className="mt-1.5 text-sm text-ink-500">
            Request access for your investment team
          </p>
        </div>
        <div className="rounded-xl border border-border bg-white shadow-sm p-1">
          <SignUp
            appearance={{
              elements: {
                rootBox: 'w-full',
                card: 'shadow-none bg-transparent border-0',
                headerTitle: 'text-base font-semibold text-ink-900',
                headerSubtitle: 'text-xs text-ink-500',
                formButtonPrimary:
                  'bg-brand-500 hover:bg-brand-700 text-white rounded-md font-medium',
                footerActionLink: 'text-brand-500 hover:text-brand-700 font-medium',
              },
            }}
            signInUrl="/sign-in"
          />
        </div>
      </div>
    </main>
  );
}
