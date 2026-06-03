import asyncio
import os
import re
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client

# ── LOGGING ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── ENV ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN",      "")
SUPABASE_URL   = os.getenv("SUPABASE_URL",   "")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY",   "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
RENDER_URL     = os.getenv("RENDER_URL",     "https://price-service-51a3.onrender.com")

for _k, _v in {
    "BOT_TOKEN": BOT_TOKEN,
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_KEY": SUPABASE_KEY,
    "OPENAI_API_KEY": OPENAI_API_KEY,
}.items():
    if not _v:
        log.warning("ENV не задан: %s", _k)

# ── CLIENTS ───────────────────────────────────────────────────────────────
bot      = Bot(token=BOT_TOKEN)                        if BOT_TOKEN                        else None
dp       = Dispatcher()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)   if SUPABASE_URL and SUPABASE_KEY    else None
ai       = AsyncOpenAI(api_key=OPENAI_API_KEY)         if OPENAI_API_KEY                   else None

# ── APP ───────────────────────────────────────────────────────────────────
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── HELPERS ───────────────────────────────────────────────────────────────
def get_client_ip(request: Request) -> str:
    for h in ["x-forwarded-for", "x-real-ip", "cf-connecting-ip"]:
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def escape_md2(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(("\\" + c) if c in special else c for c in str(text))


def safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if f == f else default  # nan check
    except (TypeError, ValueError):
        return default

# ── KEEP-ALIVE ────────────────────────────────────────────────────────────
async def keep_alive():
    async with httpx.AsyncClient() as c:
        while True:
            try:
                await c.get(RENDER_URL, timeout=10)
                log.info("[PING] OK")
            except Exception as e:
                log.warning("[PING ERROR] %s", e)
            await asyncio.sleep(280)

# ── PRICE FETCHING ────────────────────────────────────────────────────────
async def fetch_price_from_exchange(symbol: str, exchange: str) -> Optional[float]:
    sym = symbol.upper().strip()
    try:
        async with httpx.AsyncClient(timeout=10) as c:

            if exchange == "binance":
                for quote in ("USDT", "BUSD", "USDC"):
                    try:
                        r = await c.get(
                            "https://api.binance.com/api/v3/ticker/price",
                            params={"symbol": sym + quote}
                        )
                        d = r.json()
                        if "price" in d:
                            px = safe_float(d["price"])
                            if px > 0:
                                return px
                    except Exception:
                        continue
                return None

            if exchange == "bybit":
                r = await c.get(
                    "https://api.bybit.com/v5/market/tickers",
                    params={"category": "spot", "symbol": sym + "USDT"}
                )
                lst = r.json().get("result", {}).get("list", [])
                if lst:
                    px = safe_float(lst[0].get("lastPrice"))
                    return px if px > 0 else None
                return None

            if exchange == "okx":
                r = await c.get(
                    "https://www.okx.com/api/v5/market/ticker",
                    params={"instId": sym + "-USDT"}
                )
                lst = r.json().get("data", [])
                if lst:
                    px = safe_float(lst[0].get("last"))
                    return px if px > 0 else None
                return None

            if exchange == "kucoin":
                r = await c.get(
                    "https://api.kucoin.com/api/v1/market/orderbook/level1",
                    params={"symbol": sym + "-USDT"}
                )
                d = r.json().get("data") or {}
                px = safe_float(d.get("price"))
                return px if px > 0 else None

            if exchange == "htx":
                r = await c.get(
                    "https://api.huobi.pro/market/detail/merged",
                    params={"symbol": sym.lower() + "usdt"}
                )
                tick = r.json().get("tick") or {}
                px = safe_float(tick.get("close"))
                return px if px > 0 else None

            if exchange == "gate":
                r = await c.get(
                    "https://api.gateio.ws/api/v4/spot/tickers",
                    params={"currency_pair": sym + "_USDT"}
                )
                lst = r.json()
                if isinstance(lst, list) and lst:
                    px = safe_float(lst[0].get("last"))
                    return px if px > 0 else None
                return None

            if exchange == "mexc":
                r = await c.get(
                    "https://api.mexc.com/api/v3/ticker/price",
                    params={"symbol": sym + "USDT"}
                )
                d = r.json()
                if "code" in d:
                    return None
                px = safe_float(d.get("price"))
                return px if px > 0 else None

            if exchange == "coinbase":
                r = await c.get(
                    f"https://api.coinbase.com/v2/prices/{sym}-USD/spot"
                )
                px = safe_float(r.json().get("data", {}).get("amount"))
                return px if px > 0 else None

            if exchange == "kraken":
                ksym = "XBT" if sym == "BTC" else sym
                r = await c.get(
                    "https://api.kraken.com/0/public/Ticker",
                    params={"pair": ksym + "USD"}
                )
                result = r.json().get("result", {})
                for v in result.values():
                    px = safe_float(v["c"][0])
                    return px if px > 0 else None
                return None

    except Exception as e:
        log.warning("[PRICE %s/%s] %s", exchange, sym, e)
    return None

# ── COIN META ─────────────────────────────────────────────────────────────
COIN_META = {
    "BTC":   "Bitcoin",
    "ETH":   "Ethereum",
    "BNB":   "BNB",
    "SOL":   "Solana",
    "XRP":   "XRP",
    "ADA":   "Cardano",
    "DOGE":  "Dogecoin",
    "TON":   "Toncoin",
    "DOT":   "Polkadot",
    "MATIC": "Polygon",
    "AVAX":  "Avalanche",
    "LINK":  "Chainlink",
    "UNI":   "Uniswap",
    "ATOM":  "Cosmos",
    "LTC":   "Litecoin",
    "TRX":   "TRON",
    "NEAR":  "NEAR Protocol",
    "OP":    "Optimism",
    "ARB":   "Arbitrum",
    "APT":   "Aptos",
    "SUI":   "Sui",
    "PEPE":  "Pepe",
    "WIF":   "dogwifhat",
    "FLOKI": "Floki",
}

FALLBACK_PRICES = {
    "BTC": 67000, "ETH": 3500, "BNB": 600,  "SOL": 180,
    "XRP": 0.6,   "ADA": 0.45, "DOGE": 0.15, "TON": 7.0,
    "LTC": 85,    "AVAX": 35,  "LINK": 18,   "DOT": 8,
}

# ── AI ANALYSIS ───────────────────────────────────────────────────────────
async def analyze_crypto(
    symbol: str,
    exchange: str,
    live_price: Optional[float]
) -> dict:
    sym   = symbol.upper()
    name  = COIN_META.get(sym, sym)
    p     = live_price or FALLBACK_PRICES.get(sym, 1.0)
    p_uah = round(p * 41,   4)
    p_eur = round(p * 0.92, 4)
    p_rub = round(p * 92,   4)

    if p >= 1:
        p_str = f"${p:,.2f}"
    else:
        p_str = f"${p:.6f}"

    prompt = (
        f"Ты эксперт по криптовалютам. Проанализируй {name} ({sym}).\n"
        f"Текущая цена с биржи {exchange}: {p_str}\n\n"
        f"Верни ТОЛЬКО валидный JSON без markdown:\n"
        "{\n"
        f'  "name": "{name}",\n'
        f'  "symbol": "{sym}",\n'
        '  "description": "3-4 предложения о монете",\n'
        f'  "current_price_usd": {p},\n'
        f'  "price_uah": {p_uah},\n'
        f'  "price_eur": {p_eur},\n'
        f'  "price_rub": {p_rub},\n'
        '  "market_cap_billions": 0,\n'
        '  "rank": 0,\n'
        '  "price_history_7d": [\n'
        '    {"day":"Пн","price":0},{"day":"Вт","price":0},{"day":"Ср","price":0},\n'
        '    {"day":"Чт","price":0},{"day":"Пт","price":0},{"day":"Сб","price":0},\n'
        '    {"day":"Вс","price":0}\n'
        '  ],\n'
        '  "change_24h": 0.0,\n'
        '  "change_7d": 0.0,\n'
        '  "forecast": {\n'
        '    "predicted_7d": 0,\n'
        '    "predicted_30d": 0,\n'
        '    "trend": "sideways",\n'
        '    "confidence": 55,\n'
        '    "support": 0,\n'
        '    "resistance": 0\n'
        '  },\n'
        '  "ai_analysis": {\n'
        '    "summary": "2-3 предложения анализа",\n'
        '    "risks": "главные риски",\n'
        '    "opportunity": "возможности для входа",\n'
        '    "recommendation": "держать",\n'
        '    "sentiment": "нейтральный"\n'
        '  },\n'
        '  "metrics": {\n'
        '    "volatility": "средняя",\n'
        '    "liquidity": "высокая",\n'
        '    "tech_score": 70,\n'
        '    "fundamental_score": 70\n'
        '  }\n'
        "}\n\n"
        "Правила:\n"
        "- trend: bullish | bearish | sideways\n"
        "- recommendation: купить | держать | продать | накапливать\n"
        "- sentiment: позитивный | нейтральный | негативный | осторожный\n"
        "- price_history_7d — реальные реалистичные цены за 7 дней\n"
        "- ТОЛЬКО JSON, никаких пояснений"
    )

    if not ai:
        return _fallback_crypto(sym, name, exchange, live_price)

    try:
        resp = await ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Отвечай ТОЛЬКО валидным JSON. Никакого markdown."
                },
                {
                    "role": "user",
                    "content": prompt
                },
            ],
            temperature=0.3,
            max_tokens=1600,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"```(?:json)?", "", raw).strip("`").strip()
        s = raw.find("{")
        e = raw.rfind("}") + 1
        if s != -1 and e > s:
            raw = raw[s:e]
        result = json.loads(raw)

        # Всегда подставляем живую цену
        if live_price and live_price > 0:
            result["current_price_usd"] = live_price
            result["price_uah"]         = round(live_price * 41,   4)
            result["price_eur"]         = round(live_price * 0.92, 4)
            result["price_rub"]         = round(live_price * 92,   4)

        result["exchange"] = exchange
        log.info("[AI OK] %s %s $%s", sym, exchange, result.get("current_price_usd"))
        return result

    except Exception as e:
        log.error("[AI ERROR] %s", e)
        return _fallback_crypto(sym, name, exchange, live_price)


def _fallback_crypto(
    sym: str,
    name: str,
    exchange: str,
    price: Optional[float]
) -> dict:
    p = price if (price and price > 0) else FALLBACK_PRICES.get(sym, 1.0)
    return {
        "name":               name,
        "symbol":             sym,
        "exchange":           exchange,
        "description":        (
            f"{name} ({sym}) — криптовалюта. "
            "ИИ-анализ временно недоступен, показаны расчётные данные."
        ),
        "current_price_usd":  p,
        "price_uah":          round(p * 41,   4),
        "price_eur":          round(p * 0.92, 4),
        "price_rub":          round(p * 92,   4),
        "market_cap_billions": 0,
        "rank":               0,
        "price_history_7d": [
            {"day": "Пн", "price": round(p * 0.97, 6)},
            {"day": "Вт", "price": round(p * 0.99, 6)},
            {"day": "Ср", "price": round(p * 1.02, 6)},
            {"day": "Чт", "price": round(p * 0.98, 6)},
            {"day": "Пт", "price": round(p * 1.01, 6)},
            {"day": "Сб", "price": round(p * 1.03, 6)},
            {"day": "Вс", "price": round(p,        6)},
        ],
        "change_24h": 0.0,
        "change_7d":  0.0,
        "forecast": {
            "predicted_7d":  round(p * 1.03, 6),
            "predicted_30d": round(p * 1.08, 6),
            "trend":         "sideways",
            "confidence":    50,
            "support":       round(p * 0.92, 6),
            "resistance":    round(p * 1.10, 6),
        },
        "ai_analysis": {
            "summary":        "ИИ-анализ временно недоступен. Данные рассчитаны автоматически.",
            "risks":          "Высокая волатильность крипторынка.",
            "opportunity":    "Следите за объёмами торгов и новостным фоном.",
            "recommendation": "держать",
            "sentiment":      "нейтральный",
        },
        "metrics": {
            "volatility":         "средняя",
            "liquidity":          "высокая",
            "tech_score":         60,
            "fundamental_score":  60,
        },
    }

# ── PRICE WATCHER ─────────────────────────────────────────────────────────
async def price_watcher():
    await asyncio.sleep(60)
    log.info("[WATCHER] Запущен")
    while True:
        try:
            if not supabase:
                await asyncio.sleep(300)
                continue

            rows = supabase.table("crypto_monitors").select("*").execute()
            data = rows.data or []
            log.info("[WATCHER] Проверяем %d записей", len(data))

            for row in data:
                sym       = row.get("symbol", "")
                exchange  = row.get("exchange", "binance")
                tg_id     = row.get("tg_id")
                old_px    = safe_float(row.get("last_price") or row.get("price_at_add"))
                alert_pct = safe_float(row.get("alert_pct"), 5.0)

                if not sym or not tg_id or old_px <= 0:
                    continue

                new_px = await fetch_price_from_exchange(sym, exchange)
                if not new_px or new_px <= 0:
                    continue

                change_pct  = ((new_px - old_px) / old_px) * 100
                abs_change  = abs(change_pct)

                if abs_change >= alert_pct:
                    direction = "📈 выросла" if change_pct > 0 else "📉 упала"
                    sign      = "\\+" if change_pct > 0 else "\\-"

                    if new_px >= 1:
                        old_fmt = escape_md2(f"${old_px:,.2f}")
                        new_fmt = escape_md2(f"${new_px:,.2f}")
                    else:
                        old_fmt = escape_md2(f"${old_px:.6f}")
                        new_fmt = escape_md2(f"${new_px:.6f}")

                    msg = (
                        f"🔔 *{escape_md2(sym)}* {direction} на "
                        f"*{sign}{escape_md2(str(round(abs_change, 2)))}%*\n"
                        f"Биржа: `{escape_md2(exchange.upper())}`\n"
                        f"Было: `{old_fmt}`\n"
                        f"Сейчас: `{new_fmt}`\n"
                        f"_Crypto Space_"
                    )

                    if bot:
                        try:
                            await bot.send_message(
                                chat_id=tg_id,
                                text=msg,
                                parse_mode="MarkdownV2"
                            )
                            log.info("[ALERT] %s → %s %.2f%%", sym, tg_id, change_pct)
                        except Exception as tg_err:
                            log.warning("[ALERT TG] %s", tg_err)

                    try:
                        supabase.table("crypto_monitors").update({
                            "last_price":   new_px,
                            "last_alerted": datetime.now(timezone.utc).isoformat(),
                        }).eq("id", row["id"]).execute()
                    except Exception as db_err:
                        log.warning("[WATCHER UPDATE] %s", db_err)

                else:
                    try:
                        supabase.table("crypto_monitors").update(
                            {"last_price": new_px}
                        ).eq("id", row["id"]).execute()
                    except Exception as db_err:
                        log.warning("[WATCHER TICK] %s", db_err)

                await asyncio.sleep(0.5)

        except Exception as fatal:
            log.error("[WATCHER FATAL] %s", fatal)

        await asyncio.sleep(300)

# ── BOT POLLING ───────────────────────────────────────────────────────────
async def run_bot_polling():
    if not bot:
        log.warning("[BOT] Токен не задан — polling пропущен")
        return
    try:
        log.info("[BOT] Polling запущен")
        await dp.start_polling(bot, handle_signals=False)
    except Exception as e:
        log.error("[BOT POLLING] %s", e)

# ── STARTUP ───────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())
    asyncio.create_task(price_watcher())
    asyncio.create_task(run_bot_polling())
    log.info("[STARTUP] Все задачи запущены")

# ── TELEGRAM COMMANDS ─────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text="🚀 Открыть Crypto Space",
            web_app=types.WebAppInfo(
                url="https://camorezka.github.io/price-service-site/"
            )
        )
    ]])
    text = (
        "👋 Привет\\! *Crypto Space* — мониторинг крипты с ИИ\\-аналитикой\\.\n\n"
        "Нажми кнопку ниже 👇"
    )
    await message.answer(text, reply_markup=kb, parse_mode="MarkdownV2")

# ── ROUTES ────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "service": "Crypto Space", "version": "2.0"}


@app.post("/auth")
async def auth(request: Request):
    try:
        data       = await request.json()
        tg_id      = data.get("id")
        username   = str(data.get("username")   or "")
        first_name = str(data.get("first_name") or "")
        last_name  = str(data.get("last_name")  or "")
        language   = str(data.get("language")   or "")
        platform   = str(data.get("platform")   or "")
        user_agent = request.headers.get("user-agent", "")
        ip         = get_client_ip(request)
        now_iso    = datetime.now(timezone.utc).isoformat()

        if not tg_id:
            return JSONResponse(
                {"status": "error", "message": "No Telegram ID"},
                status_code=400
            )

        if not supabase:
            return JSONResponse(
                {"status": "error", "message": "DB not configured"},
                status_code=500
            )

        existing = supabase.table("users").select("id, visit_count").eq("tg_id", tg_id).execute()

        if existing.data:
            old_count = existing.data[0].get("visit_count") or 1
            supabase.table("users").update({
                "last_ip":    ip,
                "last_seen":  now_iso,
                "user_agent": user_agent,
                "platform":   platform,
                "language":   language,
                "visit_count": old_count + 1,
            }).eq("tg_id", tg_id).execute()
            return {"status": "ok", "already_registered": True}

        supabase.table("users").insert({
            "tg_id":      tg_id,
            "username":   username,
            "first_name": first_name,
            "last_name":  last_name,
            "reg_ip":     ip,
            "last_ip":    ip,
            "user_agent": user_agent,
            "platform":   platform,
            "language":   language,
            "last_seen":  now_iso,
            "created_at": now_iso,
            "visit_count": 1,
        }).execute()

        if bot:
            try:
                welcome = (
                    "✅ Добро пожаловать в *Crypto Space*\\!\n"
                    "Вход через Telegram — автоматически\\."
                )
                await bot.send_message(
                    chat_id=tg_id,
                    text=welcome,
                    parse_mode="MarkdownV2"
                )
            except Exception as tg_err:
                log.warning("[TG WELCOME] %s", tg_err)

        return {"status": "ok", "already_registered": False}

    except Exception as e:
        log.error("[AUTH ERROR] %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/analyze")
async def analyze(request: Request):
    try:
        data      = await request.json()
        symbol    = str(data.get("symbol")   or "").strip().upper()
        exchange  = str(data.get("exchange") or "binance").strip().lower()
        tg_id     = data.get("id")
        alert_pct = safe_float(data.get("alert_pct"), 5.0)

        if not symbol:
            return JSONResponse(
                {"status": "error", "message": "Укажите символ"},
                status_code=400
            )

        live_price = await fetch_price_from_exchange(symbol, exchange)
        result     = await analyze_crypto(symbol, exchange, live_price)
        final_px   = live_price or result.get("current_price_usd") or 0

        if supabase and tg_id:
            try:
                now_iso  = datetime.now(timezone.utc).isoformat()
                existing = (
                    supabase.table("crypto_monitors")
                    .select("id")
                    .eq("tg_id",    tg_id)
                    .eq("symbol",   symbol)
                    .eq("exchange", exchange)
                    .execute()
                )
                if existing.data:
                    supabase.table("crypto_monitors").update({
                        "last_price":   final_px,
                        "price_at_add": final_px,
                        "alert_pct":    alert_pct,
                        "added_at":     now_iso,
                    }).eq("id", existing.data[0]["id"]).execute()
                else:
                    supabase.table("crypto_monitors").insert({
                        "tg_id":        tg_id,
                        "symbol":       symbol,
                        "exchange":     exchange,
                        "price_at_add": final_px,
                        "last_price":   final_px,
                        "alert_pct":    alert_pct,
                        "added_at":     now_iso,
                    }).execute()
            except Exception as db_err:
                log.warning("[DB ANALYZE] %s", db_err)

        return {"status": "ok", "data": result}

    except Exception as e:
        log.error("[ANALYZE ERROR] %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/price/{exchange}/{symbol}")
async def get_price(exchange: str, symbol: str):
    price = await fetch_price_from_exchange(symbol.upper(), exchange.lower())
    if price is None:
        return JSONResponse(
            {
                "status": "error",
                "message": f"Не удалось получить цену {symbol} с {exchange}"
            },
            status_code=404
        )
    return {
        "status":   "ok",
        "price":    price,
        "symbol":   symbol.upper(),
        "exchange": exchange.lower()
    }


@app.get("/monitors/{tg_id}")
async def get_monitors(tg_id: int):
    try:
        if not supabase:
            return JSONResponse(
                {"status": "error", "message": "DB not configured"},
                status_code=500
            )
        res = (
            supabase.table("crypto_monitors")
            .select("*")
            .eq("tg_id", tg_id)
            .order("added_at", desc=True)
            .limit(30)
            .execute()
        )
        return {"status": "ok", "monitors": res.data}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ── ENTRY POINT ───────────────────────────────────────────────────────────
# Render запускает: uvicorn main:app --host 0.0.0.0 --port $PORT
# Локальный запуск:
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False
    )
