import os
from io import BytesIO
from fastapi import FastAPI, Response, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from PIL import Image
import asyncio
import time

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API_KEY = "ziko"
BACKGROUND_FILENAME = "outfit.png"
IMAGE_TIMEOUT = 8.0
PLAYER_INFO_URL = "https://sheihk-anamul-info-ob53.vercel.app/player-info"
ICON_API_BASE = "https://iconapi.wasmer.app/"

# البادئات المطلوبة للملابس (من الكود الأقدم)
REQUIRED_STARTS = ["211", "214", "208", "203", "204", "205", "212"]
FALLBACK_IDS = ["211000000", "214000000", "208000000", "203000000", "204000000", "205000000", "212000000"]

# المواضع الثمانية (6 للملابس + 1 للحيوان + 1 للسلاح)
POSITIONS = [
    (350, 30),    # 1. head (211)
    (575, 130),   # 2. faceprint (214)
    (665, 350),   # 3. mask (208)
    (575, 550),   # 4. top (203)
    (350, 654),   # 5. bottom (204)
    (135, 570),   # 6. shoe (205)
    (47, 340),    # 7. pet
    (135, 130)    # 8. weapon
]

client = httpx.AsyncClient(timeout=httpx.Timeout(IMAGE_TIMEOUT),
                           limits=httpx.Limits(max_keepalive_connections=20),
                           follow_redirects=True)

image_cache = {}
CACHE_TTL = 300

async def fetch_image_cached(item_id, size=(150, 150)):
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
        img = img.resize(size, Image.LANCZOS)
        image_cache[key] = {"img": img, "ts": now}
        return img
    except:
        image_cache[key] = {"img": None, "ts": now}
        return None

async def fetch_player_info(uid):
    try:
        resp = await client.get(f"{PLAYER_INFO_URL}?uid={uid}")
        resp.raise_for_status()
        return resp.json()
    except:
        return None

def load_background():
    bg_path = os.path.join(os.path.dirname(__file__), BACKGROUND_FILENAME)
    return Image.open(bg_path).convert("RGBA")

background = load_background()

@app.get("/")
async def home():
    return {"message": "Ziko Outfit API (Fast & Reliable)", "usage": "/ziko-outfit-image?key=ziko&uid=UID"}

@app.get("/ziko-outfit-image")
async def generate_outfit(uid: str = Query(...), key: str = Query(...)):
    if key != API_KEY:
        raise HTTPException(401, "Invalid API key")

    data = await fetch_player_info(uid)
    if not data:
        raise HTTPException(500, "Player info fetch failed")

    # استخراج قطع الملابس باستخدام البادئات (من الكود الأقدم)
    outfit_ids = data.get("profileInfo", {}).get("clothes", [])
    used_ids = set()
    selected_clothes = []

    for idx, code in enumerate(REQUIRED_STARTS[:6]):  # نأخذ أول 6 بادئات فقط
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

    # الحيوان الأليف (يفضل skinId)
    pet_info = data.get("petInfo", {})
    pet_id = pet_info.get("skinId") or pet_info.get("id")

    # السلاح
    weapon_list = data.get("basicInfo", {}).get("weaponSkinShows", [])
    weapon_id = weapon_list[0] if weapon_list else None

    # قائمة جميع المعرفات (6 ملابس + حيوان + سلاح)
    item_ids = selected_clothes + [pet_id, weapon_id]
    # التأكد من العدد 8
    while len(item_ids) < 8:
        item_ids.append(None)

    # جلب الصور بالتوازي
    tasks = [fetch_image_cached(iid) for iid in item_ids]
    images = await asyncio.gather(*tasks)

    # رسم اللوحة
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