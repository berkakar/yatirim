import asyncio
import os

import redis.asyncio as redis

# Mevcut alpaca_buy_points.py / alpaca_trailing_stop.py ile ayni env var isimleri
APCA_API_KEY_ID = os.environ.get("APCA_API_KEY_ID")
APCA_API_SECRET_KEY = os.environ.get("APCA_API_SECRET_KEY")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")


async def main():
    r = redis.from_url(REDIS_URL)

    # TODO: alpaca-py StockDataStream ile gercek zamanli fiyat akisina baglan.
    # Her tick geldiginde:
    #   await r.set(f"price:{symbol}", price)
    #   await r.publish(f"price:{symbol}", price)
    while True:
        print("ingestor calisiyor, henuz Alpaca stream'e baglanmadi")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
