// Stage: builds the Pixi layer-container graph and mounts the canvas.
// World layers live under one camera-transformed worldContainer; uiLayer is
// screen-space and never camera-transformed. (spec §2)
const { Container } = window.PIXI;

export class Stage {
  constructor() {
    this.world = new Container();        // camera will transform this in M1
    this.world.isRenderGroup = true;     // v8: GPU-handled transform for cheap pan/zoom
    this.terrainLayer = new Container();
    this.structureLayer = new Container();
    this.antLayer = new Container(); this.antLayer.cullableChildren = true;
    this.fxLayer = new Container();
    this.fogLayer = new Container();
    this.world.addChild(this.terrainLayer, this.structureLayer, this.antLayer,
                        this.fxLayer, this.fogLayer);
    this.uiLayer = new Container();      // screen space, never camera-transformed
  }
  attach(app) { app.stage.addChild(this.world, this.uiLayer); }
  mount(app, el) { el.innerHTML = ''; el.appendChild(app.canvas); }
}
