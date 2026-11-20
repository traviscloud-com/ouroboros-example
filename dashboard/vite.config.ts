import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { visualizer } from "rollup-plugin-visualizer";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // `npm run analyze` (ANALYZE=1): treemap of the bundle in dist/stats.html (task 4.14).
    process.env.ANALYZE ? visualizer({ filename: "dist/stats.html", gzipSize: true, template: "treemap" }) : null,
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      output: {
        /* Bundle budget (task 4.14): keep the heavy, page-specific libraries out of the
           entry chunk so they load with the first route that needs them. Everything
           else (React, router, Base UI, the shell) stays in the entry chunk. */
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          // React, the router and the Base UI primitives: one stable vendor chunk the
          // entry and every lazy chunk import (keeps React out of the recharts chunk).
          if (/node_modules\/(react|react-dom|scheduler|react-is|react-router|react-router-dom|@base-ui|@floating-ui|cn|class-variance-authority|clsx|tailwind-merge)\//.test(id)) return "vendor";
          if (/node_modules\/(recharts|victory-vendor|d3-[a-z-]+|@reduxjs|immer|reselect|es-toolkit|decimal\.js-light|eventemitter3|fast-equals|internmap|react-redux|redux)\//.test(id)) return "recharts";
          if (/node_modules\/(react-day-picker|date-fns|@date-fns)\//.test(id)) return "calendar";
          if (/node_modules\/@tanstack\//.test(id)) return "table";
          return undefined;
        },
      },
    },
  },
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:8000",
    },
  },
});
