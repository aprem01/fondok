import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx,js,jsx,mdx}'],
  theme: {
    extend: {
      colors: {
        bg: '#fafaf7',          // warm off-white
        card: '#ffffff',
        surface: '#f5f5f0',     // warm tint for nested surfaces
        border: '#e8e6df',      // warmer border
        divider: '#f0ede5',     // soft divider

        ink: {
          900: '#0a0e1a',       // deep near-black navy
          800: '#1a1f2e',
          700: '#2d3548',
          600: '#475467',
          500: '#667085',
          400: '#98a2b3',
          300: '#d0d5dd',
          200: '#eaecf0',
          100: '#f2f4f7',
        },

        // Brand stays institutional blue — primary trust color.
        // Champagne lives in `gold` for premium accent moments.
        brand: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#0d3b66',       // deep institutional navy (was bright blue)
          600: '#0a2e52',
          700: '#082848',
          800: '#061d35',
          900: '#041325',
        },

        // Champagne / gold — used for premium highlights only
        // (IC Ready, Total Deal Volume, premium CTAs, logo accent).
        gold: {
          50: '#f5f0e8',
          100: '#e8dcc4',
          200: '#d4c19a',
          300: '#bea470',
          400: '#a68850',       // primary champagne
          500: '#8a6d35',
          600: '#6e5523',
          700: '#564019',
          800: '#3d2c10',
          900: '#1f1607',
        },

        // Legacy alias so existing `bg-brand-50` / `text-brand-700`
        // call sites remain readable. The `accent` family is reserved
        // for secondary CTAs that should feel deep-navy.
        accent: {
          50: '#eef4f8',
          500: '#0d3b66',
          700: '#082848',
        },

        success: { 50: '#f0f9f4', 500: '#15803d', 600: '#166534', 700: '#14532d' },
        warn:    { 50: '#fefbf3', 500: '#b97309', 600: '#92580c', 700: '#7a4708' },
        danger:  { 50: '#fdf3f3', 500: '#b91c1c', 600: '#991b1b', 700: '#7f1d1d' },
      },

      fontFamily: {
        sans: ['"Inter"', '"SF Pro Display"', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        display: ['"Inter Display"', '"Inter"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"SF Mono"', 'ui-monospace', 'Menlo', 'monospace'],
        serif: ['"Source Serif Pro"', '"Tiempos Headline"', 'Georgia', 'serif'],
      },

      boxShadow: {
        card: '0 1px 3px rgba(10, 14, 26, 0.04), 0 1px 2px rgba(10, 14, 26, 0.03)',
        'card-hover': '0 4px 12px rgba(10, 14, 26, 0.08), 0 2px 4px rgba(10, 14, 26, 0.04)',
        'inset-line': 'inset 0 0 0 1px rgba(10, 14, 26, 0.06)',
        premium: '0 0 0 1px rgba(166, 136, 80, 0.18), 0 8px 24px rgba(10, 14, 26, 0.06)',
        'premium-glow': '0 0 0 1px rgba(166, 136, 80, 0.25), 0 6px 18px rgba(166, 136, 80, 0.18)',
      },

      backgroundImage: {
        'brand-gradient': 'linear-gradient(135deg, #0d3b66 0%, #082848 100%)',
        'gold-gradient': 'linear-gradient(135deg, #bea470 0%, #8a6d35 100%)',
        'subtle-grain': 'radial-gradient(circle at 1px 1px, rgba(10,14,26,0.04) 1px, transparent 0)',
        'card-luxe': 'linear-gradient(180deg, #ffffff 0%, #fafaf7 100%)',
      },
    },
  },
  plugins: [],
};
export default config;
