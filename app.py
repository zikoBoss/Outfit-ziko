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

# --- CONFIG ---
API_KEY = "ziko"  # تم تغييره كما طلبت
BACKGROUND_FILENAME = "outfit.png"
IMAGE_TIMEOUT = 6.0
PLAYER_INFO_URL = "https://sheihk-anamul-info-ob53.vercel.app/player-info"
ICON_API_BASE = "https://iconapi.wasmer.app/"

# المواضع الصحيحة (8 مواقع كما في الكود القديم)
POSITIONS = [
    (350, 30),    # head
    (575, 130),   # faceprint
    (665, 350),   # mask
    (575, 550),   # top
    (350, 654),   # bottom
    (135, 570),   # shoe
    (47, 340),    # pet (أو إضافي)
    (135, 130)    # weapon
]

# --- HTTP Client Pool ---
client = httpx.AsyncClient(
    timeout=httpx.Timeout(IMAGE_TIMEOUT),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    follow_redirects=True
)

# --- Simple Cache (TTL 5 min) ---
image_cache = {}
CACHE_TTL = 300

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
    try:
        bg = Image.open(bg_path).convert("RGBA")
        return bg
    except Exception as e:
        raise RuntimeError(f"Background not found: {BACKGROUND_FILENAME}") from e

background = load_background()
canvas_base = background.copy()  # سيتم نسخها لكل طلب

# --- Routes ---
@app.get("/")
async def home():
    return {
        "status": "running",
        "message": "Ziko Outfit API (Fast & Fixed)",
        "usage": "/ziko-outfit-image?key=ziko&uid=YOUR_UID"
    }

@app.get("/ziko-outfit-image")
async def generate_outfit(uid: str = Query(...), key: str = Query(...)):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    data = await fetch_player_info(uid)
    if not data:
        raise HTTPException(status_code=500, detail="Player info fetch failed")

    # ===== منطق استخراج العناصر (مثل الكود القديم) =====
    # 1. أول 6 قطع من clothes
    outfit_ids = data.get("profileInfo", {}).get("clothes", [])[:6]
    # 2. الـ pet
    pet_id = data.get("petInfo", {}).get("id")
    # 3. أول سلاح من weaponSkinShows
    weapon_list = data.get("basicInfo", {}).get("weaponSkinShows", [])
    weapon_id = weapon_list[0] if weapon_list else None

    # جمع كل المعرفات (قد يصل العدد أقل من 8، وسيتم التعامل معها)
    item_ids = outfit_ids.copy()
    if pet_id:
        item_ids.append(pet_id)
    else:
        item_ids.append(None)
    if weapon_id:
        item_ids.append(weapon_id)
    else:
        item_ids.append(None)

    # التأكد من أن لدينا 8 عناصر (ملء بـ None)
    while len(item_ids) < 8:
        item_ids.append(None)

    # جلب كل الصور بالتوازي
    tasks = [fetch_image_cached(iid) for iid in item_ids]
    images = await asyncio.gather(*tasks)

    # إنشاء اللوحة من نسخة من الخلفية
    canvas = background.copy()

    # لصق الصور في المواضع المحددة
    for idx, img in enumerate(images):
        if img is not None and idx < len(POSITIONS):
            canvas.paste(img, POSITIONS[idx], img)

    # إخراج PNG
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)

    return Response(
        content=output.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"}
    )

@app.on_event("shutdown")
async def shutdown():
    await client.aclose()