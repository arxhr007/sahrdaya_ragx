import uvicorn

from api.app import app
from api.core.settings import get_settings


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
