from fastapi import FastAPI
from api.core.settings import get_settings
from api.routes.chat import router as chat_router


settings = get_settings()

app = FastAPI(title="Sahrdaya RAG API", version="0.1.0")
app.include_router(chat_router)
