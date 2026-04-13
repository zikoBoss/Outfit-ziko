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

# المواضع الثمانية (يمكنك تعديلها)
POSITIONS = [
    (350, 30),    # 1. قطعة ملابس
    (575, 130),   # 2. قطعة ملابس
    (665, 350),   # 3. قطعة ملابس
    (575, 550),   # 4. قطعة ملابس
    (350, 654),   # 5. قطعة ملابس
    (135, 570),   # 6. قطعة ملابس
    (47, 340),    # 7. الحيوان الأليف (pet)
    (135, 130)    # 8. السلاح
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
        "message": "Ziko Outfit API - Fixed pet image",
        "usage": "/ziko-outfit-image?key=ziko&uid=YOUR_UID"
    }

@app.get("/ziko-outfit-image")
async def generate_outfit(uid: str = Query(...), key: str = Query(...)):
    if key != API_KEY:
        raise HTTPException(401, "Invalid API key")

    data = await fetch_player_info(uid)
    if not data:
        raise HTTPException(500, "Player info fetch failed")

    profile_info = data.get("profileInfo", {})
    basic_info = data.get("basicInfo", {})
    pet_info = data.get("petInfo", {})

    # 1. قطع الملابس (أول 6)
    clothes_ids = profile_info.get("clothes", [])[:6]

    # 2. الحيوان الأليف: نفضل skinId، فإن لم يوجد نستخدم id
    pet_skin_id = pet_info.get("skinId")
    pet_id = pet_info.get("id")
    pet_final_id = pet_skin_id if pet_skin_id else pet_id

    # 3. السلاح
    weapon_list = basic_info.get("weaponSkinShows", [])
    weapon_id = weapon_list[0] if weapon_list else None

    # بناء قائمة المعرفات بالترتيب
    item_ids = list(clothes_ids)           # 6 عناصر
    item_ids.append(pet_final_id)          # العنصر السابع (pet)
    item_ids.append(weapon_id)             # العنصر الثامن (weapon)

    # التأكد من الطول 8
    while len(item_ids) < 8:
        item_ids.append(None)

    # جلب الصور بالتوازي
    tasks = [fetch_image_cached(iid) for iid in item_ids]
    images = await asyncio.gather(*tasks)

    # رسم اللوحة
    canvas = background.copy()
    for idx, img in enumerate(images):
        if img is not None and idx < len(POSITIONS):
            canvas.paste(img, POSITIONS[idx], img)

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return Response(content=output.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"})

@app.get("/debug-pet")
async def debug_pet(uid: str = Query(...), key: str = Query(...)):
    """نقطة اختبار لمعرفة معرفات الحيوان الأليف"""
    if key != API_KEY:
        raise HTTPException(401, "Invalid key")
    data = await fetch_player_info(uid)
    if not data:
        return {"error": "no data"}
    pet_info = data.get("petInfo", {})
    return {
        "pet_id": pet_info.get("id"),
        "pet_skinId": pet_info.get("skinId"),
        "pet_level": pet_info.get("level"),
        "final_used": pet_info.get("skinId") or pet_info.get("id")
    }