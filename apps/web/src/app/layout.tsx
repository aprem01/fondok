import type { Metadata } from 'next';
import { ClerkProvider } from '@clerk/nextjs';
import './globals.css';
import AppShell from '@/components/layout/AppShell';

export const metadata: Metadata = {
  title: 'Fondok AI — Hotel Acquisition Underwriting',
  description: 'AI-powered hotel acquisition underwriting. From OM to IC memo in 17 minutes.',
  icons: {
    icon: [
      { url: '/icon.svg', type: 'image/svg+xml' },
      { url: '/favicon.svg', type: 'image/svg+xml' },
    ],
    apple: { url: '/apple-icon.svg', type: 'image/svg+xml' },
  },
};

// Feature flag: real Clerk auth kicks in only when a real publishable key
// is present. The `_dummy` suffix is a sentinel for build-time CI checks
// — any key ending in `_dummy` is treated as unset so the app falls back
// to the demo persona ("Eshan Mehta · Brookfield Real Estate").
const clerkKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const isClerkConfigured =
  !!clerkKey &&
  (clerkKey.startsWith('pk_test_') || clerkKey.startsWith('pk_live_')) &&
  !clerkKey.endsWith('_dummy');

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const tree = (
    <html lang="en">
      <body className="font-sans antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
  return isClerkConfigured ? <ClerkProvider>{tree}</ClerkProvider> : tree;
}
