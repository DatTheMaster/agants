// Connection: resolves the WebSocket URL (live match vs. local replay) and wraps
// the socket with reconnect + message dispatch. Mirrors the existing Canvas
// renderer's protocol handling (see index.html connect()).

export function resolveWsUrl(location, backend) {
  const p = new URLSearchParams(location.search || '');
  if (p.get('replay') === 'true') {
    return p.get('replayUrl') || 'ws://localhost:8765';
  }
  const ws = String(backend || '').replace(/^http/, 'ws');
  const match = p.get('match');
  return match ? `${ws}/ws/${match}` : ws;
}

export class Connection {
  constructor(url, handlers = {}) {
    this.url = url;
    this.handlers = handlers;
    this.ws = null;
    this._opened = false;
  }
  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      if (this._opened) this.handlers.onReset?.();   // reconnect → clear ghosts
      this._opened = true;
    };
    this.ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === 'map' || m.type === 'init' || m.map) this.handlers.onMap?.(m);
      else this.handlers.onTick?.(m);   // tick dict (serialize_tick)
    };
    this.ws.onclose = () => setTimeout(() => this.connect(), 1000); // simple reconnect
  }
}
