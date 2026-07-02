"""
Telegram-бот на ZenMux — память, скиллы под продажи, зрение и генерация картинок.

Возможности:
  - Постоянная память диалога (SQLite, переживает перезапуски бота)
  - Скиллы-режимы под B2B-продажи (см. команды)
  - Понимание картинок: пришли скриншот с подписью — бот прочитает
  - Генерация картинок: /image описание  (модель Nano Banana 2 Lite)
  - Смена текстовой модели на лету: /model

Токены — из переменных окружения (в панели Bothost):
    TELEGRAM_TOKEN, ZENMUX_API_KEY

Библиотеки: aiogram, openai, google-genai (см. requirements.txt).
"""

import os
import time
import base64
import asyncio
import logging
import sqlite3
import threading

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, BotCommand, BufferedInputFile
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)

# ======================= КОНФИГ =======================
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ZENMUX_API_KEY = os.environ["ZENMUX_API_KEY"]

# Модель по умолчанию для текста. Список id — на https://zenmux.ai/models
DEFAULT_MODEL = "deepseek/deepseek-chat"
# Модель для понимания картинок (зрение). Бюджетная, хороша для скриншотов/OCR.
VISION_MODEL = "google/gemini-3-flash-preview"
# Модель для ГЕНЕРАЦИИ картинок (Nano Banana 2 Lite).
IMAGE_MODEL = "google/gemini-3.1-flash-lite-image"

DB_PATH = "bot_memory.db"
MAX_TURNS = 14
REQUEST_TIMEOUT = 90

# ======================= СКИЛЛЫ =======================
PROMPTS = {
    "assistant": (
        "Ты — полезный ассистент менеджера по продажам. "
        "Отвечай кратко, по делу, на русском. Без воды и лести."
    ),
    "kyc": (
        "Ты — аналитик по B2B-продажам. Пользователь даёт название компании или "
        "скриншот с данными. Дай краткий разбор: чем занимается, размер и выручка, "
        "финансовое состояние, вероятные потребности в IT-инфраструктуре, "
        "точки входа и кто может быть ЛПР. Тезисно, на русском."
    ),
    "email": (
        "Ты — эксперт по холодным письмам в enterprise B2B. Пиши короткие письма "
        "без канцелярита: цепляющая первая строка про клиента (а не про нас), "
        "одна конкретная ценность, мягкий призыв к короткому созвону. "
        "Предложи 2 варианта темы письма. На русском."
    ),
    "followup": (
        "Ты — помощник по follow-up. Составь вежливое, но не навязчивое сообщение-"
        "напоминание, которое двигает сделку вперёд и даёт человеку лёгкий повод "
        "ответить. Коротко, на русском. Предложи 2 тона: мягкий и более настойчивый."
    ),
    "discovery": (
        "Ты — помощник по подготовке к discovery-звонку. По вводным о клиенте выдай: "
        "гипотезы о болях, список открытых вопросов по SPIN, что важно услышать, "
        "и красные флаги. Тезисно, на русском."
    ),
    "meddpicc": (
        "Ты — коуч по квалификации сделок по MEDDPICC. Разбери сделку по буквам "
        "(Metrics, Economic buyer, Decision criteria, Decision process, Paper process, "
        "Identify pain, Champion, Competition). Отметь, где пусто, оцени здоровье сделки "
        "и предложи 3 следующих шага. На русском."
    ),
    "battlecard": (
        "Ты — специалист по конкурентному позиционированию. По названному конкуренту "
        "собери батлкарту: их сильные и слабые стороны, где мы выигрываем, "
        "как отвечать на типичные аргументы в их пользу, ловушки. Тезисно, на русском. "
        "Не выдумывай фактов — если данных мало, так и скажи и предложи, что уточнить."
    ),
    "objection": (
        "Ты — тренер по работе с возражениями. Пользователь присылает возражение клиента. "
        "Дай 2-3 варианта ответа разной тональности, объясни логику каждого и предложи "
        "уточняющий вопрос, чтобы понять настоящую причину. На русском."
    ),
    "proposal": (
        "Ты — помощник по коммерческим предложениям. По вводным составь структуру КП: "
        "проблема клиента, решение, ценность в деньгах/времени, ключевые условия, "
        "следующий шаг. Пиши языком выгод клиента, а не характеристик. На русском."
    ),
    "summary": (
        "Ты — помощник по итогам встреч. Пользователь присылает заметки или расшифровку. "
        "Сделай: краткое резюме, договорённости, действия с ответственными, "
        "открытые вопросы и предложи текст follow-up письма. На русском."
    ),
    "linkedin": (
        "Ты — эксперт по первому касанию в мессенджерах и соцсетях. Напиши короткое "
        "персональное сообщение (2-4 предложения) без продающего давления, "
        "с понятным поводом для ответа. Предложи 2 варианта. На русском."
    ),
    "digest": (
        "Ты — помощник по дайджестам. Пользователь присылает текст/новости. "
        "Сожми до сути: главные тезисы списком, что это значит для продаж "
        "и на что обратить внимание. Кратко, на русском."
    ),
}

SKILL_TITLES = {
    "assistant": "обычный чат",
    "kyc": "разбор компании",
    "email": "холодное письмо",
    "followup": "follow-up",
    "discovery": "подготовка к звонку",
    "meddpicc": "квалификация сделки (MEDDPICC)",
    "battlecard": "батлкарта по конкуренту",
    "objection": "работа с возражением",
    "proposal": "структура КП",
    "summary": "итоги встречи",
    "linkedin": "первое касание в мессенджере",
    "digest": "дайджест текста",
}

# ======================= ПАМЯТЬ (SQLite) =======================
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_db_lock = threading.Lock()

with _db_lock:
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        " user_id INTEGER, role TEXT, content TEXT, ts REAL)"
    )
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS settings ("
        " user_id INTEGER PRIMARY KEY, mode TEXT, model TEXT)"
    )
    _conn.commit()


def get_settings(user_id: int):
    with _db_lock:
        row = _conn.execute(
            "SELECT mode, model FROM settings WHERE user_id=?", (user_id,)
        ).fetchone()
    if row:
        return row[0], row[1]
    return "assistant", DEFAULT_MODEL


def save_settings(user_id: int, mode: str, model: str):
    with _db_lock:
        _conn.execute(
            "INSERT INTO settings(user_id, mode, model) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, model=excluded.model",
            (user_id, mode, model),
        )
        _conn.commit()


def add_message(user_id: int, role: str, content: str):
    with _db_lock:
        _conn.execute(
            "INSERT INTO messages(user_id, role, content, ts) VALUES(?,?,?,?)",
            (user_id, role, content, time.time()),
        )
        _conn.commit()


def get_history(user_id: int):
    limit = MAX_TURNS * 2
    with _db_lock:
        rows = _conn.execute(
            "SELECT role, content FROM messages WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    rows.reverse()
    return [{"role": r, "content": c} for r, c in rows]


def clear_history(user_id: int):
    with _db_lock:
        _conn.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
        _conn.commit()


# ======================= КЛИЕНТЫ =======================
# Текст и зрение — через OpenAI-совместимый вход ZenMux.
client = AsyncOpenAI(
    api_key=ZENMUX_API_KEY,
    base_url="https://zenmux.ai/api/v1",
    timeout=REQUEST_TIMEOUT,
)

# Генерация картинок — через протокол Google (Vertex AI) на ZenMux.
# Оборачиваем в try, чтобы бот жил, даже если генерация недоступна.
try:
    from google import genai
    from google.genai import types as genai_types

    image_client = genai.Client(
        api_key=ZENMUX_API_KEY,
        vertexai=True,
        http_options=genai_types.HttpOptions(
            api_version="v1", base_url="https://zenmux.ai/api/vertex-ai"
        ),
    )
    IMAGE_ENABLED = True
except Exception as e:  # noqa
    logging.warning("Генерация картинок отключена: %s", e)
    IMAGE_ENABLED = False


def generate_image(prompt: str):
    """Синхронная генерация. Возвращает bytes картинки или None."""
    resp = image_client.models.generate_content(
        model=IMAGE_MODEL,
        contents=[prompt],
        config=genai_types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"]
        ),
    )
    for part in resp.candidates[0].content.parts:
        inline = getattr(part, "inline_data", None)
        if inline is not None and inline.data:
            data = inline.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            return data
    return None


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


def help_text() -> str:
    lines = ["Я ассистент на ZenMux с памятью. Скиллы:\n"]
    for cmd, title in SKILL_TITLES.items():
        lines.append(f"/{cmd} — {title}")
    lines.append("\n/image <описание> — сгенерировать картинку")
    lines.append("/model — показать/сменить текстовую модель")
    lines.append("/mode — текущий режим")
    lines.append("/reset — очистить память диалога")
    lines.append("\nМожно прислать скриншот с подписью — прочитаю картинку.")
    return "\n".join(lines)


# ======================= КОМАНДЫ =======================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("На связи, память включена.\n\n" + help_text())


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(help_text())


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    clear_history(message.from_user.id)
    await message.answer("Память диалога очищена.")


@dp.message(Command("mode"))
async def cmd_mode(message: Message):
    mode, _ = get_settings(message.from_user.id)
    await message.answer(f"Текущий режим: /{mode} — {SKILL_TITLES.get(mode, mode)}")


@dp.message(Command("model"))
async def cmd_model(message: Message):
    user_id = message.from_user.id
    mode, model = get_settings(user_id)
    parts = message.text.split(maxsplit=1)
    if len(parts) == 1:
        await message.answer(
            f"Текущая модель: {model}\n\n"
            "Сменить: /model provider/name\n"
            "Список моделей: https://zenmux.ai/models"
        )
    else:
        new_model = parts[1].strip()
        save_settings(user_id, mode, new_model)
        await message.answer(f"Модель переключена на: {new_model}")


@dp.message(Command(commands=["image", "img"]))
async def cmd_image(message: Message):
    if not IMAGE_ENABLED:
        await message.answer("Генерация картинок сейчас недоступна.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Напиши, что нарисовать. Пример:\n/image рыжий кот в деловом костюме")
        return
    prompt = parts[1]
    await bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        data = await asyncio.to_thread(generate_image, prompt)
    except Exception as e:
        logging.exception("Image generation error")
        await message.answer(f"Не смог сгенерировать: {e}")
        return
    if data:
        await message.answer_photo(
            BufferedInputFile(data, filename="image.png"), caption=prompt[:1000]
        )
    else:
        await message.answer("Модель не вернула картинку, попробуй переформулировать запрос.")


@dp.message(Command(commands=list(PROMPTS.keys())))
async def set_mode(message: Message):
    user_id = message.from_user.id
    cmd = message.text.lstrip("/").split()[0].split("@")[0].lower()
    _, model = get_settings(user_id)
    save_settings(user_id, cmd, model)
    clear_history(user_id)
    await message.answer(
        f"Режим: /{cmd} — {SKILL_TITLES.get(cmd, cmd)}. Память очищена, пиши запрос."
    )


# ======================= ПОНИМАНИЕ КАРТИНОК =======================
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    mode, _ = get_settings(user_id)
    question = message.caption or "Что на изображении? Разбери по сути."

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buf = await bot.download_file(file.file_path)
    b64 = base64.b64encode(buf.read()).decode()

    messages = [
        {"role": "system", "content": PROMPTS[mode]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        },
    ]

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        resp = await client.chat.completions.create(
            model=VISION_MODEL, messages=messages
        )
        answer = resp.choices[0].message.content
    except Exception as e:
        logging.exception("Vision error")
        await message.answer(f"Не смог обработать картинку: {e}")
        return

    add_message(user_id, "user", f"[картинка] {question}")
    add_message(user_id, "assistant", answer)
    await send_long(message, answer)


# ======================= ТЕКСТ =======================
@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    mode, model = get_settings(user_id)

    add_message(user_id, "user", message.text)
    history = get_history(user_id)
    messages = [{"role": "system", "content": PROMPTS[mode]}, *history]

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        resp = await client.chat.completions.create(model=model, messages=messages)
        answer = resp.choices[0].message.content
    except Exception as e:
        logging.exception("ZenMux error")
        await message.answer(f"Ошибка запроса к модели: {e}")
        return

    add_message(user_id, "assistant", answer)
    await send_long(message, answer)


async def send_long(message: Message, text: str):
    """Telegram режет сообщения длиннее 4096 символов."""
    for i in range(0, len(text), 4000):
        await message.answer(text[i:i + 4000])


async def set_menu():
    commands = [BotCommand(command=c, description=t) for c, t in SKILL_TITLES.items()]
    commands += [
        BotCommand(command="image", description="сгенерировать картинку"),
        BotCommand(command="model", description="сменить модель"),
        BotCommand(command="reset", description="очистить память"),
        BotCommand(command="help", description="справка"),
    ]
    await bot.set_my_commands(commands)


async def main():
    await set_menu()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
