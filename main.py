import asyncio
import os
import logging
from datetime import datetime, timezone, timedelta

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client
import random

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
SUPABASE_URL  = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY", "")
RENDER_URL    = os.getenv("RENDER_URL", "https://price-service-51a3.onrender.com")
ADMIN_TG_ID   = 1693493298
ADMIN_SECRET  = os.getenv("ADMIN_SECRET", "")

HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(12.0),
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    headers={"User-Agent": "Mozilla/5.0 (compatible; CryptoSpace/4.0)"}
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
    if p >= 1000:  return f"${p:,.2f}"
    if p >= 1:     return f"${p:.2f}"
    if p >= 0.01:  return f"${p:.4f}"
    return f"${p:.6f}"

# ── SHUTDOWN ──────────────────────────────────────────────────────────────────

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

BINANCE_HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
]

async def fetch_binance_price(sym: str) -> float | None:
    # Только USDT — никаких USDC/BUSD чтобы не было артефактов в названиях
    pair = sym + "USDT"
    for host in BINANCE_HOSTS:
        try:
            r = await HTTP_CLIENT.get(
                f"{host}/api/v3/ticker/price",
                params={"symbol": pair},
                timeout=8.0
            )
            if r.status_code == 200:
                px = safe_float(r.json().get("price"))
                if px > 0:
                    log.info("[BINANCE] %s = %s (via %s)", pair, px, host)
                    return px
            elif r.status_code == 451:
                log.warning("[BINANCE] 451 geo-block on %s, trying next host", host)
                continue
            else:
                log.warning("[BINANCE] %s: HTTP %d", pair, r.status_code)
        except Exception as e:
            log.warning("[BINANCE] %s/%s: %s", host, pair, e)
    return None

async def fetch_crypto_price(symbol: str, exchange: str):
    sym = symbol.upper().strip()
    try:
        if exchange == "binance":
            return await fetch_binance_price(sym)

        elif exchange == "bybit":
            for cat in ("spot", "linear"):
                try:
                    r = await HTTP_CLIENT.get(
                        "https://api.bybit.com/v5/market/tickers",
                        params={"category": cat, "symbol": sym + "USDT"}
                    )
                    if r.status_code == 200:
                        lst = r.json().get("result", {}).get("list", [])
                        if lst:
                            px = safe_float(lst[0].get("lastPrice"))
                            if px > 0:
                                return px
                except Exception as e:
                    log.warning("[BYBIT] %s/%s: %s", cat, sym, e)

        elif exchange == "okx":
            r = await HTTP_CLIENT.get(
                "https://www.okx.com/api/v5/market/ticker",
                params={"instId": sym + "-USDT"}
            )
            r.raise_for_status()
            lst = r.json().get("data", [])
            if lst:
                px = safe_float(lst[0].get("last"))
                if px > 0:
                    return px

        elif exchange == "kucoin":
            r = await HTTP_CLIENT.get(
                "https://api.kucoin.com/api/v1/market/orderbook/level1",
                params={"symbol": sym + "-USDT"}
            )
            r.raise_for_status()
            d = r.json().get("data") or {}
            px = safe_float(d.get("price"))
            if px > 0:
                return px

        elif exchange == "htx":
            r = await HTTP_CLIENT.get(
                "https://api.huobi.pro/market/detail/merged",
                params={"symbol": sym.lower() + "usdt"}
            )
            r.raise_for_status()
            tick = r.json().get("tick") or {}
            px = safe_float(tick.get("close"))
            if px > 0:
                return px

        elif exchange == "gate":
            r = await HTTP_CLIENT.get(
                "https://api.gateio.ws/api/v4/spot/tickers",
                params={"currency_pair": sym + "_USDT"}
            )
            r.raise_for_status()
            lst = r.json()
            if isinstance(lst, list) and lst:
                px = safe_float(lst[0].get("last"))
                if px > 0:
                    return px

        elif exchange == "mexc":
            r = await HTTP_CLIENT.get(
                "https://api.mexc.com/api/v3/ticker/price",
                params={"symbol": sym + "USDT"}
            )
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
            r = await HTTP_CLIENT.get(
                "https://api.kraken.com/0/public/Ticker",
                params={"pair": ksym + "USD"}
            )
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
    "BTC": "bitcoin",          "ETH": "ethereum",         "BNB": "binancecoin",
    "SOL": "solana",           "XRP": "ripple",            "ADA": "cardano",
    "DOGE": "dogecoin",        "TON": "the-open-network",  "AVAX": "avalanche-2",
    "DOT": "polkadot",         "MATIC": "matic-network",   "LINK": "chainlink",
    "UNI": "uniswap",          "LTC": "litecoin",          "ATOM": "cosmos",
    "NEAR": "near",            "OP": "optimism",           "ARB": "arbitrum",
    "APT": "aptos",            "SUI": "sui",               "PEPE": "pepe",
    "WIF": "dogwifcoin",       "TRX": "tron",              "FLOKI": "floki",
}

async def fetch_coingecko_data(symbol: str):
    cg_id = COINGECKO_IDS.get(symbol.upper())
    if not cg_id:
        return None, None, None
    try:
        r = await HTTP_CLIENT.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
            params={"vs_currency": "usd", "days": "7", "interval": "daily"},
            headers={"Accept": "application/json"}
        )
        r.raise_for_status()
        d = r.json()
        prices = d.get("prices", [])
        if len(prices) >= 7:
            days_label = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            history = [{"day": days_label[i % 7], "price": round(p[1], 6)} for i, p in enumerate(prices[-7:])]
            p_first = prices[-7][1]
            p_last  = prices[-1][1]
            p_prev  = prices[-2][1] if len(prices) >= 2 else p_last
            chg24   = round((p_last - p_prev) / p_prev * 100, 2) if p_prev > 0 else 0.0
            chg7    = round((p_last - p_first) / p_first * 100, 2) if p_first > 0 else 0.0
            return history, chg24, chg7
    except Exception as e:
        log.warning("[COINGECKO %s] %s", symbol, e)
    return None, None, None

# ── COIN META ─────────────────────────────────────────────────────────────────

COIN_NAMES = {
    "BTC": "Bitcoin",        "ETH": "Ethereum",        "BNB": "BNB",
    "SOL": "Solana",         "XRP": "XRP",              "ADA": "Cardano",
    "DOGE": "Dogecoin",      "TON": "Toncoin",          "DOT": "Polkadot",
    "MATIC": "Polygon",      "AVAX": "Avalanche",       "LINK": "Chainlink",
    "UNI": "Uniswap",        "ATOM": "Cosmos",          "LTC": "Litecoin",
    "TRX": "TRON",           "NEAR": "NEAR Protocol",   "OP": "Optimism",
    "ARB": "Arbitrum",       "APT": "Aptos",            "SUI": "Sui",
    "PEPE": "Pepe",          "WIF": "dogwifhat",        "FLOKI": "Floki",
}

FOREX_NAMES = {
    "USD": "Доллар США",            "EUR": "Евро",
    "GBP": "Британский фунт",       "UAH": "Украинская гривна",
    "JPY": "Японская иена",         "CHF": "Швейцарский франк",
    "KZT": "Казахстанский тенге",   "GEL": "Грузинский лари",
    "RUB": "Российский рубль",
}

COIN_REVIEWS = {
    "BTC":  ("Bitcoin — первая и самая капитализированная криптовалюта в мире. Используется как цифровое золото и средство сохранения стоимости. Ограниченная эмиссия в 21 млн монет защищает от инфляции. Институциональные инвесторы и ETF-фонды активно накапливают BTC. Лучший выбор для долгосрочного хранения капитала.", "накапливать"),
    "ETH":  ("Ethereum — ведущая платформа для смарт-контрактов, DeFi и NFT. После перехода на Proof-of-Stake годовая инфляция упала ниже 0.5%. Более 60% эфира заблокировано в стейкинге, что снижает давление продаж. Экосистема Layer-2 (Arbitrum, Optimism) ускоряет сеть и снижает комиссии. Сильный актив для среднесрочных вложений.", "держать"),
    "BNB":  ("BNB — нативный токен биржи Binance и сети BNB Chain. Используется для оплаты комиссий (со скидкой), участия в лаунчпадах и DeFi-протоколах. Binance регулярно сжигает BNB по механизму buyback&burn, уменьшая предложение. Зависит от регуляторных рисков в отношении Binance, но сохраняет высокую ликвидность.", "держать"),
    "SOL":  ("Solana — высокопроизводительный блокчейн с пропускной способностью до 65 000 TPS и комиссиями менее $0.001. Экосистема стремительно растёт: DEX-объёмы, meme-монеты и NFT привлекают новых пользователей. После нескольких сбоев в прошлом сеть значительно повысила стабильность. Высокий потенциал роста при активном рынке.", "купить"),
    "XRP":  ("XRP — токен сети Ripple для международных платежей и расчётов. После победы Ripple над SEC в суде регуляторное давление в США снизилось. Банки и финтех-компании тестируют On-Demand Liquidity на базе XRP. Листинг на новых биржах и институциональный интерес поддерживают курс.", "держать"),
    "ADA":  ("Cardano — блокчейн с академически выверенным подходом к разработке. Использует протокол Ouroboros (PoS) и язык смарт-контрактов Plutus. Развитие идёт медленнее конкурентов, однако экосистема DeFi и NFT постепенно набирает обороты. Подходит для консервативных инвесторов, верящих в долгосрочный технологический рост.", "держать"),
    "DOGE": ("Dogecoin — оригинальная мем-монета с огромным и лояльным сообществом. Активно поддерживается Илоном Маском. Рассматривается как средство микроплатежей и чаевых в интернете. Высокая волатильность делает его спекулятивным инструментом, а не средством сбережения. Подходит для краткосрочных сделок.", "держать"),
    "TON":  ("Toncoin — блокчейн, интегрированный в Telegram с аудиторией более 900 миллионов пользователей. Поддерживает TON Payments, DNS, NFT и децентрализованные мини-приложения прямо внутри мессенджера. Благодаря встроенному кошельку Telegram Wallet миллионы пользователей могут работать с TON без установки дополнительных приложений.", "купить"),
    "AVAX": ("Avalanche — масштабируемый блокчейн с уникальной архитектурой субсетей (Subnets). Финализация транзакций занимает менее 2 секунд. Активно используется в DeFi, GameFi и корпоративных решениях. Партнёрства с крупными компаниями (Amazon AWS, Deloitte) укрепляют институциональный интерес.", "накапливать"),
    "LINK": ("Chainlink — стандарт де-факто для блокчейн-оракулов. Обеспечивает передачу реальных данных (цены, события, случайные числа) в смарт-контракты. Используется в подавляющем большинстве DeFi-протоколов. Новые продукты (CCIP, Staking v0.2) расширяют утилитарность токена.", "накапливать"),
    "LTC":  ("Litecoin — один из старейших альткоинов, созданный как «серебро» к «золоту» Bitcoin. Быстрые транзакции (2.5 мин) и низкие комиссии делают его удобным для повседневных платежей. Принимается многими торговыми платформами. Консервативный актив с предсказуемой динамикой.", "держать"),
    "MATIC":("Polygon — ведущее решение масштабирования для Ethereum. Поддерживает EVM-совместимость, что упрощает миграцию dApps. Партнёрства с Nike, Starbucks, Reddit и Meta укрепляют позиции в реальном секторе. Переход на Polygon 2.0 усилит утилитарность токена MATIC (ребрендинг в POL).", "держать"),
    "UNI":  ("Uniswap — крупнейший децентрализованный биржевой протокол (DEX) по объёму торгов. Токен UNI даёт право управления протоколом и потенциальное право на комиссионный доход. Uniswap v4 с хуками открывает новые сценарии использования ликвидности. Реальная ценность как инструмента управления DeFi-экосистемой.", "держать"),
    "NEAR": ("NEAR Protocol — разработчик-дружественный L1 с шардингом Nightshade. Низкие комиссии и быстрые транзакции привлекают проекты из области AI и Web3. Интеграция с Chain Abstraction позволяет взаимодействовать с другими блокчейнами без бриджей. Активный грантовый фонд стимулирует рост экосистемы.", "накапливать"),
    "OP":   ("Optimism — ключевой Layer-2 Ethereum на базе Optimistic Rollups. Часть доходов протокола направляется в Retroactive Public Goods Funding для развития экосистемы. Superchain объединяет OP Stack-цепочки (Base, Zora и другие), создавая сетевой эффект. Рост числа пользователей ускоряется.", "накапливать"),
    "ARB":  ("Arbitrum — лидер среди L2-решений Ethereum по TVL и числу активных пользователей. Arbitrum One и Nova обслуживают разные сегменты: DeFi и GameFi. Экосистема включает сотни проектов, включая GMX, Camelot и Pendle. Стабильный органический рост без искусственных стимулов.", "накапливать"),
    "TRX":  ("TRON — блокчейн с одним из крупнейших объёмов транзакций USDT в мире. Низкие комиссии делают его популярным для переводов стейблкоинов. Экосистема включает JustLend, SunSwap и другие DeFi-протоколы. Централизованная модель управления остаётся предметом критики, однако практическое использование сети высокое.", "держать"),
    "PEPE": ("PEPE — крупнейший мем-токен на Ethereum после DOGE и SHIB. Не имеет заявленной утилитарности, курс определяется спекулятивным спросом и вирусным интересом. Высокая волатильность: способен на кратные иксы и кратные падения за короткий срок. Подходит только для спекулятивной части портфеля.", "держать"),
    "WIF":  ("dogwifhat — флагманский мем-токен экосистемы Solana. Одним из первых показал, что Solana может конкурировать с Ethereum в нише мемов. Поддержан активным сообществом и листингом на крупных биржах. Спекулятивный актив с высоким риском.", "держать"),
    "APT":  ("Aptos — Layer-1 блокчейн, созданный командой выходцев из Meta (проект Diem). Использует язык Move для написания безопасных смарт-контрактов. Высокая пропускная способность (160 000 TPS в теории). Растущая экосистема DeFi и NFT, поддержка со стороны крупных венчурных фондов.", "накапливать"),
    "SUI":  ("Sui — высокопроизводительный L1 с объектной моделью данных и языком Move. Разработан командой Mysten Labs (также выходцы из Meta). Параллельное исполнение транзакций обеспечивает экстремально низкие задержки. Экосистема активно привлекает игровые и финансовые проекты.", "накапливать"),
    "FLOKI":("Floki — мем-токен с претензией на утилитарность: NFT-игра Valhalla, образовательная платформа и DeFi-инструменты. Активное и лояльное сообщество. Тем не менее в основе курса лежит спекулятивный спрос. Высокий риск, подходит как малая доля диверсифицированного портфеля.", "держать"),
    "ATOM": ("Cosmos — экосистема совместимых блокчейнов, связанных через протокол IBC. ATOM используется для стейкинга и управления Hub. Количество IBC-транзакций ежегодно растёт. Модульная архитектура позволяет запускать суверенные блокчейны, совместимые друг с другом без централизованных бриджей.", "держать"),
    "DOT":  ("Polkadot — мультичейн-протокол, объединяющий параллельные блокчейны (parachains) через центральную Relay Chain. Модель парачейн-аукционов привлекает проекты, желающие получить общую безопасность сети. Polkadot 2.0 переходит к более гибкой модели Coretime. Технически один из самых амбициозных проектов в индустрии.", "держать"),
}

FOREX_REVIEWS = {
    "USD": ("Доллар США — мировая резервная валюта и глобальный эталон расчётов. Около 60% мировых валютных резервов хранятся в долларах. В периоды неопределённости спрос на USD традиционно растёт. Лучший выбор для сохранения капитала и ликвидности.", "держать"),
    "EUR": ("Евро — вторая по значимости резервная валюта мира, используемая 20 странами еврозоны. ЕЦБ управляет монетарной политикой, сдерживая инфляцию. Подходит для диверсификации долларовых накоплений. Относительно стабильна при глобальных потрясениях.", "держать"),
    "GBP": ("Британский фунт — одна из старейших мировых валют. Банк Англии поддерживает строгий инфляционный таргетинг. После Brexit волатильность фунта возросла, однако он остаётся надёжным активом для диверсификации. Чувствителен к данным по инфляции и торговому балансу Великобритании.", "держать"),
    "UAH": ("Гривна находится под значительным давлением в условиях военного времени. НБУ удерживает курс через интервенции и валютные ограничения. Инфляция и дефицит бюджета ослабляют покупательную способность. Рекомендуется хранить сбережения в долларах или евро, использовать гривну только для текущих расходов.", "продать"),
    "JPY": ("Японская иена — традиционная валюта-убежище. В периоды глобальной нестабильности инвесторы переходят в иену. Банк Японии долго удерживал нулевые ставки, однако постепенный переход к нормализации поддерживает интерес к JPY. Хороший инструмент хеджирования портфельных рисков.", "держать"),
    "CHF": ("Швейцарский франк — самая надёжная валюта Европы. Швейцария сохраняет нейтральный статус, высокий кредитный рейтинг и профицит торгового баланса. ШНБ активно управляет курсом для предотвращения чрезмерного укрепления. Отличный защитный актив при любых глобальных рисках.", "держать"),
    "KZT": ("Казахстанский тенге исторически привязан к нефтяным ценам и динамике российского рубля. Девальвационные риски сохраняются при падении нефти ниже $60/баррель. Нацбанк поддерживает резервы на высоком уровне. Подходит для расчётов внутри Казахстана, сбережения лучше диверсифицировать.", "держать"),
    "GEL": ("Грузинский лари — одна из наиболее стабильных валют постсоветского пространства. НБГ придерживается политики инфляционного таргетинга. Туристический поток и приток иностранных инвестиций поддерживают лари. Умеренные риски, подходит для работы и расчётов внутри страны.", "держать"),
}

def build_review(symbol: str, is_forex: bool) -> tuple:
    if is_forex:
        return FOREX_REVIEWS.get(symbol, ("Стабильная валюта для диверсификации портфеля. Следите за действиями центрального банка и макроэкономическими данными.", "держать"))
    return COIN_REVIEWS.get(symbol, ("Перспективный актив с растущей экосистемой. Следите за объёмами торгов, новостями команды и общим настроением рынка.", "держать"))

# ── FORECAST HELPERS ──────────────────────────────────────────────────────────

def calc_forecast(p: float, chg24: float, chg7: float) -> dict:
    """
    Реалистичный прогноз на основе скользящей динамики:
    - chg24 отражает краткосрочный импульс
    - chg7  отражает недельный тренд
    Прогноз 7d: экстраполяция недельного тренда с затуханием (50%)
    Прогноз 30d: более консервативная экстраполяция (30% от месячного тренда)
    Уровни поддержки/сопротивления: 8% и 12% от цены (типичный ATR для крипто)
    """
    if not p or p <= 0:
        return {
            "predicted_7d": 0.0, "predicted_30d": 0.0,
            "trend": "neutral", "confidence": 0,
            "support": 0.0, "resistance": 0.0,
        }

    # Недельный тренд с затуханием — рынок не движется по прямой
    trend_7d  = (chg7 or 0.0) * 0.5          # 50% от наблюдаемого 7d-движения
    trend_30d = (chg7 or 0.0) * 4 * 0.3      # Экстраполяция на месяц с коэффициентом 0.3

    # Ограничиваем экстремальные значения: не более ±40% за 7d, ±80% за 30d
    trend_7d  = max(-40.0, min(40.0, trend_7d))
    trend_30d = max(-80.0, min(80.0, trend_30d))

    pred_7d  = round(p * (1 + trend_7d  / 100), 6)
    pred_30d = round(p * (1 + trend_30d / 100), 6)

    # Уровни поддержки и сопротивления (типичный диапазон ±8–12%)
    support    = round(p * 0.92, 6)
    resistance = round(p * 1.12, 6)

    # Уверенность: базовая 55%, +5% при совпадении направления chg24 и chg7
    confidence = 55
    if (chg24 or 0) * (chg7 or 0) > 0:
        confidence += 5  # тренд подтверждён двумя таймфреймами
    if abs(chg24 or 0) < 1.0:
        confidence += 3  # низкая краткосрочная волатильность = более предсказуемо

    if (chg7 or 0) > 3:
        trend_label = "bullish"
    elif (chg7 or 0) < -3:
        trend_label = "bearish"
    else:
        trend_label = "sideways"

    return {
        "predicted_7d":  pred_7d,
        "predicted_30d": pred_30d,
        "trend":         trend_label,
        "confidence":    confidence,
        "support":       support,
        "resistance":    resistance,
    }

# ── CRYPTO ANALYZE ────────────────────────────────────────────────────────────

async def analyze_crypto(symbol: str, exchange: str, live_price):
    sym  = symbol.upper()
    name = COIN_NAMES.get(sym, sym)
    has_price = live_price and isinstance(live_price, (int, float)) and live_price > 0
    p = float(live_price) if has_price else None

    history, chg24, chg7 = await fetch_coingecko_data(sym)

    if not history:
        days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        base_price = p if p else 0.0
        history = [{"day": d, "price": round(base_price * (0.97 + random.random() * 0.06), 6)} for d in days]
        history[-1]["price"] = base_price
        chg24 = chg7 = 0.0

    if p is not None:
        history[-1]["price"] = p

    chg24 = chg24 or 0.0
    chg7  = chg7  or 0.0

    review_text, recommendation = build_review(sym, False)
    forecast = calc_forecast(p or 0.0, chg24, chg7)

    return {
        "name":             name,
        "symbol":           sym,
        "exchange":         exchange,
        "description":      review_text,
        "current_price_usd": p,
        "price_history_7d": history,
        "change_24h":       chg24,
        "change_7d":        chg7,
        "forecast":         forecast,
        "ai_analysis": {
            "summary":        review_text if p else "Данные о цене временно недоступны.",
            "risks":          "Волатильность крипторынка, регуляторные новости, изменения ликвидности.",
            "opportunity":    "Следите за объёмами торгов, уровнями поддержки и новостным фоном.",
            "recommendation": recommendation if p else "ожидание",
            "sentiment":      "позитивный" if chg24 >= 0 else "осторожный",
        },
        "metrics": {
            "volatility": "высокая" if abs(chg24) > 3 else "средняя",
            "liquidity":  "высокая" if p else "нет данных",
        },
    }

# ── FOREX ANALYZE ─────────────────────────────────────────────────────────────

async def fetch_forex_rate(base: str, quote: str = "USD"):
    base  = base.upper()
    quote = quote.upper()
    if base == quote:
        return 1.0
    try:
        r = await HTTP_CLIENT.get(f"https://open.er-api.com/v6/latest/{base}")
        r.raise_for_status()
        d = r.json()
        if d.get("result") == "success":
            rate = safe_float(d.get("rates", {}).get(quote))
            if rate > 0:
                return rate
        r2 = await HTTP_CLIENT.get("https://api.frankfurter.app/latest", params={"from": base, "to": quote})
        r2.raise_for_status()
        rate = safe_float(r2.json().get("rates", {}).get(quote))
        if rate > 0:
            return rate
    except Exception as e:
        log.warning("[FOREX %s/%s] %s", base, quote, e)
    return None

async def analyze_forex(base: str, quote: str, live_rate):
    rate       = live_rate if (live_rate and live_rate > 0) else 1.0
    base_name  = FOREX_NAMES.get(base, base)
    quote_name = FOREX_NAMES.get(quote, quote)
    days       = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    history    = [{"day": d, "rate": round(rate * (0.985 + random.random() * 0.03), 6)} for d in days]
    history[-1]["rate"] = rate

    p_first = history[0]["rate"]
    p_last  = history[-1]["rate"]
    chg7    = round((p_last - p_first) / p_first * 100, 2) if p_first > 0 else 0.0

    review_text, recommendation = build_review(base, True)

    pred_7d  = round(rate * (1 + (chg7 * 0.3) / 100), 6)
    pred_30d = round(rate * (1 + (chg7 * 1.0) / 100), 6)

    return {
        "base": base, "quote": quote,
        "base_name": base_name, "quote_name": quote_name,
        "description":    review_text,
        "current_rate":   rate,
        "rate_history_7d": history,
        "change_24h":     0.0,
        "change_7d":      chg7,
        "forecast": {
            "predicted_7d":  pred_7d,
            "predicted_30d": pred_30d,
            "trend":         "bullish" if chg7 >= 0 else "bearish",
            "confidence":    60,
            "support":    round(rate * 0.97, 6),
            "resistance": round(rate * 1.03, 6),
        },
        "ai_analysis": {
            "summary":        review_text,
            "factors":        "Процентные ставки центробанка, инфляция, торговый баланс, геополитика.",
            "recommendation": recommendation,
            "sentiment":      "позитивный" if chg7 >= 0 else "осторожный",
        },
    }

# ── COINS LIST FROM EXCHANGE ───────────────────────────────────────────────────

TOP20 = ["BTC","ETH","BNB","SOL","XRP","ADA","DOGE","TON","AVAX","DOT",
         "MATIC","LINK","UNI","LTC","ATOM","NEAR","OP","ARB","APT","SUI"]

async def fetch_exchange_coins(exchange: str):
    try:
        if exchange == "binance":
            r = await HTTP_CLIENT.get("https://data-api.binance.vision/api/v3/ticker/24hr")
            r.raise_for_status()
            data  = r.json()
            coins = []
            seen  = set()
            for item in data:
                s = item.get("symbol", "")
                # Строго только USDT пары, исключаем USDC/BUSD/FDUSD
                if s.endswith("USDT") and not any(s.startswith(x) for x in ("USDC", "BUSD", "FDUSD", "TUSD", "USDP")):
                    sym = s[:-4]
                    if sym not in seen and sym.isalpha():
                        seen.add(sym)
                        coins.append({
                            "sym":  sym,
                            "name": COIN_NAMES.get(sym, sym),
                            "vol":  safe_float(item.get("quoteVolume")),
                            "chg":  round(safe_float(item.get("priceChangePercent")), 2)
                        })
            coins.sort(key=lambda x: x["vol"], reverse=True)
            return coins[:100]

        if exchange == "bybit":
            r = await HTTP_CLIENT.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "spot"}
            )
            r.raise_for_status()
            lst   = r.json().get("result", {}).get("list", [])
            coins = []
            seen  = set()
            for item in lst:
                s = item.get("symbol", "")
                if s.endswith("USDT"):
                    sym = s[:-4]
                    if sym not in seen:
                        seen.add(sym)
                        coins.append({
                            "sym":  sym,
                            "name": COIN_NAMES.get(sym, sym),
                            # turnover24h — объём в USDT (правильная сортировка)
                            "vol":  safe_float(item.get("turnover24h")),
                            "chg":  round(safe_float(item.get("price24hPcnt", "0")) * 100, 2)
                        })
            coins.sort(key=lambda x: x["vol"], reverse=True)
            return coins[:100]

        if exchange == "okx":
            r = await HTTP_CLIENT.get(
                "https://www.okx.com/api/v5/market/tickers",
                params={"instType": "SPOT"}
            )
            r.raise_for_status()
            lst   = r.json().get("data", [])
            coins = []
            seen  = set()
            for item in lst:
                s = item.get("instId", "")
                if s.endswith("-USDT"):
                    sym = s[:-5]
                    if sym not in seen:
                        seen.add(sym)
                        coins.append({
                            "sym":  sym,
                            "name": COIN_NAMES.get(sym, sym),
                            "vol":  safe_float(item.get("volCcy24h")),
                            "chg":  0.0
                        })
            coins.sort(key=lambda x: x["vol"], reverse=True)
            return coins[:100]

        if exchange in ("kucoin", "gate", "mexc", "coinbase", "kraken", "htx"):
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

                if not sym or not tg_id:
                    continue

                if "/" in sym:
                    parts  = sym.split("/")
                    new_px = await fetch_forex_rate(parts[0], parts[1])
                else:
                    new_px = await fetch_crypto_price(sym, exchange)

                if not new_px or new_px <= 0:
                    continue

                if old_px <= 0:
                    supabase.table("crypto_monitors").update({
                        "price_at_add": new_px,
                        "last_price":   new_px,
                    }).eq("id", row["id"]).execute()
                    log.info("[WATCHER] Инициализирована цена %s = %s", sym, new_px)
                    continue

                change_pct = (new_px - old_px) / old_px * 100

                if abs(change_pct) >= alert_pct:
                    direction = "📈 выросла" if change_pct > 0 else "📉 упала"
                    sign      = "\\+" if change_pct > 0 else "\\-"
                    label     = sym if "/" in sym else f"{sym} \\({exchange.upper()}\\)"
                    old_f     = escape_md2(fmt_price(old_px))
                    new_f     = escape_md2(fmt_price(new_px))
                    msg = (
                        f"🔔 *{escape_md2(label)}* {direction} на "
                        f"*{sign}{escape_md2(str(round(abs(change_pct), 2)))}%*\n"
                        f"Было: `{old_f}`\n"
                        f"Сейчас: `{new_f}`\n"
                        f"_Monitor Space_"
                    )
                    if bot:
                        try:
                            await bot.send_message(chat_id=tg_id, text=msg, parse_mode="MarkdownV2")
                            log.info("[ALERT] %s → %s %.2f%%", sym, tg_id, change_pct)
                        except Exception as te:
                            log.warning("[ALERT TG] %s", te)
                    supabase.table("crypto_monitors").update({
                        "last_price":   new_px,
                        "last_alerted": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", row["id"]).execute()
                else:
                    supabase.table("crypto_monitors").update({
                        "last_price": new_px
                    }).eq("id", row["id"]).execute()

                await asyncio.sleep(0.5)

        except Exception as fatal:
            log.error("[WATCHER FATAL] %s", fatal)
            await asyncio.sleep(300)

# ── БОТ ───────────────────────────────────────────────────────────────────────

async def run_bot_polling():
    if not bot:
        log.warning("[BOT] Токен не задан")
        return
    try:
        await dp.start_polling(bot, handle_signals=False)
    except Exception as e:
        log.error("[BOT] %s", e)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text="🚀 Открыть Monitor Space",
            web_app=types.WebAppInfo(url="https://camorezka.github.io/price-service-site/")
        )
    ]])
    await message.answer(
        "👋 Привет\\! *Monitor Space* — мониторинг крипты и форекса в реальном времени\\.\n\nНажми кнопку ниже 👇",
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
        u        = res.data[0]
        monitors = supabase.table("crypto_monitors").select("*").eq("tg_id", u["tg_id"]).execute()
        mon_list = monitors.data or []
        full_name = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        mon_text  = "\n".join(f"• {m['symbol']} ({m['exchange']})" for m in mon_list) if mon_list else "Нет мониторингов"
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

# ── STARTUP ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())
    asyncio.create_task(price_watcher())
    asyncio.create_task(run_bot_polling())
    log.info("[STARTUP] OK")

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "Monitor Space", "version": "4.2"}

@app.post("/auth")
async def auth(request: Request):
    try:
        data   = await request.json()
        tg_id  = data.get("id")
        if not tg_id:
            return JSONResponse({"status": "error", "message": "No ID"}, status_code=400)
        if not supabase:
            return JSONResponse({"status": "error", "message": "DB not configured"}, status_code=500)
        now_iso = datetime.now(timezone.utc).isoformat()
        ip      = get_client_ip(request)
        existing = supabase.table("users").select("id,visit_count").eq("tg_id", tg_id).execute()
        if existing.data:
            cnt = existing.data[0].get("visit_count") or 0
            supabase.table("users").update({
                "last_ip":    ip,
                "last_seen":  now_iso,
                "user_agent": request.headers.get("user-agent", ""),
                "platform":   str(data.get("platform") or ""),
                "language":   str(data.get("language") or ""),
                "visit_count": cnt + 1,
            }).eq("tg_id", tg_id).execute()
            return {"status": "ok", "already_registered": True}
        supabase.table("users").insert({
            "tg_id":      tg_id,
            "username":   str(data.get("username") or ""),
            "first_name": str(data.get("first_name") or ""),
            "last_name":  str(data.get("last_name") or ""),
            "reg_ip":     ip, "last_ip": ip,
            "user_agent": request.headers.get("user-agent", ""),
            "platform":   str(data.get("platform") or ""),
            "language":   str(data.get("language") or ""),
            "last_seen":  now_iso, "created_at": now_iso,
            "visit_count": 1,
        }).execute()
        return {"status": "ok", "already_registered": False}
    except Exception as e:
        log.error("[AUTH] %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/analyze")
async def analyze_route(request: Request):
    try:
        data       = await request.json()
        symbol     = str(data.get("symbol") or "").strip().upper()
        exchange   = str(data.get("exchange") or "binance").strip().lower()
        tg_id      = data.get("id")
        alert_pct  = safe_float(data.get("alert_pct"), 5.0)
        if not symbol:
            return JSONResponse({"status": "error", "message": "Укажите символ"}, status_code=400)
        live_price = await fetch_crypto_price(symbol, exchange)
        result     = await analyze_crypto(symbol, exchange, live_price)
        final_px   = live_price or result.get("current_price_usd") or 0
        if supabase and tg_id:
            try:
                now_iso  = datetime.now(timezone.utc).isoformat()
                existing = supabase.table("crypto_monitors").select("id") \
                    .eq("tg_id", tg_id).eq("symbol", symbol).eq("exchange", exchange).execute()
                if existing.data:
                    supabase.table("crypto_monitors").update({
                        "last_price":   final_px,
                        "price_at_add": final_px,
                        "alert_pct":    alert_pct,
                        "added_at":     now_iso,
                    }).eq("id", existing.data[0]["id"]).execute()
                else:
                    supabase.table("crypto_monitors").insert({
                        "tg_id": tg_id, "symbol": symbol, "exchange": exchange,
                        "price_at_add": final_px, "last_price": final_px,
                        "alert_pct": alert_pct, "added_at": now_iso,
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
        data      = await request.json()
        base      = str(data.get("base") or "").strip().upper()
        quote     = str(data.get("quote") or "USD").strip().upper()
        tg_id     = data.get("id")
        alert_pct = safe_float(data.get("alert_pct"), 1.0)
        if not base:
            return JSONResponse({"status": "error", "message": "Укажите валюту"}, status_code=400)
        live_rate = await fetch_forex_rate(base, quote)
        result    = await analyze_forex(base, quote, live_rate)
        final_r   = live_rate or result.get("current_rate") or 0
        if supabase and tg_id:
            try:
                now_iso  = datetime.now(timezone.utc).isoformat()
                pair_sym = f"{base}/{quote}"
                existing = supabase.table("crypto_monitors").select("id") \
                    .eq("tg_id", tg_id).eq("symbol", pair_sym).execute()
                if existing.data:
                    supabase.table("crypto_monitors").update({
                        "last_price":   final_r,
                        "last_alerted": now_iso,
                        "alert_pct":    alert_pct,
                        "added_at":     now_iso,
                    }).eq("id", existing.data[0]["id"]).execute()
                else:
                    supabase.table("crypto_monitors").insert({
                        "tg_id": tg_id, "symbol": pair_sym, "exchange": "forex",
                        "price_at_add": final_r, "last_price": final_r,
                        "alert_pct": alert_pct, "added_at": now_iso,
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
    u        = res.data[0]
    monitors = supabase.table("crypto_monitors").select("*").eq("tg_id", u["tg_id"]).execute()
    return {"status": "ok", "user": u, "monitors": monitors.data or []}

@app.post("/activate-monitor")
async def activate_monitor(request: Request):
    """
    Глобальный лимит: 3 уникальных мониторинга за 7 дней на пользователя.
    Счётчик считается по числу записей с last_alerted > (now - 7d).
    После истечения 7 дней — лимит сбрасывается автоматически.
    """
    try:
        data     = await request.json()
        tg_id    = data.get("tg_id")
        symbol   = str(data.get("symbol") or "").strip().upper()
        exchange = str(data.get("exchange") or "").strip().lower()

        if not tg_id or not symbol or not exchange:
            return JSONResponse({"status": "error", "message": "Не хватает параметров"}, status_code=400)
        if not supabase:
            return JSONResponse({"status": "error", "message": "DB не настроена"}, status_code=500)

        now        = datetime.now(timezone.utc)
        week_ago   = (now - timedelta(days=7)).isoformat()
        WEEK_LIMIT = 3

        # ── Считаем сколько уникальных мониторингов активировано за последние 7 дней ──
        all_user = supabase.table("crypto_monitors").select("id,symbol,exchange,last_alerted") \
            .eq("tg_id", tg_id).execute()
        user_rows = all_user.data or []

        # Активные за неделю — у которых last_alerted проставлено и в пределах 7 дней
        active_week = []
        for r in user_rows:
            la = r.get("last_alerted")
            if not la:
                continue
            la_str = str(la).replace("Z", "+00:00")
            try:
                la_dt = datetime.fromisoformat(la_str)
                if la_dt.tzinfo is None:
                    la_dt = la_dt.replace(tzinfo=timezone.utc)
                if la_dt >= (now - timedelta(days=7)):
                    active_week.append(r)
            except ValueError:
                continue

        # ── Проверяем: уже активирован ли ЭТОТ мониторинг пользователем ──
        this_record = next(
            (r for r in user_rows if r.get("symbol") == symbol and r.get("exchange") == exchange),
            None
        )
        already_active = this_record and this_record.get("last_alerted") and any(
            r["id"] == this_record["id"] for r in active_week
        )

        # Если этот конкретный мониторинг уже активен — просто возвращаем статус
        if already_active:
            used  = len(active_week)
            remaining = max(0, WEEK_LIMIT - used)
            return {"status": "ok", "remaining": remaining, "already_active": True}

        # ── Проверяем лимит ──
        used = len(active_week)
        if used >= WEEK_LIMIT:
            # Находим ближайшую дату сброса (самый старый last_alerted + 7 дней)
            oldest = min(active_week, key=lambda r: r.get("last_alerted", ""))
            la_str = str(oldest["last_alerted"]).replace("Z", "+00:00")
            try:
                reset_dt = datetime.fromisoformat(la_str)
                if reset_dt.tzinfo is None:
                    reset_dt = reset_dt.replace(tzinfo=timezone.utc)
                reset_dt = reset_dt + timedelta(days=7)
                delta    = reset_dt - now
                hours    = int(delta.total_seconds() // 3600)
                days_left = hours // 24
                hrs_left  = hours % 24
                if days_left > 0:
                    reset_str = f"через {days_left} д. {hrs_left} ч."
                else:
                    reset_str = f"через {hrs_left} ч."
            except Exception:
                reset_str = "через 7 дней"
            return {
                "status":    "error",
                "message":   f"Лимит исчерпан — 3 запуска в неделю",
                "reset_in":  reset_str,
            }

        # ── Активируем ──
        if this_record:
            # Запись уже есть — обновляем last_alerted
            supabase.table("crypto_monitors").update({
                "last_alerted": now.isoformat(),
                "alerts_count": (this_record.get("alerts_count") or 0) + 1,
            }).eq("id", this_record["id"]).execute()
        else:
            # Создаём новую запись
            current_price = await fetch_crypto_price(symbol, exchange)
            supabase.table("crypto_monitors").insert({
                "tg_id":        tg_id,
                "symbol":       symbol,
                "exchange":     exchange,
                "alerts_count": 1,
                "expires_at":   (now + timedelta(days=7)).isoformat(),
                "last_alerted": now.isoformat(),
                "price_at_add": current_price or 0,
                "last_price":   current_price or 0,
                "alert_pct":    5,
            }).execute()

        remaining = WEEK_LIMIT - (used + 1)
        return {"status": "ok", "remaining": remaining}

    except Exception as e:
        log.error("[ACTIVATE-MONITOR] %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/check-sub")
async def check_sub(tg_id: int):
    if not bot:
        return {"subscribed": True}
    try:
        member     = await bot.get_chat_member(chat_id="@MonitorSpace", user_id=tg_id)
        subscribed = member.status not in ("left", "kicked", "banned")
        return {"subscribed": subscribed}
    except Exception as e:
        log.warning("[CHECK-SUB] %s", e)
        return {"subscribed": True}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
