// Agants frontend configuration
// Middleware (functions/_middleware.js) overrides these at deploy time via CF Pages env vars.
// Static defaults here ensure the site works even if Functions are not active.

window.AGANTS_BACKEND  = window.AGANTS_BACKEND  || "https://api.datthemaster.com/agants";
window.AGANTS_AUTH_URL = window.AGANTS_AUTH_URL || "https://agants-auth.hermesagent424.workers.dev";
window.AGANTS_ADMIN    = window.AGANTS_ADMIN !== undefined
  ? window.AGANTS_ADMIN
  : (location.hostname === "localhost" || location.hostname === "127.0.0.1");
