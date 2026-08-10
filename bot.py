import asyncio
import logging
import sys
from datetime import datetime
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import aiohttp
import os

# === КОНФИГУРАЦИЯ ===
TELEGRAM_TOKEN = "8846910732:AAFbZdiJ6mPIVwY-W3wWzh9epOgdU7jtc0s"          # Замените!
CHANNEL_ID = "@chetoUtyaAlert"                 # Замените!
TONCENTER_API_KEY = "4b0245d06e6dd23f5422c22f1c7fa6ba0cf4d0ec9f0077c87e1e51938ce23dc8"         # Замените!

POOL_ADDRESS = "EQCO9NDT4Il25_4ZpHIOgMAUbRJvpsI9pLzqhD8X7eTVB7X_"
THRESHOLD_TON = 10000.0
POLL_INTERVAL = 10
# =========================

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))

class TransactionCache:
    def __init__(self, max_size=200):
        self.cache = set()
        self.max_size = max_size

    def is_new(self, tx_hash: str) -> bool:
        if tx_hash in self.cache:
            return False
        self.cache.add(tx_hash)
        if len(self.cache) > self.max_size:
            old_items = list(self.cache)[:self.max_size//2]
            for item in old_items:
                self.cache.discard(item)
        return True

tx_cache = TransactionCache()

async def fetch_pool_transactions(session: aiohttp.ClientSession):
    url = "https://toncenter.com/api/v2/getTransactions"
    params = {
        "address": POOL_ADDRESS,
        "limit": 20,
        "archival": "false"
    }
    headers = {"X-API-Key": TONCENTER_API_KEY}
    
    try:
        async with session.get(url, params=params, headers=headers, timeout=15) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("ok"):
                    return data.get("result", [])
                else:
                    logging.error(f"Ошибка API: {data.get('error', 'Unknown error')}")
                    return []
            else:
                logging.error(f"Ошибка API Toncenter: Статус {response.status}")
                return []
    except Exception as e:
        logging.error(f"Ошибка при запросе к API: {e}")
        return []

def parse_stonfi_swap(tx: dict) -> dict | None:
    tx_hash = tx.get("transaction_id", {}).get("hash")
    if not tx_hash:
        return None

    in_msg = tx.get("in_msg", {})
    out_msgs = tx.get("out_msgs", [])
    
    pool_involved = False
    if in_msg.get("source") == POOL_ADDRESS or in_msg.get("destination") == POOL_ADDRESS:
        pool_involved = True
    if not pool_involved:
        for msg in out_msgs:
            if msg.get("source") == POOL_ADDRESS or msg.get("destination") == POOL_ADDRESS:
                pool_involved = True
                break
    if not pool_involved:
        return None
    
    total_ton_volume = 0.0
    if in_msg:
        value_nano = int(in_msg.get("value", 0))
        if value_nano > 0:
            total_ton_volume = value_nano / 1_000_000_000
    
    if total_ton_volume == 0:
        for out_msg in out_msgs:
            value_nano = int(out_msg.get("value", 0))
            if value_nano > 1_000_000_000:
                total_ton_volume = value_nano / 1_000_000_000
                break
    
    if total_ton_volume < THRESHOLD_TON:
        return None
    
    is_buy = False
    if in_msg and in_msg.get("destination") == POOL_ADDRESS:
        value_nano = int(in_msg.get("value", 0))
        if value_nano > 1_000_000_000:
            is_buy = True
    
    for out_msg in out_msgs:
        if out_msg.get("source") == POOL_ADDRESS:
            value_nano = int(out_msg.get("value", 0))
            if value_nano > 1_000_000_000:
                is_buy = False
                break
    
    trader_address = "Неизвестно"
    if in_msg:
        if is_buy and in_msg.get("source"):
            trader_address = in_msg.get("source")
        elif not is_buy and in_msg.get("source"):
            trader_address = in_msg.get("source")
    
    if trader_address == "Неизвестно":
        for out_msg in out_msgs:
            if out_msg.get("source") == POOL_ADDRESS and out_msg.get("destination"):
                trader_address = out_msg.get("destination")
                break
    
    return {
        "hash": tx_hash,
        "type": "🟢 ПОКУПКА" if is_buy else "🔴 ПРОДАЖА",
        "amount": total_ton_volume,
        "trader": trader_address,
        "timestamp": datetime.fromtimestamp(int(tx.get("utime", 0)))
    }

async def monitor_pool():
    logging.info("🚀 Запуск мониторинга крупных транзакций UTYA...")
    logging.info(f"📊 Пул: {POOL_ADDRESS} | Порог: {THRESHOLD_TON} TON")
    
    async with aiohttp.ClientSession() as session:
        initial_txs = await fetch_pool_transactions(session)
        for tx in initial_txs:
            tx_hash = tx.get("transaction_id", {}).get("hash")
            if tx_hash:
                tx_cache.is_new(tx_hash)
        
        logging.info("✅ Первичный кэш заполнен. Начинаю отслеживание...")

        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                transactions = await fetch_pool_transactions(session)
                
                if not transactions:
                    continue
                
                for tx in transactions:
                    tx_hash = tx.get("transaction_id", {}).get("hash")
                    
                    if not tx_hash or not tx_cache.is_new(tx_hash):
                        continue
                    
                    result = parse_stonfi_swap(tx)
                    if result:
                        message = (
                            f"{result['type']} UTYA!\n\n"
                            f"💰 **Сумма:** `{result['amount']:,.2f}` TON\n"
                            f"👤 **Трейдер:** `{result['trader'][:10]}...{result['trader'][-10:]}`\n"
                            f"🕒 **Время:** {result['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                            f"🔗 [Смотреть транзакцию](https://tonscan.org/tx/{result['hash']})"
                        )
                        
                        try:
                            await bot.send_message(
                                chat_id=CHANNEL_ID, 
                                text=message, 
                                disable_web_page_preview=True
                            )
                            logging.info(f"✅ Алерт отправлен: {result['type']} {result['amount']:.2f} TON")
                        except Exception as e:
                            logging.error(f"❌ Ошибка отправки в Telegram: {e}")
                            
            except Exception as e:
                logging.error(f"❌ Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(5)

async def main():
    try:
        await monitor_pool()
    except KeyboardInterrupt:
        logging.info("⏹️ Бот остановлен пользователем")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⏹️ Программа завершена")
