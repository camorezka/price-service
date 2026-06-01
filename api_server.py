import asyncio
import httpx
import os
from bs4 import BeautifulSoup
from fastapi import FastAPI
from pydantic import BaseModel
from supabase import create_client
import uvicorn

app = FastAPI()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

class MonitorTask(BaseModel):
    url: str
    target_price: float
    chat_id: str

# --- ЛОГИКА МОНИТОРИНГА ---
async def get_price(url):
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = await client.get(url, follow_redirects=True)
            soup = BeautifulSoup(resp.text, 'html.parser')
            price_text = soup.select_one(".price, [class*='price']").text
            return float(''.join(filter(str.isdigit, price_text)))
    except:
        return None

async def run_monitor():
    while True:
        try:
            tasks = supabase.table("monitors").select("*").execute().data
            for task in tasks:
                price = await get_price(task['url'])
                if price and price <= task['target_price']:
                    print(f"Нашел! Цена {price} для {task['chat_id']}")
                await asyncio.sleep(2)
        except Exception as e:
            print(f"Ошибка мониторинга: {e}")
        await asyncio.sleep(60)

# --- API ЭНДПОИНТЫ ---
@app.post("/add-task")
async def add_task(task: MonitorTask):
    data = supabase.table("monitors").insert({
        "url": task.url, 
        "target_price": task.target_price, 
        "chat_id": task.chat_id
    }).execute()
    return {"status": "success", "data": data}

# --- ЗАПУСК ---
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_monitor())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("api_server:app", host="0.0.0.0", port=port)
