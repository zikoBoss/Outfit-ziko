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

# --- CONFIGURATION ---
API_KEY = "ziko"
BACKGROUND_FILENAME = "outfit.png"
IMAGE_TIMEOUT = 8.0
CANVAS_SIZE = (500, 500)          # الحجم النهائي للصورة
BACKGROUND_MODE = "cover"         # 'cover' أو 'contain'

PLAYER_INFO_URL = "https://sheihk-anamul-info-ob53.vercel.app/player-info"
ICON_API_BASE = "https://iconapi.wasmer.app/"

# البادئات المطلوبة (نفس الكود الأقدم)
REQUIRED_STARTS = ["211", "214", "208", "203", "204", "205", "212"]
FALLBACK_IDS = ["211000000", "214000000", "208000000", "203000000", "204000000", "205000000", "212000000"]

# الإحداثيات الأصلية من الكود الأقدم (بدون مقياس)
POSITIONS_RAW = [
    {"x": 350, "y": 30, "w": 150, "h": 150},   # head
    {"x": 575, "y": 130, "w": 150, "h": 150},  # faceprint
    {"x": 665, "y": 350, "w": 150, "h": 150},  # mask
    {"x": 575, "y": 550, "w": 150, "h": 150},  # top
    {"x": 350, "y": 654, "w": 150, "h": 150},  # bottom
    {"x": 135, "y": 570, "w": 150, "h": 150},  # shoe
    {"x": 135, "y": 130, "w": 150, "h": 150}   # إضافي
]

# --- HTTP Client Pool ---
client = httpx.AsyncClient(
    timeout=httpx.Timeout(IMAGE_TIMEOUT),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    follow_redirects=True
)

# --- Cache (5 دقائق) ---
image_cache = {}
CACHE_TTL = 300

async def fetch_image_cached(item_id: str, size=(150, 150)):
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
    except Exception:
        image_cache[key] = {"img": None, "ts": now}
        return None

async def fetch_player_info(uid: str):
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

# تحميل الخلفية مرة واحدة عند بدء التشغيل
background_image = load_background()
bg_w, bg_h = background_image.size

# حساب التدرج (scaling) بناءً على CANVAS_SIZE و BACKGROUND_MODE
if CANVAS_SIZE is None:
    canvas_w, canvas_h = bg_w, bg_h
    scale_x = scale_y = 1.0
    offset_x = offset_y = 0
    background_resized = background_image
else:
    canvas_w, canvas_h = CANVAS_SIZE
    if BACKGROUND_MODE == "contain":
        scale = min(canvas_w / bg_w, canvas_h / bg_h)
    else:  # cover
        scale = max(canvas_w / bg_w, canvas_h / bg_h)
    new_w = max(1, int(bg_w * scale))
    new_h = max(1, int(bg_h * scale))
    background_resized = background_image.resize((new_w, new_h), Image.LANCZOS)
    offset_x = (canvas_w - new_w) // 2
    offset_y = (canvas_h - new_h) // 2
    scale_x = new_w / bg_w
    scale_y = new_h / bg_h

# تحويل الإحداثيات إلى القيم النهائية (مرة واحدة فقط)
scaled_positions = []
for pos in POSITIONS_RAW:
    x = offset_x + int(pos["x"] * scale_x)
    y = offset_y + int(pos["y"] * scale_y)
    w = max(1, int(pos["w"] * scale_x))
    h = max(1, int(pos["h"] * scale_y))
    scaled_positions.append((x, y, w, h))

# --- Routes ---
@app.get("/")
async def home():
    return {
        "message": "⚡ Ziko Outfit API (Fast & Fixed Positions)",
        "usage": "/ziko-outfit-image?key=ziko&uid=YOUR_UID",
        "note": "If positions are wrong, edit POSITIONS_RAW in the code"
    }

@app.get("/ziko-outfit-image")
async def generate_outfit(uid: str = Query(...), key: str = Query(...)):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # جلب بيانات اللاعب
    player_data = await fetch_player_info(uid)
    if not player_data:
        raise HTTPException(status_code=500, detail="Failed to fetch player info")

    # استخراج outfit_ids بنفس طريقة الكود الأقدم
    outfit_ids = player_data.get("profileInfo", {}).get("clothes", [])
    if not outfit_ids:
        outfit_ids = player_data.get("AccountProfileInfo", {}).get("EquippedOutfit", []) or []

    used_ids = set()
    selected_ids = []

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
        selected_ids.append(matched)

    # جلب الصور بالتوازي
    tasks = [fetch_image_cached(item_id, size=(150, 150)) for item_id in selected_ids]
    images = await asyncio.gather(*tasks)

    # إنشاء اللوحة
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 255))
    canvas.paste(background_resized, (offset_x, offset_y), background_resized)

    # لصق كل صورة في موضعها
    for idx, img in enumerate(images):
        if img is not None and idx < len(scaled_positions):
            x, y, w, h = scaled_positions[idx]
            # الصورة بحجم 150x150 قد تحتاج إلى تغيير الحجم حسب المساحة
            if img.size != (w, h):
                img = img.resize((w, h), Image.LANCZOS)
            canvas.paste(img, (x, y), img)

    # إخراج الصورة
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