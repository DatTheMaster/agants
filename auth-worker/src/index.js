// Agants auth worker — CF Workers + D1
// Routes:
//   POST /register         open — create account, returns api_key (one-time)
//   GET  /me?key=<key>     open — user info + W/L/D record
//   POST /validate         internal (X-Internal-Secret) — api_key → user row
//   POST /hide-record      authed (Authorization: Bearer <api_key>) — toggle hide_record
//   POST /match            internal (X-Internal-Secret) — write completed match result

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

function err(msg, status = 400) {
  return json({ error: msg }, status);
}

function isInternal(request, env) {
  return request.headers.get("X-Internal-Secret") === env.INTERNAL_SECRET;
}

async function getUser(db, key) {
  return db.prepare("SELECT * FROM users WHERE api_key = ?").bind(key).first();
}

async function wld(db, userId) {
  const rows = await db.prepare(`
    SELECT
      SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) AS wins,
      SUM(CASE WHEN winner_id != ? AND winner_id IS NOT NULL
               AND (red_agent_id = ? OR blue_agent_id = ?) THEN 1 ELSE 0 END) AS losses,
      SUM(CASE WHEN winner_id IS NULL
               AND (red_agent_id = ? OR blue_agent_id = ?) THEN 1 ELSE 0 END) AS draws
    FROM matches
    WHERE red_agent_id = ? OR blue_agent_id = ?
  `).bind(userId, userId, userId, userId, userId, userId, userId, userId).first();
  return {
    wins:   rows?.wins   ?? 0,
    losses: rows?.losses ?? 0,
    draws:  rows?.draws  ?? 0,
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    if (method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Internal-Secret",
        },
      });
    }

    // POST /register
    if (path === "/register" && method === "POST") {
      let body;
      try { body = await request.json(); } catch { return err("invalid JSON"); }
      const { username } = body;
      if (!username) return err("username required");
      if (!/^[a-zA-Z0-9_-]{3,32}$/.test(username))
        return err("username must be 3–32 chars, letters/numbers/_/-");
      const apiKey = crypto.randomUUID();
      try {
        await env.DB.prepare(
          "INSERT INTO users (username, api_key) VALUES (?, ?)"
        ).bind(username.trim(), apiKey).run();
      } catch (e) {
        if (e.message?.includes("UNIQUE")) return err("username already taken", 409);
        return err("registration failed", 500);
      }
      return json({ ok: true, username, api_key: apiKey });
    }

    // GET /me?key=<api_key>
    if (path === "/me" && method === "GET") {
      const key = url.searchParams.get("key");
      if (!key) return err("key required");
      const user = await getUser(env.DB, key);
      if (!user) return err("invalid key", 401);
      const record = user.hide_record ? null : await wld(env.DB, user.id);
      return json({
        id:           user.id,
        username:     user.username,
        created_at:   user.created_at,
        hide_record:  !!user.hide_record,
        record,
      });
    }

    // POST /validate  (internal — game server calls this on join_seat)
    if (path === "/validate" && method === "POST") {
      if (!isInternal(request, env)) return err("forbidden", 403);
      let body;
      try { body = await request.json(); } catch { return err("invalid JSON"); }
      const user = await getUser(env.DB, body.api_key);
      if (!user) return err("invalid api_key", 401);
      return json({ ok: true, id: user.id, username: user.username });
    }

    // POST /hide-record  (authed via Bearer api_key)
    if (path === "/hide-record" && method === "POST") {
      const auth = request.headers.get("Authorization") || "";
      const key  = auth.startsWith("Bearer ") ? auth.slice(7) : null;
      if (!key) return err("authorization required", 401);
      const user = await getUser(env.DB, key);
      if (!user) return err("invalid key", 401);
      const toggle = user.hide_record ? 0 : 1;
      await env.DB.prepare("UPDATE users SET hide_record = ? WHERE id = ?")
        .bind(toggle, user.id).run();
      return json({ ok: true, hide_record: !!toggle });
    }

    // POST /match  (internal — game server calls on game over)
    if (path === "/match" && method === "POST") {
      if (!isInternal(request, env)) return err("forbidden", 403);
      let body;
      try { body = await request.json(); } catch { return err("invalid JSON"); }
      const { match_id, red_user_id, blue_user_id, winner_user_id, ticks, ended_at, result_path } = body;
      if (!match_id) return err("match_id required");
      try {
        await env.DB.prepare(`
          INSERT OR REPLACE INTO matches
            (id, red_agent_id, blue_agent_id, winner_id, ticks, ended_at, result_path)
          VALUES (?, ?, ?, ?, ?, ?, ?)
        `).bind(match_id, red_user_id ?? null, blue_user_id ?? null,
                winner_user_id ?? null, ticks ?? null,
                ended_at ?? null, result_path ?? null).run();
      } catch (e) {
        return err("db error: " + e.message, 500);
      }
      return json({ ok: true });
    }

    return err("not found", 404);
  },
};
