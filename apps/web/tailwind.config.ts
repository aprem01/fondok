import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx,js,jsx,mdx}'],
  theme: {
    extend: {
      colors: {
        bg: '#fafbfc',
        card: '#ffffff',
        border: '#e5e7eb',
        ink: {
          900: '#0f172a',
          700: '#334155',
          500: '#64748b',
          400: '#94a3b8',
          300: '#cbd5e1',
        },
        brand: {
          50: '#eff6ff',
          100: '#dbeafe',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
        success: { 50: '#ecfdf5', 500: '#10b981', 700: '#047857' },
        warn: { 50: '#fffbeb', 500: '#f59e0b', 700: '#b45309' },
        danger: { 50: '#fef2f2', 500: '#ef4444', 700: '#b91c1c' },
      },
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system'] },
      boxShadow: { card: '0 1px 2px rgba(0,0,0,0.04)' },
    },
  },
  plugins: [],
};
export default config;
