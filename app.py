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
IMAGE_TIMEOUT = 8.0
PLAYER_INFO_URL = "https://sheihk-anamul-info-ob53.vercel.app/player-info"
ICON_API_BASE = "https://iconapi.wasmer.app/"

# المواضع الثمانية الصحيحة (تم جمعها من الكود الذي كان يعمل سابقاً)
POSITIONS = [
    (350, 30),    # 1. head
    (575, 130),   # 2. faceprint
    (665, 350),   # 3. mask
    (575, 550),   # 4. top
    (350, 654),   # 5. bottom
    (135, 570),   # 6. shoe
    (47, 340),    # 7. pet (أو أي عنصر إضافي)
    (135, 130)    # 8. weapon
]

client = httpx.AsyncClient(
    timeout=httpx.Timeout(IMAGE_TIMEOUT),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    follow_redirects=True
)

image_cache = {}
CACHE_TTL = 300

async def fetch_image_cached(item_id):
    if not item_id:
        return None
    now = time.time()
    key = str(item_id)
    if key in image_cache and now - image_cache[key]["ts"] < CACHE_TTL:
        return image_cache[key]["img"]
    url = f"{ICON_API_BASE}{item_id}"
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        img = img.resize((150, 150), Image.LANCZOS)
        image_cache[key] = {"img": img, "ts": now}
        return img
    except Exception:
        image_cache[key] = {"img": None, "ts": now}
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
    return Image.open(bg_path).convert("RGBA")

background = load_background()

@app.get("/")
async def home():
    return {
        "status": "running",
        "message": "Ziko Outfit API - 8 items (Fast&m9awed & Reliable)",
        "usage": "/ziko-outfit-image?key=ziko&uid=YOUR_UID"
    }

@app.get("/ziko-outfit-image")
async def generate_outfit(uid: str = Query(...), key: str = Query(...)):
    if key != API_KEY:
        raise HTTPException(401, "Invalid API key")

    data = await fetch_player_info(uid)
    if not data:
        raise HTTPException(500, "Player info fetch failed")

    # استخراج 6 قطع أولى من clothes
    outfit_ids = data.get("profileInfo", {}).get("clothes", [])[:6]
    pet_id = data.get("petInfo", {}).get("id")
    weapon_list = data.get("basicInfo", {}).get("weaponSkinShows", [])
    weapon_id = weapon_list[0] if weapon_list else None

    # بناء قائمة بـ 8 معرفات (قد يكون بعضها None)
    item_ids = list(outfit_ids)  # أول 6
    item_ids.append(pet_id)      # السابع
    item_ids.append(weapon_id)   # الثامن

    # التأكد من وجود 8 عناصر (ملء None إذا نقص)
    while len(item_ids) < 8:
        item_ids.append(None)

    # جلب الصور بالتوازي
    tasks = [fetch_image_cached(iid) for iid in item_ids]
    images = await asyncio.gather(*tasks)

    # إنشاء اللوحة
    canvas = background.copy()

    # لصق الصور في المواضع المحددة
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