import Hero from '@/components/landing/Hero';
import Features from '@/components/landing/Features';
import VarianceSnapshot from '@/components/landing/VarianceSnapshot';
import AgentStack from '@/components/landing/AgentStack';
import LandingFooter from '@/components/landing/LandingFooter';

// Public landing page. /dashboard remains the demo entry for first-click users.
// AppShell (sidebar + chrome) wraps this — the landing renders inside the main
// content area, so the sidebar stays available for one-click navigation to /dashboard.
export default function Home() {
  return (
    <div className="bg-bg">
      <Hero />
      <Features />
      <VarianceSnapshot />
      <AgentStack />
      <LandingFooter />
    </div>
  );
}
