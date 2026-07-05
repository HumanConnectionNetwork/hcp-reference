from fastapi import FastAPI
from app.api.records import router as records_router

app = FastAPI(
    title="HCP Reference Node",
    description="Reference implementation of the Humanitarian Connection Protocol",
    version="0.1.0",
)

app.include_router(records_router)


@app.get("/")
def root():
    return {
        "name": "HCP Reference Node",
        "protocol": "Humanitarian Connection Protocol",
        "version": "0.1.0",
        "description": "This node stores humanitarian observations, not identities.",
    }
