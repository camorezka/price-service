import asyncio
import os
import uvicorn
import httpx
import json
from urllib.parse import urlparse

from openai import AsyncOpenAI
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from supabase import create_client


# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")



bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

app = FastAPI()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

# =====================
# CORS
# =====================
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
    url = "https://price-service-51a3.onrender.com"

    async with httpx.AsyncClient() as client_http:
        while True:
            try:
                await client_http.get(url)
            except Exception as e:
                print("Ping error:", e)

            await asyncio.sleep(300)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())


# =====================
# PLATFORM DETECTION
# =====================
def detect_platform(url: str):
    host = urlparse(url).netloc.lower()

    if "binance" in host:
        return "binance"

    if "ozon" in host:
        return "ozon"

    if "wildberries" in host or "wb.ru" in host:
        return "wildberries"

    if any(x in host for x in ["cbr", "bank", "exchangerate", "currency"]):
        return "currency"

    return "unknown"


def detect_monitoring_type(platform: str, url: str):
    if platform == "binance":
        return "crypto_price"

    if platform in ["ozon", "wildberries"]:
        return "price"

    if platform == "currency":
        return "currency_rate"

    return "general"


# =====================
# AI ANALYSIS (HYBRID)
# =====================
async def analyze_url(url: str):
    platform = detect_platform(url)
    monitor_type = detect_monitoring_type(platform, url)

    # deterministic result (no GPT needed)
    if platform != "unknown":
        return {
            "supported": True,
            "category": platform,
            "type": monitor_type,
            "reason": f"Auto-detected platform: {platform}"
        }

    # fallback GPT
    response = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": """
Ты анализируешь ссылки для сервиса мониторинга.

Верни ТОЛЬКО JSON:
{
  "supported": true,
  "category": "crypto|shop|currency|unknown",
  "type": "price|crypto_price|currency_rate|general",
  "reason": "..."
}
"""
            },
            {
                "role": "user",
                "content": url
            }
        ]
    )

    raw = response.choices[0].message.content

    try:
        return json.loads(raw)
    except Exception:
        return {
            "supported": False,
            "category": "unknown",
            "type": "general",
            "reason": "Failed to parse AI response"
        }


# =====================
# BOT HANDLERS
# =====================
@dp.message(Command("start"))
async def start(message: types.Message):

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Открыть мониторинг",
                    web_app=types.WebAppInfo(
                        url="https://camorezka.github.io/price-service-site/"
                    )
                )
            ]
        ]
    )

    await message.answer(
        "Привет! Нажми кнопку ниже.",
        reply_markup=keyboard
    )


# =====================
# API
# =====================
@app.get("/")
async def root():
    return {"status": "ok"}


@app.post("/auth")
async def auth(request: Request):
    try:
        data = await request.json()

        print("AUTH REQUEST:", data)

        supabase.table("users").insert({
            "id": data["id"],
            "username": data["username"],
            "password": data["password"]
        }).execute()

        try:
            await bot.send_message(
                chat_id=data["id"],
                text="✅ Вы успешно зарегистрировались"
            )
        except Exception as tg_error:
            print("Telegram error:", tg_error)

        return {"status": "ok"}

    except Exception as e:
        print("AUTH ERROR:", e)

        return {
            "status": "error",
            "message": str(e)
        }


@app.post("/add-task")
async def add_task(request: Request):
    data = await request.json()

    print("TASK REQUEST:", data)

    analysis = await analyze_url(data["url"])

    # safety: иногда приходит строка
    if isinstance(analysis, str):
        try:
            analysis = json.loads(analysis)
        except:
            return {
                "status": "error",
                "message": "Invalid AI response"
            }

    if not analysis.get("supported", False):
        return {
            "status": "error",
            "message": analysis.get("reason", "Not supported")
        }

    supabase.table("monitors").insert({
        "user_id": data["id"],
        "url": data["url"],
        "target_type": analysis.get("type", data["type"]),
        "platform": analysis.get("category", "unknown")
    }).execute()

    try:
        await bot.send_message(
            chat_id=data["id"],
            text=(
                "✅ Мониторинг добавлен\n\n"
                f"Ссылка: {data['url']}\n"
                f"Тип: {analysis.get('type', data['type'])}"
            )
        )

        await bot.send_message(
            chat_id=data["id"],
            text=f"📊 Категория: {analysis.get('category')}\n💡 {analysis.get('reason')}"
        )

    except Exception as tg_error:
        print("Telegram error:", tg_error)

    return {"status": "task_saved"}


# =====================
# SERVER
# =====================
async def run_api():
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )

    server = uvicorn.Server(config)

    await server.serve()


# =====================
# MAIN
# =====================
async def main():
    await asyncio.gather(
        dp.start_polling(bot),
        run_api()
    )


if __name__ == "__main__":
    asyncio.run(main())
