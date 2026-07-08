from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone

app = FastAPI(title="ObserverAI Runner Control API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STARTED_AT = datetime.now(timezone.utc)

@app.get("/health")
def health():
    return {
        "ok": True,
        "status": "online",
        "service": "runner-control",
        "started_at": STARTED_AT.isoformat(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

@app.post("/reconnect")
def reconnect():
    return {
        "ok": True,
        "status": "reconnect_requested",
        "message": "Runner control API is reachable. MT5 reconnect must be handled by runner_main/MT5 client.",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }