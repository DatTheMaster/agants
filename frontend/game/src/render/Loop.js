// Loop: a single ticker callback that computes interpolation progress `t` from
// the measured tick interval and hands it to onFrame. (spec §6)
// M1+ uses `t` to lerp sprite positions; M0 just drives the callback.
export class Loop {
  start(app, store, onFrame) {
    app.ticker.add(() => {
      const t = store.interp(performance.now());
      onFrame(t);
    });
  }
}
