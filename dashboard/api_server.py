import json
import os
import time
from collections import deque

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

API_KEY = os.getenv("SNIPER_API_KEY", "sniper-local-2026")
STATE_FILE = "/dev/shm/sniper_state.json"
CMD_DIR = "/dev/shm/sniper_cmd"
LOG_FILE = "sniper.log"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
ALLOWED_ORIGINS = os.getenv("SNIPER_DASHBOARD_ORIGINS", "http://127.0.0.1:8000").split(",")
ALLOWED_COMMANDS = frozenset({"/pause", "/resume", "/panic", "/recover_halt"})

app = FastAPI(title="Sniper AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()],
    allow_headers=["*"],
    allow_methods=["*"],
)


class Command(BaseModel):
    action: str = Field(min_length=1, max_length=64)


def verify_key(req: Request):
    if req.headers.get("X-API-Key") != API_KEY:
        raise HTTPException(401, "Unauthorized")


@app.get("/")
def index():
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(path):
        raise HTTPException(404, "Dashboard HTML not found")
    return FileResponse(path)


@app.get("/api/v1/health")
def health():
    if os.path.exists(STATE_FILE):
        age = time.time() - os.path.getmtime(STATE_FILE)
        return {
            "status": "ok",
            "state_age_s": round(age, 1),
            "alive": age < 30,
        }
    return {"status": "degraded", "state_age_s": None, "alive": False}


@app.get("/api/v1/state")
def get_state(_=Depends(verify_key)):
    if not os.path.exists(STATE_FILE):
        raise HTTPException(503, "State not available")
    with open(STATE_FILE) as f:
        data = json.load(f)
    data["state_age_s"] = round(time.time() - os.path.getmtime(STATE_FILE), 1)
    return data


@app.get("/api/v1/logs")
def get_logs(lines: int = 50, _=Depends(verify_key)):
    lines = max(1, min(int(lines), 500))
    try:
        if not os.path.exists(LOG_FILE):
            return {"lines": []}
        with open(LOG_FILE, encoding="utf-8", errors="replace") as handle:
            return {"lines": [line.rstrip("\n") for line in deque(handle, maxlen=lines)]}
    except Exception as e:
        raise HTTPException(502, f"Log tail failed: {e}")


@app.post("/api/v1/command")
def send_command(cmd: Command, _=Depends(verify_key)):
    action = cmd.action.strip()
    if action not in ALLOWED_COMMANDS:
        raise HTTPException(400, "Command not allowed")
    os.makedirs(CMD_DIR, exist_ok=True)
    path = os.path.join(CMD_DIR, "command.json")
    data = {"commands": [{"action": action, "ts": time.time()}]}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return {"ok": True, "action": action}
