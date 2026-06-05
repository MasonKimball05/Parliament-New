/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './src/**/*.py',
    './static/js/**/*.js',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Beta Theta Pi royal blue — primary-600 = #003DA5
        primary: {
          50:  '#e8eef9',
          100: '#c5d4f0',
          200: '#9db4e4',
          300: '#6e8fd5',
          400: '#4a72c9',
          500: '#1a52bb',
          600: '#003da5',
          700: '#003090',
          800: '#002275',
          900: '#001455',
        },
        // Beta Theta Pi old gold — gold-400 = #FFC72C
        gold: {
          50:  '#fffbeb',
          100: '#fff3c4',
          200: '#ffe88a',
          300: '#ffd84d',
          400: '#ffc72c',
          500: '#f0b300',
          600: '#cc9800',
          700: '#a37a00',
          800: '#7a5c00',
          900: '#524000',
        },
      },
    },
  },
  safelist: [
    { pattern: /^bg-white\/(10|15|20|25|30)$/ },
    { pattern: /^bg-white\/(10|15|20|25|30)$/, variants: ['hover'] },
  ],
  plugins: [],
}
