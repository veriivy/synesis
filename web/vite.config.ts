import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  // loadEnv rather than process.env, so the config needs no @types/node.
  const env = loadEnv(mode, ".", "VITE_");

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      // The frontend talks to the API through /api in dev, so nothing in the code has
      // to know whether it is running locally or against the Vultr box.
      proxy: {
        "/api": {
          target: env.VITE_API_BASE || "http://localhost:8000",
          changeOrigin: true,
          rewrite: (path: string) => path.replace(/^\/api/, ""),
        },
      },
    },
  };
});
