// Stage: builds the Pixi layer-container graph and mounts the canvas.
// World layers live under one camera-transformed worldContainer; uiLayer is
// screen-space and never camera-transformed. (spec §2)
const { Container } = window.PIXI;

export class Stage {
  constructor() {
    this.world = new Container();        // camera transforms this (M1)
    this.world.isRenderGroup = true;     // v8: GPU-handled transform for cheap pan/zoom
    // Back-to-front layer graph (spec §2.3).
    this.terrainLayer = new Container();       // baked terrain chunks
    this.dirtFoodLayer = new Container();       // nodes (M4)
    this.territoryLayer = new Container();      // colony tint, low-alpha
    this.structureLayer = new Container();      // structures (M2)
    this.groundFxLayer = new Container();       // FX under ants (M4)
    this.antLayer = new Container(); this.antLayer.cullableChildren = true;
    this.airFxLayer = new Container();          // FX above ants (M4)
    this.fogLayer = new Container();            // per-POV fog, over the world
    this.world.addChild(
      this.terrainLayer, this.dirtFoodLayer, this.territoryLayer,
      this.structureLayer, this.groundFxLayer, this.antLayer,
      this.airFxLayer, this.fogLayer);
    // Back-compat alias (M0 referenced fxLayer).
    this.fxLayer = this.airFxLayer;
    this.uiLayer = new Container();      // screen space, never camera-transformed
  }
  attach(app) { app.stage.addChild(this.world, this.uiLayer); }
  mount(app, el) { el.innerHTML = ''; el.appendChild(app.canvas); }
}
