"""Read-mostly local FastAPI service over the agent repository (see docs/UI_API.md).

Import-safe without FastAPI installed: the heavy imports live in `agent_repo.api.app`, imported
lazily by `main()`. This keeps the base `agent_repo` package importable without the `ui` extra.

Startup rules:
- The graph target is chosen here, once, from the command line only — never from a request.
- Default target is a graph file (``.\\agent-graph.json``); an existing file is loaded, otherwise
  an in-memory graph is started and saved back after the first write.
- Aura is used ONLY with an explicit ``--use-aura`` flag. Without it, a configured ``NEO4J_URI``
  is ignored so the API can never write to Aura by accident.
"""

from __future__ import annotations

from pathlib import Path

LOCAL_HOST = "127.0.0.1"
DEFAULT_GRAPH_FILE = ".\\agent-graph.json"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m agent_repo.api",
                                     description="Run the local read-mostly agent-repo API.")
    parser.add_argument("--graph-file", default=DEFAULT_GRAPH_FILE,
                        help=f"Graph JSON file to load/save (default {DEFAULT_GRAPH_FILE}).")
    parser.add_argument("--use-aura", action="store_true",
                        help="Use the configured Aura/Neo4j backend (requires NEO4J_URI). "
                             "Without this flag Aura is never used.")
    parser.add_argument("--host", default=LOCAL_HOST, help="Must stay 127.0.0.1 (local only).")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind on 127.0.0.1.")
    parser.add_argument("--max-upload-bytes", type=int, default=None,
                        help="Attachment size limit for POST /ask (default 1 MB).")
    args = parser.parse_args(argv)

    if args.host != LOCAL_HOST:
        print(f"Error: refusing to bind to {args.host!r}; this local API only binds to {LOCAL_HOST}.")
        return 2

    from agent_repo.api.app import DEFAULT_MAX_UPLOAD_BYTES, create_app
    from agent_repo.config import Settings
    from agent_repo.graph.neo4j_store import Neo4jGraph
    from agent_repo.graph.store import InMemoryGraph

    settings = Settings.from_env()
    max_upload = args.max_upload_bytes or DEFAULT_MAX_UPLOAD_BYTES

    path = Path(args.graph_file).expanduser() if args.graph_file else None

    def store_provider():
        if args.use_aura:
            if settings.neo4j is None:
                raise RuntimeError("--use-aura requires NEO4J_URI to be configured.")
            return Neo4jGraph(settings.neo4j)
        if path is not None:
            return InMemoryGraph.load(path) if path.is_file() else InMemoryGraph()
        return InMemoryGraph()

    if args.use_aura:
        if settings.neo4j is None:
            print("Error: --use-aura requires NEO4J_URI to be configured.")
            return 2
        target = "Aura"
    elif path is not None:
        target = f"file {path}"
    else:
        target = "in-memory"

    store_holder: dict[str, object] = {}

    def provider_with_capture():
        store = store_provider()
        store_holder["store"] = store
        return store

    app = create_app(provider_with_capture, target=target, settings=settings,
                     max_upload_bytes=max_upload)

    import uvicorn

    try:
        uvicorn.run(app, host=LOCAL_HOST, port=args.port, log_level="info")
    finally:
        store = store_holder.get("store")
        if path is not None and not args.use_aura and isinstance(store, InMemoryGraph):
            store.save(path)
        if args.use_aura and isinstance(store, Neo4jGraph):
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
