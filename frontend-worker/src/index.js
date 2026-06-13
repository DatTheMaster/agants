const BLOCKED = new Set(["/wrangler.toml", "/package.json", "/package-lock.json"]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (BLOCKED.has(path) || path.startsWith("/.wrangler")) {
      return new Response("Not Found", { status: 404 });
    }
    if (path === "/" || path === "") {
      return Response.redirect(new URL("/landing", url).href, 302);
    }
    if (path === "/game") {
      return Response.redirect(new URL("/game/", url).href, 302);
    }

    // env.ASSETS.fetch returns 404 for missing files (favicon, etc.)
    return env.ASSETS.fetch(request);
  },
};
