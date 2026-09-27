import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const target = process.env.MAX_WEBHOOK_PORT || "8080";

export default defineConfig({
  base: "/max/app/",
  plugins: [react()],
  server: {
    proxy: {
      "/max/app/state": `http://localhost:${target}`,
      "/max/app/set": `http://localhost:${target}`,
    },
  },
});
