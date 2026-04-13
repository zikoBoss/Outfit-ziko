import os
import hashlib
from io import BytesIO
from functools import lru_cache
from fastapi import FastAPI, Response, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from PIL import Image
import asyncio

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Configuration ---
API_KEY = "ziko"
BACKGROUND_FILENAME = "outfit.png"
IMAGE_TIMEOUT = 8.0
CANVAS_SIZE = (500, 500)
BACKGROUND_MODE = "cover"  # 'cover' or 'contain'

# API endpoints
PLAYER_INFO_URL = "https://sheihk-anamul-info-ob53.vercel.app/player-info"
ICON_API_BASE = "https://iconapi.wasmer.app/"

# Required item prefixes for outfit parts
REQUIRED_STARTS = ["211", "214", "208", "203", "204", "205", "212"]
FALLBACK_IDS = ["211000000", "214000000", "208000000", "203000000", "204000000", "205000000", "212000000"]

# Positions (original design for background)
POSITIONS = [
    {"x": 350, "y": 30, "width": 150, "height": 150},   # head
    {"x": 575, "y": 130, "width": 150, "height": 150},   # faceprint
    {"x": 665, "y": 350, "width": 150, "height": 150},   # mask
    {"x": 575, "y": 550, "width": 150, "height": 150},   # top
    {"x": 350, "y": 654, "width": 150, "height": 150},   # bottom
    {"x": 135, "y": 570, "width": 150, "height": 150},   # shoe
    {"x": 135, "y": 130, "width": 150, "height": 150}    # accessory
]

# --- Global HTTP client (connection pooling) ---
client = httpx.AsyncClient(
    timeout=httpx.Timeout(IMAGE_TIMEOUT),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    follow_redirects=True
)

# --- Simple in-memory cache for images (TTL 300 seconds) ---
_image_cache = {}
_cache_ttl = 300  # seconds

def _cache_key(item_id: str) -> str:
    return f"icon:{item_id}"

async def fetch_image_cached(item_id: str, size: tuple = (150, 150)):
    if not item_id:
        return None
    cache_key = _cache_key(str(item_id))
    import time
    now = time.time()
    if cache_key in _image_cache:
        entry = _image_cache[cache_key]
        if now - entry["timestamp"] < _cache_ttl:
            return entry["image"]
    # Fetch
    url = f"{ICON_API_BASE}{item_id}"
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        if size:
            img = img.resize(size, Image.LANCZOS)
        _image_cache[cache_key] = {"image": img, "timestamp": now}
        return img
    except Exception:
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
    try:
        bg = Image.open(bg_path).convert("RGBA")
        return bg
    except Exception as e:
        raise RuntimeError(f"Background missing or corrupt: {BACKGROUND_FILENAME}") from e

# Preload background at startup (synchronous, but fine)
background_image = load_background()
bg_w, bg_h = background_image.size

# Precompute scaling once (since canvas size fixed)
canvas_w, canvas_h = CANVAS_SIZE
if BACKGROUND_MODE == "contain":
    scale = min(canvas_w / bg_w, canvas_h / bg_h)
else:  # cover
    scale = max(canvas_w / bg_w, canvas_h / bg_h)
new_bg_w = max(1, int(bg_w * scale))
new_bg_h = max(1, int(bg_h * scale))
background_resized = background_image.resize((new_bg_w, new_bg_h), Image.LANCZOS)
offset_x = (canvas_w - new_bg_w) // 2
offset_y = (canvas_h - new_bg_h) // 2
scale_x = new_bg_w / bg_w
scale_y = new_bg_h / bg_h

# Pre-scale positions once (list of tuples (x,y,w,h))
scaled_positions = []
for pos in POSITIONS:
    x = offset_x + int(pos["x"] * scale_x)
    y = offset_y + int(pos["y"] * scale_y)
    w = max(1, int(pos["width"] * scale_x))
    h = max(1, int(pos["height"] * scale_y))
    scaled_positions.append((x, y, w, h))

# --- Routes ---
@app.get("/")
async def home():
    return {
        "message": "⚡ Fast Outfit Generator API",
        "usage": "/ziko-outfit-image?key=xAyOuB&uid=YOUR_UID",
        "mode": BACKGROUND_MODE,
        "canvas": CANVAS_SIZE
    }

@app.get("/ziko-outfit-image")
async def generate_outfit(
    uid: str = Query(..., min_length=1),
    key: str = Query(...)
):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Fetch player data
    player_data = await fetch_player_info(uid)
    if not player_data:
        raise HTTPException(status_code=500, detail="Failed to fetch player info")

    # Extract outfit IDs
    outfit_ids = player_data.get("profileInfo", {}).get("clothes", [])
    if not outfit_ids:
        outfit_ids = player_data.get("AccountProfileInfo", {}).get("EquippedOutfit", []) or []

    # Map each required prefix to a specific item ID (first unused)
    used_ids = set()
    selected_ids = []
    for code in REQUIRED_STARTS:
        matched = None
        for oid in outfit_ids:
            str_oid = str(oid)
            if str_oid.startswith(code) and str_oid not in used_ids:
                matched = str_oid
                used_ids.add(str_oid)
                break
        if matched is None:
            matched = FALLBACK_IDS[REQUIRED_STARTS.index(code)]
        selected_ids.append(matched)

    # Fetch all 7 images concurrently
    tasks = [fetch_image_cached(item_id, size=(150, 150)) for item_id in selected_ids]
    outfit_images = await asyncio.gather(*tasks)

    # Start with a fresh canvas (copy of pre-scaled background)
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 255))
    canvas.paste(background_resized, (offset_x, offset_y), background_resized)

    # Paste each outfit image
    for idx, img in enumerate(outfit_images):
        if img is None:
            continue
        x, y, w, h = scaled_positions[idx]
        # 이미지 크기를 맞춤 (이미 이미 150x150으로 리사이즈되어 있지만 위치 크기가 다를 수 있음)
        if img.size != (w, h):
            img = img.resize((w, h), Image.LANCZOS)
        canvas.paste(img, (x, y), img)

    # Output PNG (optimized)
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