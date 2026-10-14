import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const target = process.env.EA_WEB_API_TARGET ?? "http://127.0.0.1:8000";
const origin = new URL(target);
if (
  origin.protocol !== "http:" ||
  !["127.0.0.1", "localhost", "[::1]"].includes(origin.hostname) ||
  origin.pathname !== "/" ||
  origin.username ||
  origin.password ||
  origin.search ||
  origin.hash
) {
  throw new Error("EA_WEB_API_TARGET must be a plain HTTP loopback origin");
}

const proxy = Object.fromEntries(
  ["/api", "/health"].map((path) => [path, { target, changeOrigin: false }]),
);
export default defineConfig({
  plugins: [vue()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  build: {
    sourcemap: false,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: "ui", test: /node_modules\/(element-plus|@element-plus)/ },
          ],
        },
      },
    },
  },
});
