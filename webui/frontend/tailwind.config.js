/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {
    colors: { brand: { 400:"#e06a3a",500:"#d4521f",600:"#b8401a",700:"#8f3216" } },
  } },
  plugins: [],
};
