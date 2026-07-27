import sys

if __name__ == "__main__" and sys.argv[1:] == ["--sandbox-file-worker"]:
    from automata_api.agent.execution.sandbox.file_worker import run_file_worker

    raise SystemExit(run_file_worker())

if __name__ == "__main__":
    import uvicorn

    from automata_api.config import get_api_config
    from automata_api.main import app

    config = get_api_config()
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        http="h11",
        ws="websockets",
        loop="asyncio",
    )
