import os
from io import BytesIO
from fastapi import FastAPI, Response, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from PIL import Image
import asyncio
import time

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = "ziko"
BACKGROUND_FILENAME = "outfit.png"
IMAGE_TIMEOUT = 6.0
PLAYER_INFO_URL = "https://sheihk-anamul-info-ob53.vercel.app/player-info"
# استخدام API الأيقونات الأصلي الذي كان يعمل في الكود القديم
ICON_API_BASE = "https://iconapi.wasmer.app/"

# نفس المواضيع التي كانت في الكود القديم (8 مواقع)
POSITIONS = [
    (350, 30), (575, 130), (665, 350),
    (575, 550), (350, 654), (135, 570),
    (47, 340), (135, 130)
]

client = httpx.AsyncClient(
    timeout=httpx.Timeout(IMAGE_TIMEOUT),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    follow_redirects=True
)

image_cache = {}
CACHE_TTL = 300  # 5 دقائق

async def fetch_image_cached(item_id):
    if not item_id:
        return None
    cache_key = str(item_id)
    now = time.time()
    if cache_key in image_cache:
        entry = image_cache[cache_key]
        if now - entry["ts"] < CACHE_TTL:
            return entry["img"]
    url = f"{ICON_API_BASE}{item_id}"
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        img = img.resize((150, 150), Image.LANCZOS)
        image_cache[cache_key] = {"img": img, "ts": now}
        return img
    except Exception:
        image_cache[cache_key] = {"img": None, "ts": now}
        return None

async def fetch_player_info(uid):
    try:
        resp = await client.get(f"{PLAYER_INFO_URL}?uid={uid}")
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None

def load_background():
    bg_path = os.path.join(os.path.dirname(__file__), BACKGROUND_FILENAME)
    bg = Image.open(bg_path).convert("RGBA")
    return bg

background = load_background()

@app.get("/")
async def home():
    return {
        "status": "running",
        "message": "Ziko Outfit API (Fast & Working)",
        "usage": "/ziko-outfit-image?key=ziko&uid=YOUR_UID"
    }

@app.get("/ziko-outfit-image")
async def generate_outfit(uid: str = Query(...), key: str = Query(...)):
    if key != API_KEY:
        raise HTTPException(401, "Invalid API key")

    data = await fetch_player_info(uid)
    if not data:
        raise HTTPException(500, "Player info fetch failed")

    # استخراج العناصر بنفس طريقة الكود القديم
    outfit_ids = data.get("profileInfo", {}).get("clothes", [])[:6]
    pet_id = data.get("petInfo", {}).get("id")
    weapon_list = data.get("basicInfo", {}).get("weaponSkinShows", [])
    weapon_id = weapon_list[0] if weapon_list else None

    item_ids = list(outfit_ids)
    item_ids.append(pet_id)
    item_ids.append(weapon_id)
    while len(item_ids) < 8:
        item_ids.append(None)

    tasks = [fetch_image_cached(iid) for iid in item_ids]
    images = await asyncio.gather(*tasks)

    canvas = background.copy()
    for idx, img in enumerate(images):
        if img is not None and idx < len(POSITIONS):
            canvas.paste(img, POSITIONS[idx], img)

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return Response(content=output.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"})

@app.on_event("shutdown")
async def shutdown():
    await client.aclose()