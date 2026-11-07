import { Inter, JetBrains_Mono, Source_Serif_4 } from 'next/font/google';

// Loaded only by the public home page. The app itself still renders on the
// system stack declared in tailwind.config.ts.
export const display = Source_Serif_4({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  style: ['normal', 'italic'],
  display: 'swap',
  variable: '--font-home-display',
});

export const body = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-home-body',
});

export const mono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  display: 'swap',
  variable: '--font-home-mono',
});
