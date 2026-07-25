from fastapi import FastAPI

app = FastAPI(title="DocTalk")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
