
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
IMAGE_TIMEOUT = 20.0

# API الجديد
PLAYER_INFO_URL = "https://mahir-info-api.vercel.app/info"
ICON_API_BASE = "https://iconapi.wasmer.app/"

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


async def fetch_image_cached(item_id, retries=2, new_size=(150, 150)):
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
            logger.warning(
                "Failed %s attempt %s: %s",
                item_id,
                attempt + 1,
                e
            )

            if attempt < retries:
                await asyncio.sleep(0.5)
            else:
                image_cache[key] = {
                    "img": None,
                    "ts": time.time(),
                    "success": False
                }
                return None


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
    equipped_skills = profile_info.get("equipedSkills") or []

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

    tasks = []

    for idx, item_id in enumerate(item_ids):
        if item_id is None:
            tasks.append(asyncio.sleep(0, result=None))
        elif idx == 7:
            # السلاح يُحمّل بحجمه الأصلي
            tasks.append(
                fetch_image_cached(
                    item_id,
                    retries=2,
                    new_size=None
                )
            )
        else:
            tasks.append(
                fetch_image_cached(
                    item_id,
                    retries=2,
                    new_size=(150, 150)
                )
            )

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    images = []

    for idx, result in enumerate(results):
        if isinstance(result, Exception) or result is None:
            images.append(None)
            continue

        if idx == 7:
            img = result
            target_height = 150
            aspect = img.width / img.height
            new_width = int(target_height * aspect)

            img_resized = img.resize(
                (new_width, target_height),
                Image.LANCZOS
            )
            images.append(img_resized)
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
        headers={"Cache-Control": "public, max-age=300"}
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
