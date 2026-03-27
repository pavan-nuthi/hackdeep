import os
from fastapi import FastAPI

app = FastAPI()


@app.get("/items")
def get_items():
    """Return a minimal list of items. Dummy target for an AI agent."""
    return {"items": [{"id": "1", "name": "Widget"}]}
