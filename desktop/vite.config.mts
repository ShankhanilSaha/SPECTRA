import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The renderer is loaded from disk by Electron (file://), never from a dev server: the
// product opens no listening socket (doc 3 D5, §14). Hence relative asset paths.
export default defineConfig({
  root: "src",
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../dist/renderer",
    emptyOutDir: true,
    target: "chrome130",
    sourcemap: true,
  },
});
