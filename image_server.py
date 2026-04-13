"""
Image Generation Server — run this on your PC (4070 Ti)
========================================================
Install deps:
    pip install fastapi uvicorn diffusers torch accelerate transformers

Run:
    python image_server.py

Then set IMAGE_SERVER_URL=http://<your-pc-ip>:8001 in the CRM backend .env
"""

import io
import base64
import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Image Generation Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Load model at startup ──────────────────────────────────────────────────────
print("Loading SDXL-Turbo model (first run downloads ~6 GB)...")

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype  = torch.float16 if device == "cuda" else torch.float32

from diffusers import AutoPipelineForText2Image

pipe = AutoPipelineForText2Image.from_pretrained(
    "stabilityai/sdxl-turbo",
    torch_dtype=dtype,
    variant="fp16" if device == "cuda" else None,
)
pipe = pipe.to(device)

print(f"Model loaded on {device}. Server ready.")


# ── Schemas ────────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = "blurry, low quality, watermark, text"
    steps: int = 4          # SDXL-Turbo works well with 1-4 steps
    width: int = 512
    height: int = 512
    seed: int = -1          # -1 = random


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "device": device}


@app.post("/generate")
def generate(req: GenerateRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")

    generator = None
    if req.seed >= 0:
        generator = torch.Generator(device=device).manual_seed(req.seed)

    try:
        result = pipe(
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            num_inference_steps=req.steps,
            guidance_scale=0.0,     # SDXL-Turbo uses 0.0
            width=req.width,
            height=req.height,
            generator=generator,
        )
        image = result.images[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    return {
        "image_base64": b64,
        "format": "png",
        "width": req.width,
        "height": req.height,
        "prompt": req.prompt,
    }


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
