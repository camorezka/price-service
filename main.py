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
                print("Keep-alive ping OK")
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
    for header in ["x-forwarded-for", "x-real-ip", "cf-connecting-ip"]:
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def detect_platform(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except:
        return "unknown"
    if "binance" in host:                        return "binance"
    if "ozon" in host:                           return "ozon"
    if "wildberries" in host or "wb.ru" in host: return "wildberries"
    if "aliexpress" in host:                     return "aliexpress"
    if "amazon" in host:                         return "amazon"
    if "lamoda" in host:                         return "lamoda"
    if "adidas" in host:                         return "adidas"
    if "nike" in host:                           return "nike"
    if "zara" in host:                           return "zara"
    if "hm.com" in host:                         return "hm"
    return "unknown"


CLOTHING_PLATFORMS = {"lamoda", "adidas", "nike", "zara", "hm", "aliexpress"}


# =====================
# AI ANALYSIS
# =====================
async def analyze_product(url: str) -> dict:
    platform    = detect_platform(url)
    is_clothing = platform in CLOTHING_PLATFORMS

    print(f"[ANALYZE] URL: {url} | Platform: {platform} | Clothing: {is_clothing}")

    system_prompt = "Ты аналитик цен и товаров. Отвечай ТОЛЬКО валидным JSON без markdown блоков, без пояснений."

    user_prompt = f"""Проанализируй товар по ссылке: {url}
Платформа: {platform}

Верни строго валидный JSON в таком формате:
{{
  "name": "реалистичное название товара для платформы {platform}",
  "category": "товары",
  "current_price": 5990,
  "currency": "RUB",
  "price_usd": 65,
  "price_history": [
    {{"month": "Янв", "price": 6200}},
    {{"month": "Фев", "price": 6100}},
    {{"month": "Мар", "price": 5800}},
    {{"month": "Апр", "price": 6000}},
    {{"month": "Май", "price": 5990}},
    {{"month": "Июн", "price": 5990}}
  ],
  "forecast": {{
    "drop_probability": 35,
    "best_time_to_buy": "Через 2-3 недели",
    "predicted_price_30d": 5500,
    "predicted_price_90d": 5200
  }},
  "analytics": {{
    "summary": "Краткое описание товара и динамики цен в 2-3 предложения.",
    "usd_impact": "Краткое описание влияния курса доллара на цену.",
    "recommendation": "подождать"
  }},
  "is_clothing_or_shoes": {str(is_clothing).lower()},
  "alternatives": []
}}

Правила:
- category: одно из товары, одежда, обувь, крипта, электроника, косметика, еда, спорт
- recommendation: одно из "купить сейчас", "подождать", "отличная цена", "цена завышена"
- Если is_clothing_or_shoes = true, добавь в alternatives 3 объекта: {{"name": "...", "price": 0, "store": "...", "image_query": "english search query"}}
- Все цены в рублях, курс доллара ~92 руб
- Верни ТОЛЬКО JSON, никакого текста вокруг"""

    try:
        response = await ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt}
            ],
            temperature=0.5,
            max_tokens=1500
        )

        raw = response.choices[0].message.content.strip()
        print(f"[OPENAI RAW]: {raw[:300]}")

        # Убираем markdown если есть
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        raw = raw.strip()

        # Находим JSON если есть лишний текст
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        result = json.loads(raw)
        result["platform"] = platform
        print(f"[ANALYZE OK] name={result.get('name')} price={result.get('current_price')}")
        return result

    except json.JSONDecodeError as e:
        print(f"[JSON ERROR] {e} | Raw: {raw[:500]}")
        return _fallback(platform)
    except Exception as e:
        print(f"[OPENAI ERROR] {type(e).__name__}: {e}")
        return _fallback(platform)


def _fallback(platform: str) -> dict:
    """Возвращает заглушку если OpenAI недоступен."""
    return {
        "name": "Товар с " + platform.capitalize(),
        "category": "товары",
        "current_price": 4990,
        "currency": "RUB",
        "price_usd": 54,
        "platform": platform,
        "price_history": [
            {"month": "Янв", "price": 5500},
            {"month": "Фев", "price": 5200},
            {"month": "Мар", "price": 5000},
            {"month": "Апр", "price": 4800},
            {"month": "Май", "price": 5100},
            {"month": "Июн", "price": 4990},
        ],
        "forecast": {
            "drop_probability": 40,
            "best_time_to_buy": "Через 1-2 месяца",
            "predicted_price_30d": 4700,
            "predicted_price_90d": 4400
        },
        "analytics": {
            "summary": "Не удалось получить данные от ИИ. Показаны примерные данные.",
            "usd_impact": "Цена зависит от курса доллара. При росте курса цена растёт.",
            "recommendation": "подождать"
        },
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

        print(f"[AUTH] tg_id={tg_id} username={username} ip={ip}")

        if not tg_id:
            return JSONResponse({"status": "error", "message": "No Telegram ID"}, status_code=400)

        existing = supabase.table("users").select("id").eq("tg_id", tg_id).execute()

        if existing.data:
            supabase.table("users").update({
                "last_ip":   ip,
                "last_seen": datetime.utcnow().isoformat()
            }).eq("tg_id", tg_id).execute()
            print(f"[AUTH] existing user tg_id={tg_id}")
            return {"status": "ok", "already_registered": True}

        supabase.table("users").insert({
            "tg_id":      tg_id,
            "username":   username,
            "reg_ip":     ip,
            "last_ip":    ip,
            "last_seen":  datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        print(f"[AUTH] new user registered tg_id={tg_id}")

        try:
            await bot.send_message(
                chat_id=tg_id,
                text="✅ Вы зарегистрированы в Price Monitor!\nВход автоматический через Telegram."
            )
        except Exception as tg_err:
            print("TG notify error:", tg_err)

        return {"status": "ok", "already_registered": False}

    except Exception as e:
        print(f"[AUTH ERROR] {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/analyze")
async def analyze(request: Request):
    try:
        data  = await request.json()
        url   = data.get("url", "").strip()
        tg_id = data.get("id")

        print(f"[ANALYZE REQUEST] url={url} tg_id={tg_id}")

        if not url:
            return JSONResponse({"status": "error", "message": "URL обязателен"}, status_code=400)

        if not url.startswith("http"):
            url = "https://" + url

        result = await analyze_product(url)

        # Сохраняем в БД
        try:
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
        except Exception as db_err:
            print(f"[DB ERROR] {db_err}")
            # Не фейлим запрос из-за ошибки БД

        return {"status": "ok", "data": result}

    except Exception as e:
        print(f"[ANALYZE ERROR] {type(e).__name__}: {e}")
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
