import httpx
import asyncio
from bs4 import BeautifulSoup
from supabase import create_client
import os

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

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
        tasks = supabase.table("monitors").select("*").execute().data
        for task in tasks:
            price = await get_price(task['url'])
            if price and price <= task['target_price']:
                # Шлем уведомление в твой ТГ
                print(f"Нашел! Цена {price} для {task['chat_id']}")
            await asyncio.sleep(2) # Пауза, чтобы сайты не забанили IP
        await asyncio.sleep(60) # Общий цикл проверки раз в минуту

if __name__ == "__main__":
    asyncio.run(run_monitor())
