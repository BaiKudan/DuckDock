/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    // 配色统一:主色 = indigo(见 main.tsx 的 AntD colorPrimary + index.css 的 indigo-600);
    // 不在 @apply 里用自定义色 token,避免 dev server 不重载 config 时报 "class does not exist"。
    extend: {},
  },
  plugins: [],
};
