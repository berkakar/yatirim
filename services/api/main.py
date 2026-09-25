from fastapi import FastAPI

app = FastAPI(title="Yatirim API")


@app.get("/health")
async def health():
    return {"status": "ok"}
