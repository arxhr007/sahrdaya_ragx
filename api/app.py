from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.core.settings import get_settings
from api.routes.chat import router as chat_router


settings = get_settings()

app = FastAPI(title="Sahrdaya RAG API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins() or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat_router)
