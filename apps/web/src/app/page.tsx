import type { Metadata } from 'next';
import Landing from '@/components/landing/Hero';
import { body, display, mono } from '@/components/landing/fonts';

export const metadata: Metadata = {
  title: 'Fondok | Hotel acquisition underwriting, cited to the source page',
  description:
    'Fondok reads the OM, T-12 and STR reports, builds the hotel model and drafts the IC memo. Every figure traces to the page it came from.',
};

// Public marketing page: a single screen on desktop. AppShell renders `/`
// without the app sidebar, so this page owns the full viewport.
export default function Home() {
  return (
    <div className={`home-page ${display.variable} ${body.variable} ${mono.variable} antialiased`}>
      <Landing />
    </div>
  );
}
