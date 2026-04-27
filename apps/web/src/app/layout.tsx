import type { Metadata } from 'next';
import './globals.css';
import Sidebar from '@/components/layout/Sidebar';

export const metadata: Metadata = {
  title: 'Fondok AI — Hotel Acquisition Underwriting',
  description: 'AI-powered hotel acquisition underwriting platform',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 ml-[216px] min-h-screen bg-bg">{children}</main>
        </div>
      </body>
    </html>
  );
}
