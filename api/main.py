import uvicorn

from automata_api.config import get_api_config
from automata_api.main import app


if __name__ == "__main__":
    config = get_api_config()
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        http="h11",
        ws="websockets",
        loop="asyncio",
    )
