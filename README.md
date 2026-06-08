# Swarm Wars 🐝

Ant Colony RTS for LLMs. Two AI colonies compete on a shared forest floor with visible pheromone trails.

## Run

```bash
cd ~/projects/swarm-wars
python3 server.py
# Open http://localhost:8083
```

## What You're Seeing

- **Dirt/leaves/water/rocks** — procedurally generated forest floor
- **Red & Blue dots** — ant colonies with workers, soldiers, scouts, and a queen
- **Green trails** — foraging routes to food sources (thicker = better food)
- **Red trails** — alarm pheromones marking enemy sightings
- **Gold trails** — territory markers from patrolling soldiers
- **Blue trails** — scout exploration paths
- **Colored squares** — food sources (seeds, beetles, leaves, honeydew)

## Architecture

| File | Role |
|------|------|
| `server.py` | Simulation engine + WebSocket broadcast @ 10 TPS |
| `index.html` | Browser client — Canvas rendering, pheromone visualisation |

## Ant Behaviour

- **Workers** follow foraging trails to food, carry it back to nest
- **Soldiers** patrol territory, respond to alarm pheromones, fight enemies
- **Scouts** explore outward, discover food and enemies, lay trail back to nest
- **Queen** stays in nest, produces workers when colony has food

No LLM yet — purely heuristic behaviour. LLM integration would control colony-level strategy (allocation, expansion, aggression) while ants execute via simple rules.
