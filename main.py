import asyncio
import os
import uvicorn  # <--- ЭТОГО СТРОКИ НЕ ХВАТАЛО!
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client
import httpx

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8972261315:AAGlcYMX2sBdBKb880gI_Xvo0eYXDw-Q8Fs"
SUPABASE_URL = "https://csibdzwhkkhsmmlkiyxk.supabase.co"
SUPABASE_KEY = "sb_secret_SkHUDJEBH53YfqJBckMrYA_FqZGy0E6"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()

async def keep_alive():
    url = "https://price-service-51a3.onrender.com" 
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(url)
            except Exception as e:
                print(f"Ping error: {e}")
            await asyncio.sleep(300) 

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())

@dp.message(Command("start"))
async def start(message: types.Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Открыть мониторинг", web_app=types.WebAppInfo(url="ССЫЛКА_НА_GITHUB_PAGES"))]
    ])
    await message.answer("Привет! Нажми кнопку.", reply_markup=kb)

@app.post("/auth")
async def auth(request: Request):
    data = await request.json()
    supabase.table("users").insert({"id": data['id'], "username": data['username'], "password": data['password']}).execute()
    return {"status": "ok"}

@app.post("/add-task")
async def add_task(request: Request):
    data = await request.json()
    supabase.table("monitors").insert({"user_id": data['id'], "url": data['url'], "target_type": data['type']}).execute()
    return {"status": "task_saved"}

async def run_app():
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Запускаем бота и сервер
        loop.run_until_complete(asyncio.gather(dp.start_polling(bot), run_app()))
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
