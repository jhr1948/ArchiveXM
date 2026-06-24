/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    screens: {
      'xs': '480px',
      'sm': '640px',
      'md': '768px',
      'lg': '1024px',
      'xl': '1280px',
      '2xl': '1536px',
    },
    extend: {
      colors: {
        'sxm': {
          'dark': '#0a0a0f',
          'darker': '#050508',
          'card': '#12121a',
          'border': '#1e1e2e',
          'accent': '#6366f1',
          'accent-hover': '#818cf8',
          'success': '#22c55e',
          'warning': '#f59e0b',
          'error': '#ef4444'
        }
      }
    },
  },
  plugins: [],
}
