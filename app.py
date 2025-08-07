from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

@app.post("/v1/replicate")
async def replicate(request: Request):
    try:
        data = await request.json()
        s3_bucket = data.get("s3_bucket")
        s3_key = data.get("s3_key")

        if not s3_bucket or not s3_key:
            return JSONResponse(status_code=400, content={"error": "Missing s3_bucket or s3_key"})

        print(f"Received replication request for: {s3_bucket}/{s3_key}")
        return {"message": "Received", "bucket": s3_bucket, "key": s3_key}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
