
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

API_KEY = "ziko"
BACKGROUND_FILENAME = "outfit.png"
IMAGE_TIMEOUT = 60.0  # Render free tier cold start can take ~50s

# API الجديد
PLAYER_INFO_URL = "https://api-info-by-xalinas-12.vercel.app/player-info"
# iconapi.wasmer.app is dead (HTTP 402 payment required for every id),
# so icons come from the same icon API the banner project already uses.
ICON_API_BASE = "https://mafu-icon-api.onrender.com/icon?key=MAFU&item_id="

REQUIRED_STARTS = ["211", "214", "208", "203", "204", "205"]
FALLBACK_IDS = [
    "211000000", "214000000", "208000000",
    "203000000", "204000000", "205000000"
]

# الإحداثيات الأصلية - لم يتم تغييرها
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
    limits=httpx.Limits(
        max_keepalive_connections=30,
        max_connections=100
    ),
    follow_redirects=True
)

image_cache = {}
CACHE_TTL_SUCCESS = 600
CACHE_TTL_FAIL = 5


async def fetch_image_cached(item_id, retries=3, new_size=(150, 150)):
    if not item_id:
        return None

    now = time.time()
    size_key = (
        f"_{new_size[0]}x{new_size[1]}"
        if new_size else "_original"
    )
    key = str(item_id) + size_key

    if key in image_cache:
        entry = image_cache[key]
        ttl = CACHE_TTL_SUCCESS if entry["success"] else CACHE_TTL_FAIL

        if now - entry["ts"] < ttl:
            return entry["img"]

    for attempt in range(retries + 1):
        try:
            url = f"{ICON_API_BASE}{item_id}"
            logger.info(
                "Fetching %s (attempt %s)",
                item_id,
                attempt + 1
            )

            resp = await client.get(url)
            resp.raise_for_status()

            img = Image.open(BytesIO(resp.content)).convert("RGBA")

            if new_size:
                img = img.resize(new_size, Image.LANCZOS)

            image_cache[key] = {
                "img": img,
                "ts": time.time(),
                "success": True
            }

            logger.info("Success %s", item_id)
            return img

        except Exception as e:
            status = getattr(
                getattr(e, "response", None),
                "status_code",
                None
            )

            logger.warning(
                "Failed %s attempt %s: %s",
                item_id,
                attempt + 1,
                e
            )

            # 404 means the icon does not exist: retrying is pointless.
            if status == 404 or attempt >= retries:
                image_cache[key] = {
                    "img": None,
                    "ts": time.time(),
                    "success": False
                }
                return None

            # The icon API sleeps on Render's free tier and answers 502
            # while waking up: wait longer between attempts.
            await asyncio.sleep(8 * (attempt + 1))


async def fetch_player_info(uid):
    try:
        resp = await client.get(
            PLAYER_INFO_URL,
            params={"uid": uid}
        )
        resp.raise_for_status()

        data = resp.json()

        if not isinstance(data, dict):
            logger.error("Player API returned unexpected JSON")
            return None

        # بعض APIs قد تعيد الخطأ داخل JSON رغم HTTP 200
        if data.get("error") or data.get("message") == "error":
            logger.error("Player API returned an error: %s", data)
            return None

        return data

    except Exception as e:
        logger.error("Player info failed: %s", e)
        return None


def load_background():
    bg_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        BACKGROUND_FILENAME
    )
    return Image.open(bg_path).convert("RGBA")


background = load_background()


def extract_outfit_ids(data):
    """
    استخراج معرّفات العناصر وفق البادئات المطلوبة.
    لا نعتبر equipedSkills بديلاً مؤكداً عن clothes؛
    نستخدمه فقط إذا لم نجد تطابقات في clothes.
    """
    profile_info = data.get("profileInfo") or {}

    clothes = profile_info.get("clothes") or []
    # الـ API الجديد يرسل equippedSkills (بتهجئة صحيحة)؛ القديم كان يرسل equipedSkills
    equipped_skills = (
        profile_info.get("equippedSkills")
        or profile_info.get("equipedSkills")
        or []
    )

    def match_items(source):
        used_ids = set()
        selected = []

        for idx, prefix in enumerate(REQUIRED_STARTS):
            matched = None

            for oid in source:
                str_oid = str(oid)

                if (
                    str_oid.startswith(prefix)
                    and str_oid not in used_ids
                ):
                    matched = str_oid
                    used_ids.add(str_oid)
                    break

            selected.append(matched)

        return selected

    selected = match_items(clothes)

    # إذا لم نجد أي تطابق في clothes، نجرب equipedSkills.
    if not any(selected) and equipped_skills:
        logger.info("No matching clothes; checking equipedSkills")
        selected = match_items(equipped_skills)

    # استخدام المعرّفات الاحتياطية فقط للعناصر التي لم نجدها.
    for idx, item_id in enumerate(selected):
        if item_id is None:
            selected[idx] = FALLBACK_IDS[idx]

    return selected


@app.get("/")
async def home():
    return {
        "message": "Ziko Outfit API",
        "usage": "/ziko-outfit-image?key=ziko&uid=UID"
    }


@app.get("/ziko-outfit-image")
async def generate_outfit(
    uid: str = Query(...),
    key: str = Query(...)
):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    data = await fetch_player_info(uid)

    if not data:
        raise HTTPException(
            status_code=502,
            detail="Player info fetch failed"
        )

    profile_info = data.get("profileInfo") or {}
    basic_info = data.get("basicInfo") or {}
    pet_info = data.get("petInfo") or {}

    selected_clothes = extract_outfit_ids(data)

    # الحيوان الأليف
    pet_id = pet_info.get("skinId") or pet_info.get("id")

    # السلاح: الاحتفاظ باستخدام أول معرّف كما في السكربت الأصلي
    weapon_list = basic_info.get("weaponSkinShows") or []
    weapon_id = weapon_list[0] if weapon_list else None

    # الحركة: قد لا يرسلها API الجديد
    animation_id = basic_info.get("equippedAnimationId")

    item_ids = selected_clothes + [
        pet_id,
        weapon_id,
        animation_id
    ]

    while len(item_ids) < 9:
        item_ids.append(None)

    # Fetch icons one by one, not 9 at once: the icon API is a small
    # free Render service and a burst of 9 concurrent requests makes it
    # fail (502/timeout) while sequential requests succeed — the banner
    # fetches sequentially and works. After the deadline, each remaining
    # icon is tried only once so a woken service still fills every slot
    # without blowing the request time budget.
    fetch_deadline = time.monotonic() + 45
    images = []

    for idx, item_id in enumerate(item_ids):
        if item_id is None:
            images.append(None)
            continue

        retries = 3 if time.monotonic() <= fetch_deadline else 0
        size = None if idx == 7 else (150, 150)

        try:
            result = await fetch_image_cached(
                item_id,
                retries=retries,
                new_size=size
            )
        except Exception:
            result = None

        if result is None:
            images.append(None)
            continue

        if idx == 7:
            img = result
            target_height = 150
            aspect = img.width / img.height
            new_width = int(target_height * aspect)

            images.append(
                img.resize(
                    (new_width, target_height),
                    Image.LANCZOS
                )
            )
        else:
            images.append(result)

    # تركيب الأيقونات على الخلفية الأصلية
    canvas = background.copy()

    for idx, img in enumerate(images):
        if img is not None and idx < len(POSITIONS):
            canvas.paste(img, POSITIONS[idx], img)

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)

    return Response(
        content=output.getvalue(),
        media_type="image/png",
        # No edge caching: the image depends on live player data and a
        # cached empty/error image would be served stale for minutes.
        headers={"Cache-Control": "no-store"}
    )


@app.on_event("shutdown")
async def shutdown():
    await client.aclose()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )
