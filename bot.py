"""
Telegram-бот на ZenMux (OpenAI-совместимый API).

Запуск:
    pip install aiogram openai
    export TELEGRAM_TOKEN="345"      # токен от @BotFather
    export ZENMUX_API_KEY="sk"      # ключ из дашборда ZenMux
    python bot.py

ZenMux полностью совместим с протоколом OpenAI, поэтому используем
официальную библиотеку openai, просто меняем base_url.
"""

import os
import asyncio
import logging
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)

# ---------- Конфиг ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ZENMUX_API_KEY = os.environ["ZENMUX_API_KEY"]

# Точное имя модели смотри на https://zenmux.ai/models — формат provider/model.
# Примеры: "openai/gpt-4o", "anthropic/claude-sonnet-4.5", "deepseek/deepseek-chat",
#          "qwen/qwen3-max", "minimax/...". Проверь актуальный id для DeepSeek/MiniMax.
DEFAULT_MODEL = "anthropic/claude-sonnet-5"

SYSTEM_PROMPT = (
    "SYSTEM_PROMPT = "Ты  полезный ассистент. Отвечай кратко и по делу на русском.")

MAX_HISTORY = 12          # сколько последних сообщений держим в контексте (без учёта system)
REQUEST_TIMEOUT = 60      # сек

# ---------- Клиент ZenMux ----------
client = AsyncOpenAI(
    api_key=ZENMUX_API_KEY,
    base_url="https://zenmux.ai/api/v1",
    timeout=REQUEST_TIMEOUT,
)

# История на каждого пользователя: user_id -> deque[{"role", "content"}]
histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    histories.pop(message.from_user.id, None)
    await message.answer("MaxHermes на связи. Пиши вопрос — отвечу. /reset чтобы очистить диалог.")


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    histories.pop(message.from_user.id, None)
    await message.answer("Контекст очищен.")


@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    history = histories[user_id]
    history.append({"role": "user", "content": message.text})

    # Собираем запрос: system + история
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        resp = await client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=messages,
        )
        answer = resp.choices[0].message.content
    except Exception as e:
        logging.exception("ZenMux error")
        await message.answer(f"Ошибка запроса к модели: {e}")
        return

    history.append({"role": "assistant", "content": answer})

    # Telegram режет сообщения длиннее 4096 символов
    for i in range(0, len(answer), 4000):
        await message.answer(answer[i:i + 4000])


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
