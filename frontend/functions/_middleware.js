// CF Pages middleware: serves /config.js dynamically from env vars.
// Set AGANTS_BACKEND in Pages settings → Environment Variables.
// Set AGANTS_ADMIN = "true" for deployments that should show Settings panel.
export async function onRequest(context) {
  const { request, next, env } = context;
  const url = new URL(request.url);
  if (url.pathname === "/config.js") {
    const backend = (env.AGANTS_BACKEND || "").replace(/"/g, "");
    const admin = env.AGANTS_ADMIN === "true" ? "true" : "false";
    return new Response(
      `window.AGANTS_BACKEND = "${backend}";\nwindow.AGANTS_ADMIN = ${admin};\n`,
      {
        headers: {
          "Content-Type": "application/javascript; charset=utf-8",
          "Cache-Control": "no-store",
        },
      }
    );
  }
  return next();
}
