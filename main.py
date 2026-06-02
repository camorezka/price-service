import asyncio
import os
import uvicorn
import httpx
import json
import re
from urllib.parse import urlparse
from datetime import datetime

from openai import AsyncOpenAI
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from supabase import create_client


# =====================
# CONFIG
# =====================
BOT_TOKEN      = os.getenv("BOT_TOKEN")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
RENDER_URL     = os.getenv("RENDER_URL", "https://price-service-51a3.onrender.com")

bot      = Bot(token=BOT_TOKEN)
dp       = Dispatcher()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ai       = AsyncOpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================
# KEEP ALIVE
# =====================
async def keep_alive():
    async with httpx.AsyncClient() as c:
        while True:
            try:
                await c.get(RENDER_URL)
            except Exception as e:
                print("Ping error:", e)
            await asyncio.sleep(300)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())


# =====================
# HELPERS
# =====================
def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    return request.client.host if request.client else "unknown"


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "binance" in host:                        return "binance"
    if "ozon" in host:                           return "ozon"
    if "wildberries" in host or "wb.ru" in host: return "wildberries"
    if "aliexpress" in host:                     return "aliexpress"
    if "amazon" in host:                         return "amazon"
    if "lamoda" in host:                         return "lamoda"
    if "adidas" in host:                         return "adidas"
    if "nike" in host:                           return "nike"
    if "sneakersnstuff" in host:                 return "sneakersnstuff"
    if "footlocker" in host:                     return "footlocker"
    if "zara" in host:                           return "zara"
    if "hm.com" in host:                         return "hm"
    if any(x in host for x in ["cbr", "exchangerate", "currency", "investing"]): return "currency"
    return "unknown"


CLOTHING_PLATFORMS = {"lamoda", "adidas", "nike", "zara", "hm", "sneakersnstuff", "footlocker", "aliexpress"}


# =====================
# AI ANALYSIS
# =====================
async def analyze_product(url: str) -> dict:
    platform   = detect_platform(url)
    is_clothing = platform in CLOTHING_PLATFORMS

    prompt = f"""
Ты — аналитик цен и товаров. Тебе дана ссылка на товар: {url}

Платформа определена как: {platform}

Верни ТОЛЬКО JSON (без markdown, без пояснений):
{{
  "name": "Название товара (придумай реалистичное для данной платформы)",
  "category": "одна из: товары | одежда | обувь | крипта | электроника | косметика | еда | спорт",
  "current_price": 0,
  "currency": "RUB",
  "price_usd": 0,
  "price_history": [
    {{"month": "Янв", "price": 0}},
    {{"month": "Фев", "price": 0}},
    {{"month": "Мар", "price": 0}},
    {{"month": "Апр", "price": 0}},
    {{"month": "Май", "price": 0}},
    {{"month": "Июн", "price": 0}}
  ],
  "forecast": {{
    "drop_probability": 0,
    "best_time_to_buy": "Напиши когда лучше купить",
    "predicted_price_30d": 0,
    "predicted_price_90d": 0
  }},
  "analytics": {{
    "summary": "2-3 предложения о товаре и его ценовой динамике",
    "usd_impact": "Как курс доллара влияет на цену этого товара",
    "recommendation": "купить сейчас | подождать | отличная цена | цена завышена"
  }},
  "is_clothing_or_shoes": {str(is_clothing).lower()},
  "alternatives": []
}}

Если это одежда или обувь (is_clothing_or_shoes = true), заполни alternatives — 3 похожих товара:
[
  {{
    "name": "Название",
    "price": 0,
    "store": "Магазин",
    "image_query": "поисковый запрос для картинки на английском (3-4 слова)"
  }}
]

Цены придумай реалистичные. Курс USD/RUB ~ 90-95 руб.
"""

    response = await ai.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Ты аналитик цен. Отвечай ТОЛЬКО валидным JSON без markdown."},
            {"role": "user",   "content": prompt}
        ],
        temperature=0.7,
        max_tokens=1500
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"```json|```", "", raw).strip()

    try:
        result = json.loads(raw)
        result["platform"] = platform
        return result
    except Exception as e:
        print("JSON parse error:", e, "\nRaw:", raw)
        return {
            "name": "Товар",
            "category": "товары",
            "current_price": 1000,
            "currency": "RUB",
            "price_usd": 11,
            "platform": platform,
            "price_history": [],
            "forecast": {"drop_probability": 30, "best_time_to_buy": "Через 1-2 месяца", "predicted_price_30d": 950, "predicted_price_90d": 900},
            "analytics": {"summary": "Не удалось получить данные", "usd_impact": "Зависит от курса", "recommendation": "подождать"},
            "is_clothing_or_shoes": False,
            "alternatives": []
        }


# =====================
# BOT HANDLERS
# =====================
@dp.message(Command("start"))
async def start(message: types.Message):
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(
                text="🚀 Открыть Price Monitor",
                web_app=types.WebAppInfo(url="https://camorezka.github.io/price-service-site/")
            )
        ]]
    )
    await message.answer(
        "👋 Привет! Я помогу отслеживать цены на товары.\n\n"
        "Нажми кнопку ниже чтобы открыть панель мониторинга.",
        reply_markup=keyboard
    )


# =====================
# API ROUTES
# =====================
@app.get("/")
async def root():
    return {"status": "ok", "version": "2.0"}


@app.post("/auth")
async def auth(request: Request):
    try:
        data     = await request.json()
        tg_id    = data.get("id")
        username = data.get("username", "")
        ip       = get_client_ip(request)

        if not tg_id:
            return JSONResponse({"status": "error", "message": "No Telegram ID"}, status_code=400)

        # Check if already registered
        existing = supabase.table("users").select("*").eq("tg_id", tg_id).execute()
        if existing.data:
            supabase.table("users").update({
                "last_ip":   ip,
                "last_seen": datetime.utcnow().isoformat()
            }).eq("tg_id", tg_id).execute()
            return {"status": "ok", "already_registered": True, "user": existing.data[0]}

        # New registration
        supabase.table("users").insert({
            "tg_id":      tg_id,
            "username":   username,
            "reg_ip":     ip,
            "last_ip":    ip,
            "last_seen":  datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        try:
            await bot.send_message(
                chat_id=tg_id,
                text="✅ Вы зарегистрированы в Price Monitor!\n\nАккаунт привязан к вашему Telegram. Вход автоматический."
            )
        except Exception as tg_err:
            print("TG notify error:", tg_err)

        return {"status": "ok", "already_registered": False}

    except Exception as e:
        print("AUTH ERROR:", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/analyze")
async def analyze(request: Request):
    try:
        data  = await request.json()
        url   = data.get("url", "").strip()
        tg_id = data.get("id")

        if not url:
            return JSONResponse({"status": "error", "message": "URL is required"}, status_code=400)

        result = await analyze_product(url)

        if tg_id:
            supabase.table("monitors").insert({
                "tg_id":    tg_id,
                "url":      url,
                "platform": result.get("platform", "unknown"),
                "category": result.get("category", "товары"),
                "name":     result.get("name", ""),
                "price":    result.get("current_price", 0),
                "added_at": datetime.utcnow().isoformat()
            }).execute()

        return {"status": "ok", "data": result}

    except Exception as e:
        print("ANALYZE ERROR:", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/monitors/{tg_id}")
async def get_monitors(tg_id: int):
    try:
        res = supabase.table("monitors").select("*").eq("tg_id", tg_id).order("added_at", desc=True).limit(20).execute()
        return {"status": "ok", "monitors": res.data}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# =====================
# SERVER
# =====================
async def run_api():
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    await asyncio.gather(dp.start_polling(bot), run_api())


if __name__ == "__main__":
    asyncio.run(main())
