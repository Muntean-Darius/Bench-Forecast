import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"IBM Plex Mono"', 'ui-monospace', 'Menlo', 'monospace'],
      },
      colors: {
        // Tech-utility palette (Datadog / GitHub posture)
        surface: {
          DEFAULT: '#ffffff',
          muted: '#f9fafb',
        },
        border: {
          DEFAULT: '#e5e7eb',
          strong: '#d1d5db',
        },
        // Financial semantic colors
        margin: {
          healthy: '#16a34a',   // green-700
          warning: '#ca8a04',   // yellow-600
          danger:  '#dc2626',   // red-600
        },
      },
      fontSize: {
        // Tabular numerics feel
        num: ['0.8125rem', { lineHeight: '1.25rem', fontWeight: '500' }],
      },
    },
  },
  plugins: [],
} satisfies Config
