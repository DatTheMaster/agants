// Agants frontend configuration
// Override these in a deployment-specific config.js before shipping to Cloudflare Pages.

// Backend URL for API + WebSocket.
// Empty string = same origin (use when serving frontend from the game server directly).
// Set to your cloudflared tunnel URL when deploying frontend separately on Pages.
window.AGANTS_BACKEND = window.AGANTS_BACKEND || "";

// Auth worker URL.  Empty string = auth disabled (open access, no accounts required).
// Set to your agants-auth CF Worker URL when deploying with accounts.
window.AGANTS_AUTH_URL = window.AGANTS_AUTH_URL || "";

// Admin mode — shows the ⚙ Settings panel (for local dev / private installs only).
// Auto-true on localhost; false everywhere else unless explicitly overridden.
window.AGANTS_ADMIN = window.AGANTS_ADMIN !== undefined
  ? window.AGANTS_ADMIN
  : (location.hostname === "localhost" || location.hostname === "127.0.0.1");
