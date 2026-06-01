from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client
import os

app = FastAPI()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

class MonitorTask(BaseModel):
    url: str
    target_price: float
    chat_id: str

@app.post("/add-task")
async def add_task(task: MonitorTask):
    # Добавляем задачу в таблицу 'monitors'
    data = supabase.table("monitors").insert({
        "url": task.url, 
        "target_price": task.target_price, 
        "chat_id": task.chat_id
    }).execute()
    return {"status": "success", "data": data}
