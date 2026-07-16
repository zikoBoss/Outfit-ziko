import os
import uvicorn
from io import BytesIO
from fastapi import FastAPI, Response, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from PIL import Image
import asyncio
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API_KEY = "ziko"
BACKGROUND_FILENAME = "outfit.png"
IMAGE_TIMEOUT = 20.0
PLAYER_INFO_URL = "https://info.killersharmabot.online/player-info"
ICON_API_BASE = "https://iconapi.wasmer.app/"

REQUIRED_STARTS = ["211", "214", "208", "203", "204", "205"]
FALLBACK_IDS = ["211000000", "214000000", "208000000", "203000000", "204000000", "205000000"]

# إحداثيات العناصر (تم التحديث بدقة)
POSITIONS = [
    (350, 30),    # 0: Head
    (555, 130),   # 1: Facepaint
    (685, 310),   # 2: Back
    (555, 550),   # 3: Bottom
    (360, 654),   # 4: Top
    (165, 560),   # 5: Default
    (40, 310),    # 6: Pet
    (209, 360),   # 7: Weapon
    (160, 130)    # 8: Animation
]

client = httpx.AsyncClient(
    timeout=httpx.Timeout(IMAGE_TIMEOUT),
    limits=httpx.Limits(max_keepalive_connections=30, max_connections=100),
    follow_redirects=True
)

image_cache = {}
CACHE_TTL_SUCCESS = 600
CACHE_TTL_FAIL = 5

async def fetch_image_cached(item_id, retries=2, new_size=(150, 150)):
    if not item_id:
        return None
    now = time.time()
    size_key = f"_{new_size[0]}x{new_size[1]}" if new_size else "_original"
    key = str(item_id) + size_key
    if key in image_cache:
        entry = image_cache[key]
        ttl = CACHE_TTL_SUCCESS if entry["success"] else CACHE_TTL_FAIL
        if now - entry["ts"] < ttl:
            return entry["img"]
    for attempt in range(retries + 1):
        try:
            url = f"{ICON_API_BASE}{item_id}"
            logger.info(f"Fetching {item_id} (attempt {attempt+1})")
            resp = await client.get(url)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content)).convert("RGBA")
            if new_size:
                img = img.resize(new_size, Image.LANCZOS)
            image_cache[key] = {"img": img, "ts": now, "success": True}
            logger.info(f"Success {item_id}")
            return img
        except Exception as e:
            logger.warning(f"Failed {item_id} attempt {attempt+1}: {e}")
            if attempt < retries:
                await asyncio.sleep(0.5)
            else:
                image_cache[key] = {"img": None, "ts": now, "success": False}
                return None

async def fetch_player_info(uid):
    try:
        resp = await client.get(f"{PLAYER_INFO_URL}?uid={uid}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Player info failed: {e}")
        return None

def load_background():
    bg_path = os.path.join(os.path.dirname(__file__), BACKGROUND_FILENAME)
    return Image.open(bg_path).convert("RGBA")

background = load_background()

@app.get("/")
async def home():
    return {"message": "Ziko Outfit API (Stable & Fast)", "usage": "/ziko-outfit-image?key=ziko&uid=UID"}

@app.get("/ziko-outfit-image")
async def generate_outfit(uid: str = Query(...), key: str = Query(...)):
    if key != API_KEY:
        raise HTTPException(401, "Invalid API key")

    data = await fetch_player_info(uid)
    if not data:
        raise HTTPException(500, "Player info fetch failed")

    outfit_ids = data.get("profileInfo", {}).get("clothes", [])
    used_ids = set()
    selected_clothes = []

    for idx, code in enumerate(REQUIRED_STARTS):
        matched = None
        for oid in outfit_ids:
            str_oid = str(oid)
            if str_oid.startswith(code) and str_oid not in used_ids:
                matched = str_oid
                used_ids.add(str_oid)
                break
        if matched is None:
            matched = FALLBACK_IDS[idx]
        selected_clothes.append(matched)

    pet_info = data.get("petInfo", {})
    pet_id = pet_info.get("skinId") or pet_info.get("id")

    weapon_list = data.get("basicInfo", {}).get("weaponSkinShows", [])
    weapon_id = weapon_list[0] if weapon_list else None

    animation_id = data.get("basicInfo", {}).get("equippedAnimationId")

    item_ids = selected_clothes + [pet_id, weapon_id, animation_id]
    while len(item_ids) < 9:
        item_ids.append(None)

    tasks = []
    for idx, iid in enumerate(item_ids):
        if iid is None:
            tasks.append(asyncio.sleep(0, result=None))
        elif idx == 7:
            tasks.append(fetch_image_cached(iid, retries=2, new_size=None))
        else:
            tasks.append(fetch_image_cached(iid, retries=2, new_size=(150, 150)))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    images = []
    for idx, res in enumerate(results):
        if isinstance(res, Exception) or res is None:
            images.append(None)
        else:
            if idx == 7:
                img = res
                target_height = 150
                aspect = img.width / img.height
                new_width = int(target_height * aspect)
                img_resized = img.resize((new_width, target_height), Image.LANCZOS)
                images.append(img_resized)
            else:
                images.append(res)

    canvas = background.copy()
    for idx, img in enumerate(images):
        if img and idx < len(POSITIONS):
            canvas.paste(img, POSITIONS[idx], img)

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return Response(content=output.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"})

@app.on_event("shutdown")
async def shutdown():
    await client.aclose()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)