import os

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # reads backend/.env into the program's environment

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/config-check")
def config_check():
    names = [
        "SNAPTRADE_CLIENT_ID",
        "SNAPTRADE_CLIENT_SECRET",
        "REDIRECT_URI",
        "SESSION_SECRET",
    ]
    # True/False only. This must never print the real values.
    return {name: bool(os.getenv(name)) for name in names}