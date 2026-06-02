import asyncio
import os
import uvicorn
import httpx

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from supabase import create_client


# --- НАСТРОЙКИ ---
BOT_TOKEN = "8972261315:AAGlcYMX2sBdBKb880gI_Xvo0eYXDw-Q8Fs"
SUPABASE_URL = "https://csibdzwhkkhsmmlkiyxk.supabase.co"
SUPABASE_KEY = "sb_secret_SkHUDJEBH53YfqJBckMrYA_FqZGy0E6"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

app = FastAPI()

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

    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(url)
            except Exception as e:
                print("Ping error:", e)

            await asyncio.sleep(300)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())

# =====================
# BOT
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
    try:
        data = await request.json()

        print("TASK REQUEST:", data)

        supabase.table("monitors").insert({
            "user_id": data["id"],
            "url": data["url"],
            "target_type": data["type"]
        }).execute()

        try:
            await bot.send_message(
                chat_id=data["id"],
                text=(
                    "✅ Мониторинг добавлен\n\n"
                    f"Ссылка: {data['url']}\n"
                    f"Тип: {data['type']}"
                )
            )
        except Exception as tg_error:
            print("Telegram error:", tg_error)

        return {"status": "task_saved"}

    except Exception as e:
        print("TASK ERROR:", e)

        return {
            "status": "error",
            "message": str(e)
        }

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
