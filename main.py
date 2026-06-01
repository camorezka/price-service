import asyncio
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client
import os

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8972261315:AAGlcYMX2sBdBKb880gI_Xvo0eYXDw-Q8Fs"
SUPABASE_URL = "https://csibdzwhkkhsmmlkiyxk.supabase.co"
SUPABASE_KEY = "sb_secret_SkHUDJEBH53YfqJBckMrYA_FqZGy0E6"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()
import httpx

async def keep_alive():
    url = "https://price-service-51a3.onrender.com" 
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(url)
                print("Ping sent!")
            except Exception as e:
                print(f"Ping error: {e}")
            await asyncio.sleep(300) 

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())





@dp.message(Command("start"))
async def start(message: types.Message):
    # Кнопка для запуска Mini App
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Открыть мониторинг", web_app=types.WebAppInfo(url="ССЫЛКА_НА_GITHUB_PAGES"))]
    ])
    await message.answer("Привет! Нажми кнопку, чтобы открыть панель управления.", reply_markup=kb)

# --- БЭКЕНД: API для Mini App ---
@app.post("/auth")
async def auth(request: Request):
    data = await request.json()
    # Сохраняем в Supabase
    supabase.table("users").insert({"id": data['id'], "username": data['username'], "password": data['password']}).execute()
    return {"status": "ok"}

@app.post("/add-task")
async def add_task(request: Request):
    data = await request.json()
    supabase.table("monitors").insert({"user_id": data['id'], "url": data['url'], "target_type": data['type']}).execute()
    return {"status": "task_saved"}



if __name__ == "__main__":
    # Запуск бота в фоновом режиме
    async def start_bot():
        await dp.start_polling(bot)

    import uvicorn
    import threading
    
    threading.Thread(target=lambda: asyncio.run(start_bot()), daemon=True).start()
    
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
