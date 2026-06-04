import asyncio
import os
import logging
from datetime import datetime, timezone

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client
import random  # импорт один раз сверху

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
RENDER_URL   = os.getenv("RENDER_URL", "https://price-service-51a3.onrender.com")
ADMIN_TG_ID  = 1693493298
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

# Глобальный httpx клиент
HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(12.0),
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20
    ),
    headers={
        "User-Agent": "CryptoSpace/4.0"
    }
)

bot      = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp       = Dispatcher()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


# ── HELPERS ───────────────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    for h in ["x-forwarded-for", "x-real-ip", "cf-connecting-ip"]:
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def escape_md2(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(("\\" + c) if c in special else c for c in str(text))

def safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if f == f else default
    except (TypeError, ValueError):
        return default

def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.2f}"
    if p >= 0.01:
        return f"${p:.4f}"
    return f"${p:.6f}"

# ── SHUTDOWN ─────────────────────────────────────────────────────────────────

@app.on_event("shutdown")
async def shutdown_event():
    await HTTP_CLIENT.aclose()

# ── KEEP-ALIVE ────────────────────────────────────────────────────────────────

async def keep_alive():
    while True:
        try:
            await HTTP_CLIENT.get(RENDER_URL, timeout=10)
        except Exception as e:
            log.warning("[PING] %s", e)
        await asyncio.sleep(280)

# ── CRYPTO PRICE ──────────────────────────────────────────────────────────────

async def fetch_crypto_price(symbol: str, exchange: str):
    sym = symbol.upper().strip()
    try:
        if exchange == "binance":
            for q in ("USDT", "USDC", "BUSD"):
                try:
                    pair = sym + q
                    r = await HTTP_CLIENT.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": pair})
                    
                    if r.status_code == 200:
                        px = safe_float(r.json().get("price"))
                        if px > 0:
                            return px
                    else:
                        log.warning(f"[BINANCE] Ошибка для {pair}: {r.status_code} - {r.text}")
                except Exception as e:
                    log.warning(f"[BINANCE] Исключение для {sym+q}: {e}")
                    continue

        elif exchange == "bybit":
            r = await HTTP_CLIENT.get("https://api.bybit.com/v5/market/tickers", params={"category": "spot", "symbol": sym + "USDT"})
            r.raise_for_status()
            lst = r.json().get("result", {}).get("list", [])
            if lst:
                px = safe_float(lst[0].get("lastPrice"))
                if px > 0:
                    return px

        elif exchange == "okx":
            r = await HTTP_CLIENT.get("https://www.okx.com/api/v5/market/ticker", params={"instId": sym + "-USDT"})
            r.raise_for_status()
            lst = r.json().get("data", [])
            if lst:
                px = safe_float(lst[0].get("last"))
                if px > 0:
                    return px

        elif exchange == "kucoin":
            r = await HTTP_CLIENT.get("https://api.kucoin.com/api/v1/market/orderbook/level1", params={"symbol": sym + "-USDT"})
            r.raise_for_status()
            d = r.json().get("data") or {}
            px = safe_float(d.get("price"))
            if px > 0:
                return px

        elif exchange == "htx":
            r = await HTTP_CLIENT.get("https://api.huobi.pro/market/detail/merged", params={"symbol": sym.lower() + "usdt"})
            r.raise_for_status()
            tick = r.json().get("tick") or {}
            px = safe_float(tick.get("close"))
            if px > 0:
                return px

        elif exchange == "gate":
            r = await HTTP_CLIENT.get("https://api.gateio.ws/api/v4/spot/tickers", params={"currency_pair": sym + "_USDT"})
            r.raise_for_status()
            lst = r.json()
            if isinstance(lst, list) and lst:
                px = safe_float(lst[0].get("last"))
                if px > 0:
                    return px

        elif exchange == "mexc":
            r = await HTTP_CLIENT.get("https://api.mexc.com/api/v3/ticker/price", params={"symbol": sym + "USDT"})
            r.raise_for_status()
            d = r.json()
            if "code" not in d:
                px = safe_float(d.get("price"))
                if px > 0:
                    return px

        elif exchange == "coinbase":
            r = await HTTP_CLIENT.get(f"https://api.coinbase.com/v2/prices/{sym}-USD/spot")
            r.raise_for_status()
            px = safe_float(r.json().get("data", {}).get("amount"))
            if px > 0:
                return px

        elif exchange == "kraken":
            ksym = "XBT" if sym == "BTC" else sym
            r = await HTTP_CLIENT.get("https://api.kraken.com/0/public/Ticker", params={"pair": ksym + "USD"})
            r.raise_for_status()
            for v in r.json().get("result", {}).values():
                px = safe_float(v["c"][0])
                if px > 0:
                    return px

    except Exception as e:
        log.warning("[PRICE %s/%s] %s", exchange, sym, e)
    return None

# ── COINGECKO 7d HISTORY + CHANGES ───────────────────────────────────────────

COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "TON": "the-open-network",
    "AVAX": "avalanche-2", "DOT": "polkadot", "MATIC": "matic-network", "LINK": "chainlink",
    "UNI": "uniswap", "LTC": "litecoin", "ATOM": "cosmos", "NEAR": "near",
    "OP": "optimism", "ARB": "arbitrum", "APT": "aptos", "SUI": "sui",
    "PEPE": "pepe", "WIF": "dogwifcoin", "TRX": "tron", "FLOKI": "floki",
}

async def fetch_coingecko_data(symbol: str):
    cg_id = COINGECKO_IDS.get(symbol.upper())
    if not cg_id:
        return None, None, None
    try:
        # БЕЗ async with
        r = await HTTP_CLIENT.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
            params={"vs_currency": "usd", "days": "7", "interval": "daily"},
            headers={
                "Accept": "application/json",
                "User-Agent": "CryptoSpace/4.0"
            }
        )
        r.raise_for_status()
        d = r.json()
        prices = d.get("prices", [])
        if len(prices) >= 7:
            days_label = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
            history = [{"day": days_label[i % 7], "price": round(p[1], 6)} for i, p in enumerate(prices[-7:])]
            p_first = prices[-7][1]
            p_last  = prices[-1][1]
            p_prev  = prices[-2][1] if len(prices) >= 2 else p_last
            chg24   = round((p_last - p_prev)  / p_prev  * 100, 2) if p_prev  > 0 else 0.0
            chg7    = round((p_last - p_first) / p_first * 100, 2) if p_first > 0 else 0.0
            return history, chg24, chg7
    except Exception as e:
        log.warning("[COINGECKO %s] %s", symbol, e)
    return None, None, None

# ── COIN META & INFO from CoinGecko ──────────────────────────────────────────

COIN_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "BNB": "BNB", "SOL": "Solana",
    "XRP": "XRP", "ADA": "Cardano", "DOGE": "Dogecoin", "TON": "Toncoin",
    "DOT": "Polkadot", "MATIC": "Polygon", "AVAX": "Avalanche", "LINK": "Chainlink",
    "UNI": "Uniswap", "ATOM": "Cosmos", "LTC": "Litecoin", "TRX": "TRON",
    "NEAR": "NEAR Protocol", "OP": "Optimism", "ARB": "Arbitrum", "APT": "Aptos",
    "SUI": "Sui", "PEPE": "Pepe", "WIF": "dogwifhat", "FLOKI": "Floki",
}

COIN_REVIEWS = {
    "BTC":  ("Bitcoin — цифровое золото. Хранение ценности и защита от инфляции. Лучший выбор для долгосрочного хранения.", "накапливать"),
    "ETH":  ("Ethereum — основа DeFi и NFT. Переход на PoS снизил инфляцию. Хорош для среднесрочных вложений.", "держать"),
    "BNB":  ("BNB — токен экосистемы Binance. Зависит от судьбы биржи, но стабильно держится в топе.", "держать"),
    "SOL":  ("Solana — быстрый и дешёвый L1. Большая активность разработчиков. Высокий потенциал роста.", "купить"),
    "XRP":  ("XRP используется для межбанковских переводов. После победы Ripple в суде — позитивный фон.", "держать"),
    "ADA":  ("Cardano делает ставку на академический подход. Развитие медленное, но основательное.", "держать"),
    "DOGE": ("Dogecoin — мем-монета с сильным сообществом. Высокая волатильность, спекулятивный актив.", "держать"),
    "TON":  ("Toncoin интегрирован в Telegram. Огромная потенциальная аудитория — 900 млн пользователей.", "купить"),
    "AVAX": ("Avalanche — быстрый L1 с суbnets. Активно развивается, хорош для диверсификации портфеля.", "накапливать"),
    "SOL":  ("Solana лидирует по транзакциям. Мощная экосистема NFT и DeFi.", "купить"),
    "LINK": ("Chainlink — стандарт оракулов. Необходим для работы большинства DeFi протоколов.", "накапливать"),
    "LTC":  ("Litecoin — проверенный временем платёжный актив. Низкая комиссия, быстрые транзакции.", "держать"),
    "MATIC":("Polygon — ведущий L2 для Ethereum. Большие партнёрства с корпорациями.", "держать"),
    "UNI":  ("Uniswap — крупнейший DEX. Токен управления с реальной ценностью.", "держать"),
    "NEAR": ("NEAR — удобный L1 для разработчиков. Sharding обеспечивает масштабируемость.", "накапливать"),
    "OP":   ("Optimism — ключевой L2 Ethereum. Рост пользователей ускоряется.", "накапливать"),
    "ARB":  ("Arbitrum — лидер среди L2 по TVL. Активная экосистема DeFi.", "накапливать"),
    "TRX":  ("TRON популярен для стейблкоинов. Большой объём транзакций USDT.", "держать"),
    "PEPE": ("PEPE — крупнейший мем-токен после DOGE. Чисто спекулятивный, высокий риск.", "держать"),
    "WIF":  ("dogwifhat — мем-токен на Solana. Высокая волатильность.", "держать"),
    "APT":  ("Aptos — новый L1 от команды Meta. Современная архитектура Move.", "накапливать"),
    "SUI":  ("Sui — быстрый L1 с объектной моделью данных. Привлекает разработчиков.", "накапливать"),
    "FLOKI":("Floki — мем-токен с нарастающей утилитой. Спекулятивный актив.", "держать"),
    "ATOM": ("Cosmos — экосистема блокчейнов IBC. Рост межсетевого взаимодействия.", "держать"),
    "DOT":  ("Polkadot объединяет блокчейны через parachains. Технически сильный проект.", "держать"),
}

FOREX_REVIEWS = {
    "USD": ("Доллар США — мировая резервная валюта. Лучший выбор для сбережений в кризис.", "держать"),
    "EUR": ("Евро — вторая резервная валюта мира. Стабильна, подходит для диверсификации.", "держать"),
    "GBP": ("Британский фунт — одна из старейших валют. Зависит от политики Банка Англии.", "держать"),
    "UAH": ("Гривна под давлением из-за военного времени. Рекомендуем хранить накопления в USD/EUR.", "продать"),
    "JPY": ("Иена традиционно растёт в кризис как защитный актив.", "держать"),
    "CHF": ("Швейцарский франк — самая надёжная валюта Европы. Отличный защитный актив.", "держать"),
    "KZT": ("Тенге зависит от нефтяных цен и рубля. Умеренный риск.", "держать"),
    "GEL": ("Лари — одна из лучших валют СНГ по стабильности.", "держать"),
}

def build_review(symbol: str, is_forex: bool) -> tuple:
    if is_forex:
        return FOREX_REVIEWS.get(symbol, ("Стабильная валюта для диверсификации портфеля.", "держать"))
    return COIN_REVIEWS.get(symbol, ("Перспективный актив. Следите за объёмами и новостями проекта.", "держать"))


# ── CRYPTO ANALYZE ────────────────────────────────────────────────────────────

async def analyze_crypto(symbol: str, exchange: str, live_price):
    sym = symbol.upper()
    name = COIN_NAMES.get(sym, sym)
    
    # 1. Проверяем, есть ли цена. Если нет — помечаем как None
    has_price = live_price and isinstance(live_price, (int, float)) and live_price > 0
    p = float(live_price) if has_price else None

    # 2. Получаем историю
    history, chg24, chg7 = await fetch_coingecko_data(sym)
    
    if not history:
        days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        base_price = p if p else 0.0
        history = [{"day": d, "price": round(base_price * (0.97 + random.random() * 0.06), 6)} for d in days]
        history[-1]["price"] = base_price
        chg24 = chg7 = 0.0

    if p is not None:
        history[-1]["price"] = p

    review_text, recommendation = build_review(sym, False)

    return {
        "name": name,
        "symbol": sym,
        "exchange": exchange,
        "description": review_text,
        "current_price_usd": p, 
        "price_history_7d": history,
        "change_24h": chg24 or 0.0,
        "change_7d": chg7 or 0.0,
        "forecast": {
            "predicted_7d": round(p * 1.03, 6) if p else 0.0,
            "predicted_30d": round(p * 1.08, 6) if p else 0.0,
            "trend": "bullish" if (chg7 or 0) >= 0 else "bearish",
            "confidence": 62 if p else 0,
            "support": round(p * 0.92, 6) if p else 0.0,
            "resistance": round(p * 1.10, 6) if p else 0.0,
        },
        "ai_analysis": {
            "summary": review_text if p else "Данные о цене временно недоступны.",
            "risks": "Волатильность рынка, регуляторные новости.",
            "opportunity": "Следите за объёмами и уровнями поддержки.",
            "recommendation": recommendation if p else "ожидание",
            "sentiment": "позитивный" if (chg24 or 0) >= 0 else "осторожный",
        },
        "metrics": {
            "volatility": "высокая" if abs(chg24 or 0) > 3 else "средняя",
            "liquidity": "высокая" if p else "нет данных",
        },
    }

# ── FOREX ANALYZE ─────────────────────────────────────────────────────────────

async def fetch_forex_rate(base: str, quote: str = "USD"):
    base = base.upper()
    quote = quote.upper()
    if base == quote:
        return 1.0
    try:
        # Используем глобальный HTTP_CLIENT без async with
        r = await HTTP_CLIENT.get(f"https://open.er-api.com/v6/latest/{base}")
        r.raise_for_status()
        d = r.json()
        if d.get("result") == "success":
            rate = safe_float(d.get("rates", {}).get(quote))
            if rate > 0:
                return rate
        # Второй источник
        r2 = await HTTP_CLIENT.get("https://api.frankfurter.app/latest", params={"from": base, "to": quote})
        r2.raise_for_status()
        rate = safe_float(r2.json().get("rates", {}).get(quote))
        if rate > 0:
            return rate
    except Exception as e:
        log.warning("[FOREX %s/%s] %s", base, quote, e)
    return None

async def analyze_forex(base: str, quote: str, live_rate):
    rate = live_rate if (live_rate and live_rate > 0) else 1.0
    base_name = FOREX_NAMES.get(base, base)
    quote_name = FOREX_NAMES.get(quote, quote)

    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    # Используем глобальный HTTP_CLIENT
    history = [{"day": d, "rate": round(rate * (0.985 + random.random() * 0.03), 6)} for d in days]
    history[-1]["rate"] = rate

    p_first = history[0]["rate"]
    p_last = history[-1]["rate"]
    chg7 = round((p_last - p_first) / p_first * 100, 2) if p_first > 0 else 0.0

    review_text, recommendation = build_review(base, True)

    return {
        "base": base,
        "quote": quote,
        "base_name": base_name,
        "quote_name": quote_name,
        "description": review_text,
        "current_rate": rate,
        "rate_history_7d": history,
        "change_24h": 0.0,
        "change_7d": chg7,
        "forecast": {
            "predicted_7d": round(rate * (1.005 if chg7 >= 0 else 0.995), 6),
            "predicted_30d": round(rate * (1.015 if chg7 >= 0 else 0.985), 6),
            "trend": "bullish" if chg7 >= 0 else "bearish",
            "confidence": 58,
        },
        "ai_analysis": {
            "summary": review_text,
            "factors": "Процентные ставки ЦБ, инфляция, торговый баланс.",
            "recommendation": recommendation,
            "sentiment": "позитивный" if chg7 >= 0 else "осторожный",
        },
    }

# ── COINS LIST FROM EXCHANGE ──────────────────────────────────────────────────

TOP20 = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "TON", "AVAX", "DOT",
         "MATIC", "LINK", "UNI", "LTC", "ATOM", "NEAR", "OP", "ARB", "APT", "SUI"]

async def fetch_exchange_coins(exchange: str):
    try:
        # Используем глобальный HTTP_CLIENT
        async with HTTP_CLIENT as c:
            if exchange == "binance":
                r = await c.get("https://api.binance.com/api/v3/ticker/24hr")
                r.raise_for_status()
                data = r.json()
                coins = []
                seen = set()
                for item in data:
                    s = item.get("symbol", "")
                    if s.endswith("USDT"):
                        sym = s[:-4]
                        if sym not in seen:
                            seen.add(sym)
                            coins.append({
                                "sym": sym,
                                "name": COIN_NAMES.get(sym, sym),
                                "vol": safe_float(item.get("quoteVolume")),
                                "chg": round(safe_float(item.get("priceChangePercent")), 2)
                            })
                coins.sort(key=lambda x: x["vol"], reverse=True)
                return coins[:100]
            if exchange == "bybit":
                r = await c.get("https://api.bybit.com/v5/market/tickers", params={"category": "spot"})
                r.raise_for_status()
                lst = r.json().get("result", {}).get("list", [])
                coins = []
                seen = set()
                for item in lst:
                    s = item.get("symbol", "")
                    if s.endswith("USDT"):
                        sym = s[:-4]
                        if sym not in seen:
                            seen.add(sym)
                            coins.append({
                                "sym": sym,
                                "name": COIN_NAMES.get(sym, sym),
                                "vol": safe_float(item.get("volume24h")),
                                "chg": round(safe_float(item.get("price24hPcnt", "0")) * 100, 2)
                            })
                coins.sort(key=lambda x: x["vol"], reverse=True)
                return coins[:100]
            if exchange == "okx":
                r = await c.get("https://www.okx.com/api/v5/market/tickers", params={"instType": "SPOT"})
                r.raise_for_status()
                lst = r.json().get("data", [])
                coins = []
                seen = set()
                for item in lst:
                    s = item.get("instId", "")
                    if s.endswith("-USDT"):
                        sym = s[:-5]
                        if sym not in seen:
                            seen.add(sym)
                            coins.append({
                                "sym": sym,
                                "name": COIN_NAMES.get(sym, sym),
                                "vol": safe_float(item.get("volCcy24h")),
                                "chg": 0.0
                            })
                coins.sort(key=lambda x: x["vol"], reverse=True)
                return coins[:100]
            if exchange in ("kucoin", "gate", "mexc", "coinbase", "kraken", "htx"):
                # Для остальных — топ20 захардкоженных
                return [{"sym": s, "name": COIN_NAMES.get(s, s), "vol": 0, "chg": 0.0} for s in TOP20]
    except Exception as e:
        log.warning("[COINS LIST %s] %s", exchange, e)
    return [{"sym": s, "name": COIN_NAMES.get(s, s), "vol": 0, "chg": 0.0} for s in TOP20]

# ── PRICE WATCHER ─────────────────────────────────────────────────────────────

async def price_watcher():
    await asyncio.sleep(60)
    log.info("[WATCHER] Запущен")
    while True:
        try:
            if not supabase:
                await asyncio.sleep(300); continue
            rows = supabase.table("crypto_monitors").select("*").execute()
            data = rows.data or []
            log.info("[WATCHER] Проверяем %d записей", len(data))
            for row in data:
                sym = row.get("symbol", "")
                exchange = row.get("exchange", "binance")
                tg_id = row.get("tg_id")
                old_px = safe_float(row.get("last_price") or row.get("price_at_add"))
                alert_pct = safe_float(row.get("alert_pct"), 5.0)

                if not sym or not tg_id or old_px <= 0:
                    continue

                # Форекс: символ типа EUR/USD
                if "/" in sym:
                    parts = sym.split("/")
                    new_px = await fetch_forex_rate(parts[0], parts[1])
                else:
                    new_px = await fetch_crypto_price(sym, exchange)

                if not new_px or new_px <= 0:
                    continue

                change_pct = (new_px - old_px) / old_px * 100

                # Отправляем уведомление, если изменение >= alert_pct
                if abs(change_pct) >= alert_pct:
                    direction = "📈 выросла" if change_pct > 0 else "📉 упала"
                    sign = "\\+" if change_pct > 0 else "\\-"
                    label = sym if "/" in sym else f"{sym} \\({exchange.upper()}\\)"
                    old_f = escape_md2(fmt_price(old_px))
                    new_f = escape_md2(fmt_price(new_px))
                    msg = (
                        f"🔔 *{escape_md2(label)}* {direction} на "
                        f"*{sign}{escape_md2(str(round(abs(change_pct), 2)))}%*\n"
                        f"Было: `{old_f}`\n"
                        f"Сейчас: `{new_f}`\n"
                        f"_Crypto Space_"
                    )
                    if bot:
                        try:
                            await bot.send_message(chat_id=tg_id, text=msg, parse_mode="MarkdownV2")
                            log.info("[ALERT] %s → %s %.2f%%", sym, tg_id, change_pct)
                        except Exception as te:
                            log.warning("[ALERT TG] %s", te)
                    # Обновляем цену и время
                    supabase.table("crypto_monitors").update({
                        "last_price": new_px,
                        "last_alerted": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", row["id"]).execute()
                else:
                    # Обновляем только цену
                    supabase.table("crypto_monitors").update({"last_price": new_px}).eq("id", row["id"]).execute()

                await asyncio.sleep(0.5)
        except Exception as fatal:
            log.error("[WATCHER FATAL] %s", fatal)
        await asyncio.sleep(300)

# ── Бот ───────────────────────────────────────────────────────────────────────

async def run_bot_polling():
    if not bot:
        log.warning("[BOT] Токен не задан"); return
    try:
        await dp.start_polling(bot, handle_signals=False)
    except Exception as e:
        log.error("[BOT] %s", e)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text="🚀 Открыть Crypto Space",
            web_app=types.WebAppInfo(url="https://camorezka.github.io/price-service-site/")
        )
    ]])
    await message.answer(
        "👋 Привет\\! *Crypto Space* — мониторинг крипты и форекса в реальном времени\\.\n\nНажми кнопку ниже 👇",
        reply_markup=kb, parse_mode="MarkdownV2"
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_TG_ID:
        await message.answer("⛔ Нет доступа.")
        return
    await message.answer(
        "🛡 *Админ панель*\n\nОтправь username пользователя \\(без @\\) чтобы получить данные:",
        parse_mode="MarkdownV2"
    )

@dp.message()
async def handle_message(message: types.Message):
    if message.from_user.id != ADMIN_TG_ID or not supabase:
        return
    username = message.text.strip().lstrip("@")
    if not username:
        return
    try:
        res = supabase.table("users").select("*").eq("username", username).execute()
        if not res.data:
            await message.answer(f"❌ Пользователь @{username} не найден в базе.")
            return
        u = res.data[0]
        monitors = supabase.table("crypto_monitors").select("*").eq("tg_id", u["tg_id"]).execute()
        mon_list = monitors.data or []

        full_name = f"{u.get('first_name','')} {u.get('last_name','')}".strip()

        mon_text = "\n".join(
            f"• {m['symbol']} ({m['exchange']})"
            for m in mon_list
        ) if mon_list else "Нет мониторингов"

        
        text = (
            f"👤 *@{escape_md2(username)}*\n"
            f"ID: `{u['tg_id']}`\n"
            f"Имя: {escape_md2(full_name)}\n"
            f"IP рег: `{escape_md2(u.get('reg_ip',''))}`\n"
            f"Последний IP: `{escape_md2(u.get('last_ip',''))}`\n"
            f"Платформа: {escape_md2(u.get('platform',''))}\n"
            f"Язык: {u.get('language','')}\n"
            f"Визитов: {u.get('visit_count',0)}\n"
            f"Регистрация: {escape_md2(str(u.get('created_at',''))[:10])}\n"
            f"Последний вход: {escape_md2(str(u.get('last_seen',''))[:10])}\n"
            f"User\\-Agent: `{escape_md2((u.get('user_agent','') or '')[:60])}`\n\n"
            f"📊 *Мониторинги \\({len(mon_list)}\\):*\n{escape_md2(mon_text)}"
        )
        await message.answer(text, parse_mode="MarkdownV2")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")

# ── STARTUP ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())
    asyncio.create_task(price_watcher())
    asyncio.create_task(run_bot_polling())
    log.info("[STARTUP] OK")

# ── SHUTDOWN ────────────────────────────────────────────────────────────────

@app.on_event("shutdown")
async def shutdown_event():
    await HTTP_CLIENT.aclose()

# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "Crypto Space", "version": "4.0"}

@app.post("/auth")
async def auth(request: Request):
    try:
        data = await request.json()
        tg_id = data.get("id")
        if not tg_id:
            return JSONResponse({"status": "error", "message": "No ID"}, status_code=400)
        if not supabase:
            return JSONResponse({"status": "error", "message": "DB not configured"}, status_code=500)
        now_iso = datetime.now(timezone.utc).isoformat()
        ip = get_client_ip(request)
        existing = supabase.table("users").select("id,visit_count").eq("tg_id", tg_id).execute()
        if existing.data:
            cnt = existing.data[0].get("visit_count") or 0
            supabase.table("users").update({
                "last_ip": ip, "last_seen": now_iso,
                "user_agent": request.headers.get("user-agent", ""),
                "platform": str(data.get("platform") or ""),
                "language": str(data.get("language") or ""),
                "visit_count": cnt + 1,
            }).eq("tg_id", tg_id).execute()
            return {"status": "ok", "already_registered": True}
        # Новая регистрация
        supabase.table("users").insert({
            "tg_id": tg_id,
            "username": str(data.get("username") or ""),
            "first_name": str(data.get("first_name") or ""),
            "last_name": str(data.get("last_name") or ""),
            "reg_ip": ip,
            "last_ip": ip,
            "user_agent": request.headers.get("user-agent", ""),
            "platform": str(data.get("platform") or ""),
            "language": str(data.get("language") or ""),
            "last_seen": now_iso,
            "created_at": now_iso,
            "visit_count": 1,
        }).execute()
        return {"status": "ok", "already_registered": False}
    except Exception as e:
        log.error("[AUTH] %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/analyze")
async def analyze_route(request: Request):
    try:
        data = await request.json()
        symbol = str(data.get("symbol") or "").strip().upper()
        exchange = str(data.get("exchange") or "binance").strip().lower()
        tg_id = data.get("id")
        alert_pct = safe_float(data.get("alert_pct"), 5.0)
        if not symbol:
            return JSONResponse({"status": "error", "message": "Укажите символ"}, status_code=400)
        live_price = await fetch_crypto_price(symbol, exchange)
        result = await analyze_crypto(symbol, exchange, live_price)
        final_px = live_price or result.get("current_price_usd") or 0
        if supabase and tg_id:
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                existing = supabase.table("crypto_monitors").select("id") \
                    .eq("tg_id", tg_id).eq("symbol", symbol).eq("exchange", exchange).execute()
                if existing.data:
                    supabase.table("crypto_monitors").update({
                        "last_price": final_px,
                        "price_at_add": final_px,
                        "alert_pct": alert_pct,
                        "added_at": now_iso,
                    }).eq("id", existing.data[0]["id"]).execute()
                else:
                    supabase.table("crypto_monitors").insert({
                        "tg_id": tg_id,
                        "symbol": symbol,
                        "exchange": exchange,
                        "price_at_add": final_px,
                        "last_price": final_px,
                        "alert_pct": alert_pct,
                        "added_at": now_iso,
                    }).execute()
            except Exception as db_err:
                log.warning("[DB ANALYZE] %s", db_err)
        return {"status": "ok", "data": result}
    except Exception as e:
        log.error("[ANALYZE] %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/analyze-forex")
async def analyze_forex_route(request: Request):
    try:
        data = await request.json()
        base = str(data.get("base") or "").strip().upper()
        quote = str(data.get("quote") or "USD").strip().upper()
        tg_id = data.get("id")
        alert_pct = safe_float(data.get("alert_pct"), 1.0)
        if not base:
            return JSONResponse({"status": "error", "message": "Укажите валюту"}, status_code=400)
        live_rate = await fetch_forex_rate(base, quote)
        result = await analyze_forex(base, quote, live_rate)
        final_r = live_rate or result.get("current_rate") or 0
        if supabase and tg_id:
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                pair_sym = f"{base}/{quote}"
                existing = supabase.table("crypto_monitors").select("id") \
                    .eq("tg_id", tg_id).eq("symbol", pair_sym).execute()
                if existing.data:
                    supabase.table("crypto_monitors").update({
                        "last_price": final_r,
                        "last_alerted": now_iso,
                        "alert_pct": alert_pct,
                        "added_at": now_iso,
                    }).eq("id", existing.data[0]["id"]).execute()
                else:
                    supabase.table("crypto_monitors").insert({
                        "tg_id": tg_id,
                        "symbol": pair_sym,
                        "exchange": "forex",
                        "price_at_add": final_r,
                        "last_price": final_r,
                        "alert_pct": alert_pct,
                        "added_at": now_iso,
                    }).execute()
            except Exception as db_err:
                log.warning("[DB FOREX] %s", db_err)
        return {"status": "ok", "data": result}
    except Exception as e:
        log.error("[FOREX ANALYZE] %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/coins/{exchange}")
async def get_coins(exchange: str):
    coins = await fetch_exchange_coins(exchange.lower())
    return {"status": "ok", "exchange": exchange, "coins": coins}

@app.get("/price/{exchange}/{symbol}")
async def get_price(exchange: str, symbol: str):
    price = await fetch_crypto_price(symbol.upper(), exchange.lower())
    if price is None:
        return JSONResponse({"status": "error", "message": "Нет данных"}, status_code=404)
    return {"status": "ok", "price": price, "symbol": symbol.upper(), "exchange": exchange}

@app.get("/forex/{base}/{quote}")
async def get_forex(base: str, quote: str = "USD"):
    rate = await fetch_forex_rate(base.upper(), quote.upper())
    if rate is None:
        return JSONResponse({"status": "error", "message": "Нет данных"}, status_code=404)
    return {"status": "ok", "rate": rate, "base": base.upper(), "quote": quote.upper()}

@app.get("/admin/user/{username}")
async def admin_get_user(username: str, request: Request):
    key = request.query_params.get("key", "")
    if key != ADMIN_SECRET:
        return JSONResponse({"status": "error"}, status_code=403)
    if not supabase:
        return JSONResponse({"status": "error", "message": "No DB"}, status_code=500)
    res = supabase.table("users").select("*").eq("username", username.lstrip("@")).execute()
    if not res.data:
        return JSONResponse({"status": "error", "message": "Not found"}, status_code=404)
    u = res.data[0]
    monitors = supabase.table("crypto_monitors").select("*").eq("tg_id", u["tg_id"]).execute()
    return {"status": "ok", "user": u, "monitors": monitors.data or []}


@app.post("/activate-monitor")
async def activate_monitor(request: Request):
    try:
        data = await request.json()
        tg_id = data.get("tg_id")
        symbol = str(data.get("symbol") or "").strip().upper()
        exchange = str(data.get("exchange") or "").strip().lower()
        if not tg_id or not symbol or not exchange:
            return JSONResponse({"status": "error", "message": "Не хватает параметров"}, status_code=400)
        if not supabase:
            return JSONResponse({"status": "error", "message": "DB не настроена"}, status_code=500)

        now = datetime.now(timezone.utc)

        # Проверяем запись
        existing = supabase.table("crypto_monitors").select("*") \
            .eq("tg_id", tg_id).eq("symbol", symbol).eq("exchange", exchange).execute()

        if existing.data:
            m = existing.data[0]
            # Если прошло 7 дней — сбрасываем счётчик
            expires_at = m.get("expires_at")
            if expires_at:
                from dateutil.parser import parse as parse_dt
                exp = parse_dt(expires_at)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if now > exp:
                    # Сброс — новая неделя
                    supabase.table("crypto_monitors").update({
                        "alerts_count": 1,
                        "expires_at": (now + __import__('datetime').timedelta(days=7)).isoformat(),
                        "last_alerted": now.isoformat(),
                        "price_at_add": m.get("last_price", 0),
                    }).eq("id", m["id"]).execute()
                    return {"status": "ok", "remaining": 2}

            count = m.get("alerts_count") or 0
            if count >= 3:
                return {"status": "error", "message": "Лимит исчерпан — 3 запуска в неделю"}

            new_count = count + 1
            supabase.table("crypto_monitors").update({
                "alerts_count": new_count,
                "last_alerted": now.isoformat(),
            }).eq("id", m["id"]).execute()
            return {"status": "ok", "remaining": 3 - new_count}
        else:
            # Новая запись
            from datetime import timedelta
            supabase.table("crypto_monitors").insert({
                "tg_id": tg_id,
                "symbol": symbol,
                "exchange": exchange,
                "alerts_count": 1,
                "expires_at": (now + timedelta(days=7)).isoformat(),
                "last_alerted": now.isoformat(),
                "price_at_add": 0,
                "last_price": 0,
                "alert_pct": 5,
            }).execute()
            return {"status": "ok", "remaining": 2}
    except Exception as e:
        log.error("[ACTIVATE-MONITOR] %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/check-sub")
async def check_sub(tg_id: int):
    """Проверка подписки на канал @MonitorSpace"""
    if not bot:
        return {"subscribed": True}
    try:
        member = await bot.get_chat_member(chat_id="@MonitorSpace", user_id=tg_id)
        subscribed = member.status not in ("left", "kicked", "banned")
        return {"subscribed": subscribed}
    except Exception as e:
        log.warning("[CHECK-SUB] %s", e)
        return {"subscribed": True}  # при ошибке пускаем




if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
