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

# --- БОТ: Логика ---
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

# --- ЗАПУСК ---
async def run_bot():
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Запуск бота и FastAPI (через uvicorn)
    import uvicorn
    # Здесь используется фоновый процесс для бота
    asyncio.run(run_bot())
