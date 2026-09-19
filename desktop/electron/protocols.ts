/**
 * The only two places the window can load anything from.
 *
 *   spectra-app://renderer/…   the built UI, from dist/renderer
 *   spectra-media://artifact/<sha256>   a stored artefact of the open case, for the player
 *
 * Neither can reach outside its own directory, and the media scheme serves only files
 * addressed by digest inside the case's content-addressed store — never an evidence path.
 */

import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";

export const APP_SCHEME = "spectra-app";
export const MEDIA_SCHEME = "spectra-media";
export const APP_ORIGIN = `${APP_SCHEME}://renderer`;

const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".json": "application/json",
  ".map": "application/json",
  ".woff2": "font/woff2",
};

/** No network, no inline script, media only from the artefact scheme. */
export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  `media-src ${MEDIA_SCHEME}:`,
  "connect-src 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

function inside(root: string, candidate: string): boolean {
  const rel = path.relative(root, candidate);
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
}

export async function serveRenderer(request: Request, rendererDir: string): Promise<Response> {
  const url = new URL(request.url);
  if (url.host !== "renderer") return new Response(null, { status: 404 });
  const rel = decodeURIComponent(url.pathname).replace(/^\/+/, "") || "index.html";
  const file = path.resolve(rendererDir, rel);
  if (!inside(rendererDir, file)) return new Response(null, { status: 403 });
  try {
    const body = await fs.promises.readFile(file);
    const headers: Record<string, string> = {
      "Content-Type": CONTENT_TYPES[path.extname(file).toLowerCase()] ?? "application/octet-stream",
      "Cache-Control": "no-store",
    };
    if (file.endsWith(".html")) headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY;
    return new Response(new Uint8Array(body), { status: 200, headers });
  } catch {
    return new Response(null, { status: 404 });
  }
}

export async function serveArtifact(request: Request, activeCase: string | null): Promise<Response> {
  const url = new URL(request.url);
  const sha = url.pathname.replace(/^\/+/, "");
  if (url.host !== "artifact" || !/^[0-9a-f]{64}$/.test(sha) || !activeCase) {
    return new Response(null, { status: 404 });
  }
  const store = path.join(activeCase, "artifacts");
  const file = path.join(store, sha.slice(0, 2), sha.slice(2, 4), sha);
  if (!inside(store, file)) return new Response(null, { status: 403 });
  let size: number;
  try {
    size = (await fs.promises.stat(file)).size;
  } catch {
    return new Response(null, { status: 404 });
  }
  const headers: Record<string, string> = {
    "Content-Type": "video/mp4",
    "Accept-Ranges": "bytes",
    "Cache-Control": "no-store",
  };
  if (size === 0) return new Response(null, { status: 200, headers: { ...headers, "Content-Length": "0" } });

  const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get("range")?.trim() ?? "");
  if (range && (range[1] || range[2])) {
    let start: number;
    let end: number;
    if (range[1]) {
      start = Number(range[1]);
      end = range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
    } else {
      start = Math.max(0, size - Number(range[2]));
      end = size - 1;
    }
    if (start > end || start >= size) {
      return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
    }
    return new Response(stream(file, start, end), {
      status: 206,
      headers: { ...headers, "Content-Range": `bytes ${start}-${end}/${size}`,
                 "Content-Length": String(end - start + 1) },
    });
  }
  return new Response(stream(file, 0, size - 1), {
    status: 200, headers: { ...headers, "Content-Length": String(size) },
  });
}

function stream(file: string, start: number, end: number): ReadableStream {
  return Readable.toWeb(fs.createReadStream(file, { start, end })) as ReadableStream;
}
