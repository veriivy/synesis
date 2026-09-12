import type { NextConfig } from "next";

/**
 * Note on the API connection: the frontend talks to the Python backend directly at
 * NEXT_PUBLIC_API_BASE, rather than through a Next rewrite.
 *
 * A rewrite would be tidier (same-origin, no CORS), but the whole app hangs off one
 * long-lived SSE connection, and proxying SSE through Next's dev rewrites is a known
 * source of buffered-until-it-isn't behaviour — events arrive in a clump instead of
 * live, which is exactly the thing the demo is showing. Direct + CORS has one failure
 * mode (a missing origin in CORS_ORIGINS) and it fails loudly in the browser console.
 *
 * So: set NEXT_PUBLIC_API_BASE, and add this origin to CORS_ORIGINS in .env.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
