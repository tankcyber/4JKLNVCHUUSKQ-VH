import asyncio
import base64
import io
import logging
import sqlite3
import json
import re
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "8821975305:AAGyZwlu_5l2f2cD2iizZlQPVzcCXVxWtzY"  # Получи у @BotFather
ADMIN_IDS = [5439940299]  # Твой Telegram ID

# ================= НАСТРОЙКИ CODEX.SALE API =================
API_KEY = "sk-clb-cOqQjd24MYStR5-wn6EW5FEJDtlwxeiGNbztbuZi4jY"  # Твой API ключ
BASE_URL = "https://codex.sale"

# Эндпоинты
MODELS_ENDPOINT = f"{BASE_URL}/v1/models"
CHAT_ENDPOINT = f"{BASE_URL}/v1/chat/completions"
RESPONSES_ENDPOINT = f"{BASE_URL}/v1/responses"
CODEX_ENDPOINT = f"{BASE_URL}/backend-api/codex"
IMAGES_GENERATIONS_ENDPOINT = f"{BASE_URL}/v1/images/generations"
IMAGES_EDITS_ENDPOINT = f"{BASE_URL}/v1/images/edits"
IMAGE_SIZE = "1024x1024"
IMAGE_MODEL = "gpt-image-2"

# Используем Chat Endpoint (основной)
API_URL = CHAT_ENDPOINT

# Заголовки для запросов
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

AUTH_HEADERS = {
    "Authorization": f"Bearer {API_KEY}"
}

# Модели с правильными Model ID из документации
MODELS = {
    "gpt-5.4": {
        "name": "GPT-5.4", 
        "multiplier": 1.0, 
        "api_name": "gpt-5.4",
        "description": "Базовый тариф"
    },
    "gpt-5.4-mini": {
        "name": "GPT-5.4 Mini", 
        "multiplier": 0.9, 
        "api_name": "gpt-5.4-mini",
        "description": "Экономичная модель"
    },
    "gpt-5.5": {
        "name": "GPT-5.5", 
        "multiplier": 4.5, 
        "api_name": "gpt-5.5",
        "description": "Мощная модель"
    }
}

# Приоритетность запросов
PRIORITY_MODES = {
    "normal": {"name": "Обычный", "multiplier": 1.0, "emoji": "🐢"},
    "fast": {"name": "Fast Speed", "multiplier": 2.0, "emoji": "⚡"}
}

# Базовая стоимость 1K токенов (в токенах пользователя)
BASE_PRICE_PER_1K = 100

# База данных
DB_NAME = 'users.db'

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= СОСТОЯНИЯ ДЛЯ АДМИНА =================
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_token_amount = State()

# ================= ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ЭКРАНИРОВАНИЯ MARKDOWN =================
def escape_markdown(text: str) -> str:
    """Экранирует специальные символы Markdown"""
    special_chars = r'_*[]()~`>#+-=|{}.!'
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

# ================= ТОКЕНИЗАТОР =================
_TOKENIZER_CACHE = {}

def get_tokenizer(model_key: str):
    """Получить токенизатор для модели"""
    encoding_name = "cl100k_base"
    
    if encoding_name not in _TOKENIZER_CACHE:
        _TOKENIZER_CACHE[encoding_name] = tiktoken.get_encoding(encoding_name)
    
    return _TOKENIZER_CACHE[encoding_name]

async def count_tokens(text: str, model_key: str = "gpt-5.4") -> int:
    """Точный подсчёт токенов (как в API)"""
    if not text:
        return 0
    
    tokenizer = get_tokenizer(model_key)
    tokens = tokenizer.encode(text)
    return len(tokens)

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            tokens_balance INTEGER DEFAULT 100000,
            is_banned INTEGER DEFAULT 0,
            current_model TEXT DEFAULT 'gpt-5.4',
            priority_mode TEXT DEFAULT 'normal',
            registered_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    res = cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    return res

def add_user(user_id, username):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    try:
        cur.execute('INSERT INTO users (user_id, username, registered_at) VALUES (?, ?, ?)', 
                   (user_id, username, now))
        conn.commit()
    except:
        pass
    conn.close()

def update_tokens(user_id, new_balance):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('UPDATE users SET tokens_balance = ? WHERE user_id = ?', (new_balance, user_id))
    conn.commit()
    conn.close()

def set_model(user_id, model):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('UPDATE users SET current_model = ? WHERE user_id = ?', (model, user_id))
    conn.commit()
    conn.close()

def set_priority_mode(user_id, mode):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('UPDATE users SET priority_mode = ? WHERE user_id = ?', (mode, user_id))
    conn.commit()
    conn.close()

def set_ban_status(user_id, status):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_banned = ? WHERE user_id = ?', (status, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    res = cur.execute('SELECT user_id, username, tokens_balance, is_banned FROM users').fetchall()
    conn.close()
    return res

def find_user_by_username(username):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    clean_username = username.replace('@', '').lower()
    res = cur.execute(
        'SELECT user_id FROM users WHERE LOWER(username) = ?',
        (clean_username,)
    ).fetchone()
    conn.close()
    return res

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_main_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="🤖 Выбрать модель", callback_data="menu_models")
    builder.button(text="⚡ Выбрать скорость", callback_data="menu_priority")
    builder.button(text="📊 Мой профиль", callback_data="menu_profile")
    builder.button(text="💰 Купить токены", callback_data="menu_buy")
    if user_id in ADMIN_IDS:
        builder.button(text="🛠 Админ панель", callback_data="admin_panel")
    builder.adjust(1)
    return builder.as_markup()

def get_models_keyboard():
    builder = InlineKeyboardBuilder()
    for key, val in MODELS.items():
        price = int(BASE_PRICE_PER_1K * val['multiplier'])
        builder.button(text=f"{val['name']} — {price} ток/1K ({val['multiplier']}x)", callback_data=f"model_{key}")
    builder.button(text="🔙 Назад", callback_data="back_home")
    builder.adjust(1)
    return builder.as_markup()

def get_priority_keyboard(current_mode):
    builder = InlineKeyboardBuilder()
    for key, val in PRIORITY_MODES.items():
        marker = "✅ " if current_mode == key else ""
        builder.button(text=f"{marker}{val['emoji']} {val['name']} (x{val['multiplier']})", callback_data=f"priority_{key}")
    builder.button(text="🔙 Назад", callback_data="back_home")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Список пользователей", callback_data="admin_list")
    builder.button(text="🚫 Забанить/Разбанить", callback_data="admin_ban")
    builder.button(text="➕ Выдать токены", callback_data="admin_give")
    builder.button(text="➖ Забрать токены", callback_data="admin_take")
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🔙 Назад", callback_data="back_home")
    builder.adjust(1)
    return builder.as_markup()

async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup=None, parse_mode=None):
    """Безопасное редактирование сообщения без parse_mode по умолчанию"""
    try:
        if parse_mode:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await callback.message.edit_text(text, reply_markup=reply_markup)
        return True
    except Exception as e:
        if "message is not modified" in str(e):
            await callback.answer("⏳ Уже здесь")
            return False
        else:
            if parse_mode:
                await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            else:
                await callback.message.answer(text, reply_markup=reply_markup)
            return True

async def calculate_image_cost(user_id: int, is_edit: bool = False) -> float:
    """Стоимость генерации/редактирования изображения"""
    user = get_user(user_id)
    if not user:
        return 0

    model_mult = MODELS["gpt-image-2"]["multiplier"]
    priority_mult = PRIORITY_MODES.get(user[5], {"multiplier": 1.0})["multiplier"]
    base_tokens = 5000 if is_edit else 3000
    return (base_tokens / 1000) * BASE_PRICE_PER_1K * model_mult * priority_mult


async def _delete_thinking_message(thinking_message: Message | None):
    if thinking_message:
        try:
            await thinking_message.delete()
        except Exception:
            pass


def _parse_image_api_response(data: dict) -> bytes | None:
    items = data.get("data") or []
    if not items:
        return None

    item = items[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    return None


async def _download_image_from_url(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url, timeout=60) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception as e:
        logging.error(f"Image download error: {e}")
    return None


async def _charge_user_for_image(user_id: int, cost: float) -> tuple[bool, str | None, int | None]:
    user = get_user(user_id)
    if not user:
        return False, "Пользователь не найден", None

    if user[2] < cost:
        return False, f"❌ Недостаточно токенов! Нужно: {cost:.0f}, есть: {user[2]}", None

    update_tokens(user_id, user[2] - cost)
    new_balance = get_user(user_id)[2]
    return True, None, new_balance


async def generate_image(user_id: int, prompt: str, thinking_message: Message = None):
    """Генерация изображения через /v1/images/generations"""
    cost = await calculate_image_cost(user_id, is_edit=False)
    ok, error, balance = await _charge_user_for_image(user_id, cost)
    if not ok:
        await _delete_thinking_message(thinking_message)
        return None, None, error

    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "size": IMAGE_SIZE,
        "response_format": "b64_json",
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                IMAGES_GENERATIONS_ENDPOINT,
                headers=HEADERS,
                json=payload,
                timeout=120,
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    user = get_user(user_id)
                    update_tokens(user_id, user[2] + cost)
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Ошибка генерации ({resp.status}):\n{error_text[:300]}"

                data = await resp.json()
                image_bytes = _parse_image_api_response(data)

                if not image_bytes and data.get("data"):
                    url = data["data"][0].get("url")
                    if url:
                        image_bytes = await _download_image_from_url(session, url)

                if not image_bytes:
                    user = get_user(user_id)
                    update_tokens(user_id, user[2] + cost)
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Не удалось получить изображение:\n{str(data)[:200]}"

                await _delete_thinking_message(thinking_message)
                caption = (
                    f"🎨 Сгенерировано по запросу\n"
                    f"💎 Остаток: {balance:.0f} токенов | 🖼 {MODELS['gpt-image-2']['name']}"
                )
                return image_bytes, caption, None

        except asyncio.TimeoutError:
            user = get_user(user_id)
            update_tokens(user_id, user[2] + cost)
            await _delete_thinking_message(thinking_message)
            return None, None, "⏰ Таймаут при генерации изображения. Попробуйте позже."
        except Exception as e:
            user = get_user(user_id)
            update_tokens(user_id, user[2] + cost)
            await _delete_thinking_message(thinking_message)
            logging.error(f"Image generation error: {e}", exc_info=True)
            return None, None, f"❌ Ошибка: {str(e)}"


async def edit_image(user_id: int, prompt: str, image_bytes: bytes, thinking_message: Message = None):
    """Редактирование изображения через /v1/images/edits"""
    cost = await calculate_image_cost(user_id, is_edit=True)
    ok, error, balance = await _charge_user_for_image(user_id, cost)
    if not ok:
        await _delete_thinking_message(thinking_message)
        return None, None, error

    form = aiohttp.FormData()
    form.add_field("model", IMAGE_MODEL)
    form.add_field("prompt", prompt)
    form.add_field("size", IMAGE_SIZE)
    form.add_field(
        "image",
        image_bytes,
        filename="input.png",
        content_type="image/png",
    )

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                IMAGES_EDITS_ENDPOINT,
                headers=AUTH_HEADERS,
                data=form,
                timeout=120,
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    user = get_user(user_id)
                    update_tokens(user_id, user[2] + cost)
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Ошибка редактирования ({resp.status}):\n{error_text[:300]}"

                data = await resp.json()
                result_bytes = _parse_image_api_response(data)

                if not result_bytes and data.get("data"):
                    url = data["data"][0].get("url")
                    if url:
                        result_bytes = await _download_image_from_url(session, url)

                if not result_bytes:
                    user = get_user(user_id)
                    update_tokens(user_id, user[2] + cost)
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Не удалось получить изображение:\n{str(data)[:200]}"

                await _delete_thinking_message(thinking_message)
                caption = (
                    f"✏️ Отредактировано по запросу\n"
                    f"💎 Остаток: {balance:.0f} токенов | 🖼 {MODELS['gpt-image-2']['name']}"
                )
                return result_bytes, caption, None

        except asyncio.TimeoutError:
            user = get_user(user_id)
            update_tokens(user_id, user[2] + cost)
            await _delete_thinking_message(thinking_message)
            return None, None, "⏰ Таймаут при редактировании изображения. Попробуйте позже."
        except Exception as e:
            user = get_user(user_id)
            update_tokens(user_id, user[2] + cost)
            await _delete_thinking_message(thinking_message)
            logging.error(f"Image edit error: {e}", exc_info=True)
            return None, None, f"❌ Ошибка: {str(e)}"


async def calculate_cost(user_id: int, prompt_tokens: int, completion_tokens: int = 0):
    """Рассчитать стоимость запроса на основе точных токенов"""
    user = get_user(user_id)
    if not user:
        return 0

    model_key = user[4]
    model_mult = MODELS.get(model_key, {"multiplier": 1.0})["multiplier"]
    priority_key = user[5]
    priority_mult = PRIORITY_MODES.get(priority_key, {"multiplier": 1.0})["multiplier"]

    prompt_cost = (prompt_tokens / 1000) * BASE_PRICE_PER_1K * model_mult * priority_mult
    completion_cost = (completion_tokens / 1000) * BASE_PRICE_PER_1K * model_mult * priority_mult

    return prompt_cost + completion_cost

async def get_available_models():
    """Получить список доступных моделей от API"""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(MODELS_ENDPOINT, headers=HEADERS, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return True, data
                else:
                    error = await resp.text()
                    return False, error
        except Exception as e:
            return False, str(e)

async def call_codex_api(user_id, prompt, thinking_message: Message = None, image_url=None):
    """Запрос к API codex.sale с реальным подсчётом токенов из ответа API"""
    user = get_user(user_id)
    if not user:
        return None, "Пользователь не найден"
    
    model_key = user[4]
    model_info = MODELS.get(model_key, MODELS["gpt-5.4"])
    priority_mode = user[5]
    
    # Формируем сообщения
    messages = [{"role": "user", "content": prompt}]
    
    # Если есть картинка (vision для GPT-5.5)
    if image_url and model_key == "gpt-5.5":
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}}
        ]}]
    
    # Используем правильное название модели из документации
    api_model_name = model_info["api_name"]
    
    payload = {
        "model": api_model_name,
        "messages": messages,
        "max_tokens": 2000,
        "temperature": 0.7
    }
    
    # Добавляем приоритет, если Fast Speed
    if priority_mode == "fast":
        payload["priority"] = "high"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(API_URL, headers=HEADERS, json=payload, timeout=60) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    error_msg = f"❌ Ошибка API ({resp.status}):\n"
                    if resp.status == 401:
                        error_msg += "🔑 Неверный API ключ. Проверьте API_KEY в коде.\n"
                    elif resp.status == 404:
                        error_msg += "🔍 Модель не найдена. Проверьте название модели.\n"
                    error_msg += error_text[:300]
                    return None, error_msg
                
                data = await resp.json()
                
                logging.info(f"FULL API RESPONSE: {json.dumps(data, indent=2)[:1000]}")
                
                # Извлекаем ответ
                answer = None
                if 'choices' in data and len(data['choices']) > 0:
                    if 'message' in data['choices'][0]:
                        answer = data['choices'][0]['message']['content']
                    elif 'text' in data['choices'][0]:
                        answer = data['choices'][0]['text']
                
                if not answer:
                    return None, f"❌ Неожиданный формат ответа:\n{str(data)[:200]}"
                
                # ===== БЕРЁМ ТОКЕНЫ ИЗ ОТВЕТА API =====
                prompt_tokens = 0
                completion_tokens = 0
                total_tokens = 0
                
                if 'usage' in data:
                    prompt_tokens = data['usage'].get('prompt_tokens', 0)
                    completion_tokens = data['usage'].get('completion_tokens', 0)
                    total_tokens = data['usage'].get('total_tokens', 0)
                    
                    if total_tokens == 0 and prompt_tokens > 0:
                        total_tokens = prompt_tokens + completion_tokens
                    
                    logging.info(f"✅ API usage: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")
                else:
                    logging.warning(f"⚠️ API НЕ вернул поле 'usage'! Ответ: {data}")
                    prompt_tokens = len(prompt) // 2 + 50
                    completion_tokens = len(answer) // 2 + 50
                    total_tokens = prompt_tokens + completion_tokens
                
                # Расчёт стоимости на основе токенов от API
                total_cost = await calculate_cost(user_id, prompt_tokens, completion_tokens)
                
                # Проверяем баланс
                user_current = get_user(user_id)
                if user_current[2] < total_cost:
                    return None, f"❌ Недостаточно токенов! Нужно: {total_cost:.0f}, есть: {user_current[2]}"
                
                # Списываем реальную сумму
                update_tokens(user_id, user_current[2] - total_cost)
                
                # Получаем новый баланс
                user_final = get_user(user_id)
                
                # Удаляем сообщение "ДУМАЮ..."
                if thinking_message:
                    try:
                        await thinking_message.delete()
                    except:
                        pass
                
                # Формируем компактный футер
                footer = f"\n\n---\n💎 Остаток: {user_final[2]:.0f} токенов | 🧠 {MODELS[model_key]['name']}"
                
                # Добавляем ответ ИИ с футером
                final_answer = answer + footer
                
                return final_answer, None
                
        except asyncio.TimeoutError:
            if thinking_message:
                try:
                    await thinking_message.delete()
                except:
                    pass
            return None, "⏰ Таймаут. Сервер не отвечает, попробуйте позже."
        except aiohttp.ClientConnectorError as e:
            if thinking_message:
                try:
                    await thinking_message.delete()
                except:
                    pass
            return None, f"❌ Не удалось подключиться к серверу.\nОшибка: {str(e)}"
        except Exception as e:
            if thinking_message:
                try:
                    await thinking_message.delete()
                except:
                    pass
            logging.error(f"Unexpected error: {e}", exc_info=True)
            return None, f"❌ Ошибка: {str(e)}"

# ================= ОБРАБОТЧИКИ КОМАНД =================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "no_username"
    
    user = get_user(user_id)
    if not user:
        add_user(user_id, username)
        user = get_user(user_id)
    
    if user[3] == 1:
        await message.answer("❌ Вы забанены. Обратитесь к @SedoyDiada")
        return
    
    model_info = MODELS[user[4]]
    priority_info = PRIORITY_MODES[user[5]]
    model_price = int(BASE_PRICE_PER_1K * model_info['multiplier'])
    
    await message.answer(
        f"✨ Привет, {message.from_user.first_name}!\n\n"
        f"🤖 Я работаю через API codex.sale\n\n"
        f"💰 Баланс: {user[2]} токенов\n"
        f"🧠 Модель: {model_info['name']} ({model_info['multiplier']}x) — {model_price} ток/1K\n"
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']} ({priority_info['multiplier']}x)\n\n"
        f"📝 Просто напиши сообщение или отправь фото!\n\n"
        f"🖼 GPT-IMAGE 2: текст → генерация, фото + подпись → редактирование\n\n"
        f"💡 Доступные модели: GPT-5.4, GPT-5.4 Mini, GPT-5.5, GPT-IMAGE 2",
        reply_markup=get_main_keyboard(user_id)
    )

@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 Ваш Telegram ID: {message.from_user.id}")

@dp.message(Command("models"))
async def cmd_models(message: Message):
    """Показать доступные модели от API"""
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("⛔ Только для админов")
        return
    
    status, result = await get_available_models()
    if status:
        await message.answer(f"📡 Доступные модели от API:\n{json.dumps(result, indent=2)[:500]}")
    else:
        await message.answer(f"❌ Ошибка получения моделей: {result}")

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отменить текущее действие админа"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("✅ Действие отменено.")
        if message.from_user.id in ADMIN_IDS:
            await message.answer("🛠 Админ панель:", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ Нет активных действий для отмены")

@dp.message(F.text, ~StateFilter(AdminStates))
async def handle_text(message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user:
        await cmd_start(message)
        return
    
    if user[3] == 1:
        await message.answer("❌ Вы забанены")
        return

    if user[4] == "gpt-image-2":
        await bot.send_chat_action(user_id, "upload_photo")
        thinking_msg = await message.answer("🎨 Генерирую изображение...")
        image_bytes, caption, error = await generate_image(user_id, message.text, thinking_msg)
        if error:
            await message.answer(error)
        else:
            await message.answer_photo(
                BufferedInputFile(image_bytes, filename="generated.png"),
                caption=caption,
            )
        return

    await bot.send_chat_action(user_id, "typing")
    
    thinking_msg = await message.answer("🤔 Думаю...")
    
    answer, error = await call_codex_api(user_id, message.text, thinking_message=thinking_msg)
    
    if error:
        try:
            await thinking_msg.delete()
        except:
            pass
        await message.answer(error)
    else:
        await message.answer(answer)

@dp.message(F.photo, ~StateFilter(AdminStates))
async def handle_photo(message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user:
        await cmd_start(message)
        return
    
    if user[3] == 1:
        await message.answer("❌ Вы забанены")
        return

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)

    if user[4] == "gpt-image-2":
        if not message.caption:
            await message.answer(
                "🖼 Для редактирования отправьте фото с подписью.\n"
                "Пример: «Замени фон на белый студийный»"
            )
            return

        buffer = io.BytesIO()
        await bot.download_file(file.file_path, buffer)
        image_bytes = buffer.getvalue()

        await bot.send_chat_action(user_id, "upload_photo")
        thinking_msg = await message.answer("✏️ Редактирую изображение...")
        result_bytes, caption, error = await edit_image(
            user_id, message.caption, image_bytes, thinking_msg
        )
        if error:
            await message.answer(error)
        else:
            await message.answer_photo(
                BufferedInputFile(result_bytes, filename="edited.png"),
                caption=caption,
            )
        return

    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    caption = message.caption or "Опиши это изображение"
    
    await bot.send_chat_action(user_id, "typing")
    
    thinking_msg = await message.answer("🤔 Думаю...")
    
    answer, error = await call_codex_api(user_id, caption, thinking_message=thinking_msg, image_url=file_url)
    
    if error:
        try:
            await thinking_msg.delete()
        except:
            pass
        await message.answer(error)
    else:
        await message.answer(answer)

# ================= INLINE КЛАВИАТУРА =================
@dp.callback_query(F.data == "back_home")
async def back_home(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    model_info = MODELS[user[4]]
    priority_info = PRIORITY_MODES[user[5]]
    
    text = (
        f"🏠 Главное меню\n\n"
        f"💎 Баланс: {user[2]} токенов\n"
        f"🧠 Модель: {model_info['name']} ({model_info['multiplier']}x)\n"
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']} ({priority_info['multiplier']}x)"
    )
    
    await safe_edit_message(callback, text, get_main_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "menu_models")
async def show_models(callback: CallbackQuery):
    text = (
        "🎛 Выберите модель ИИ\n\n"
        "📊 Доступные модели:\n"
        "• GPT-5.4 — базовый тариф (x1)\n"
        "• GPT-5.4 Mini — экономная (x0.9)\n"
        "• GPT-5.5 — мощная (x4.5)\n"
        "• GPT-IMAGE 2 — генерация и редактирование (x3)\n\n"
        "🖼 GPT-IMAGE 2:\n"
        "• Текст → сгенерирует картинку\n"
        "• Фото + подпись → отредактирует картинку\n\n"
        "💡 Кеш считается полностью (100% списание)"
    )
    await safe_edit_message(callback, text, get_models_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("model_"))
async def set_user_model(callback: CallbackQuery):
    model_key = callback.data.replace("model_", "")
    user_id = callback.from_user.id
    
    if model_key in MODELS:
        set_model(user_id, model_key)
        user = get_user(user_id)
        model_info = MODELS[model_key]
        price = int(BASE_PRICE_PER_1K * model_info['multiplier'])
        
        text = (
            f"✅ Модель изменена на {model_info['name']}\n\n"
            f"📊 Коэффициент: {model_info['multiplier']}x\n"
            f"💰 Стоимость: {price} токенов за 1K токенов\n"
            f"💎 Ваш баланс: {user[2]} токенов"
        )
        if model_key == "gpt-image-2":
            text += (
                "\n\n🖼 Как пользоваться:\n"
                "• Напишите текст — бот сгенерирует картинку\n"
                "• Отправьте фото с подписью — бот отредактирует её"
            )
        await safe_edit_message(callback, text, get_main_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "menu_priority")
async def show_priority(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    current_mode = user[5]
    
    text = (
        "⚡ Выбор скорости запросов\n\n"
        "• 🐢 Обычный режим (x1) — стандартная скорость\n"
        "• ⚡ Fast Speed (x2) — приоритетные запросы, в 2 раза дороже\n\n"
        "💡 Fast Speed подходит для срочных задач"
    )
    await safe_edit_message(callback, text, get_priority_keyboard(current_mode))
    await callback.answer()

@dp.callback_query(F.data.startswith("priority_"))
async def set_priority(callback: CallbackQuery):
    priority_key = callback.data.replace("priority_", "")
    user_id = callback.from_user.id
    
    if priority_key in PRIORITY_MODES:
        set_priority_mode(user_id, priority_key)
        user = get_user(user_id)
        priority_info = PRIORITY_MODES[priority_key]
        
        text = (
            f"✅ Скорость изменена на {priority_info['emoji']} {priority_info['name']}\n\n"
            f"📊 Коэффициент: {priority_info['multiplier']}x\n"
            f"💎 Ваш баланс: {user[2]} токенов"
        )
        await safe_edit_message(callback, text, get_main_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "menu_profile")
async def show_profile(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    model_info = MODELS[user[4]]
    priority_info = PRIORITY_MODES[user[5]]
    model_price = int(BASE_PRICE_PER_1K * model_info['multiplier'])
    
    text = (
        f"👤 Ваш профиль\n\n"
        f"🆔 ID: {user[0]}\n"
        f"📛 Username: @{user[1]}\n"
        f"💎 Баланс: {user[2]} токенов\n\n"
        f"🧠 Модель: {model_info['name']}\n"
        f"   └ Коэффициент: {model_info['multiplier']}x\n"
        f"   └ Стоимость: {model_price} ток/1K\n\n"
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']}\n"
        f"   └ Коэффициент: {priority_info['multiplier']}x\n\n"
        f"🚫 Статус: {'🔴 Забанен' if user[3] else '🟢 Активен'}\n"
        f"📅 Регистрация: {user[6][:10] if user[6] else 'сегодня'}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="menu_profile")
    builder.button(text="🔙 Назад", callback_data="back_home")
    
    await safe_edit_message(callback, text, builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "menu_buy")
async def buy_tokens(callback: CallbackQuery):
    text = (
        "💰 Покупка токенов\n\n"
        "📊 Цены:\n"
        "• 100 000 токенов — 20₽\n"
        "• 500 000 токенов — 90₽\n"
        "• 1 000 000 токенов — 150₽\n\n"
        "📝 Как купить:\n"
        "1️⃣ Напишите @SedoyDiada\n"
        "2️⃣ Укажите нужное количество токенов\n"
        "3️⃣ Оплатите любым удобным способом\n"
        "4️⃣ Токены будут зачислены на баланс\n\n"
        "💡 По вопросам: @SedoyDiada"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📨 Написать создателю", url="https://t.me/SedoyDiada")
    builder.button(text="🔙 Назад", callback_data="back_home")
    
    await safe_edit_message(callback, text, builder.as_markup())
    await callback.answer()

# ================= АДМИН ПАНЕЛЬ =================

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    """Показать админ-панель"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await safe_edit_message(callback, "🛠 Админ панель:", get_admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_list")
async def admin_list_users(callback: CallbackQuery):
    """Список пользователей"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    users = get_all_users()
    if not users:
        await callback.answer("📭 Нет пользователей", show_alert=True)
        return
    
    text = "👥 Список пользователей:\n\n"
    for user in users[:20]:
        status = "🔴 БАН" if user[3] else "🟢 ОК"
        text += f"• ID: {user[0]} | @{user[1]} | Баланс: {user[2]} | {status}\n"
    
    if len(users) > 20:
        text += f"\n📊 Всего: {len(users)} пользователей"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin_panel")
    
    await safe_edit_message(callback, text, builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    """Статистика"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    users = get_all_users()
    total_users = len(users)
    total_balance = sum(user[2] for user in users)
    banned_users = sum(1 for user in users if user[3])
    
    text = (
        f"📊 Статистика бота:\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"🚫 Забанено: {banned_users}\n"
        f"💰 Общий баланс: {total_balance} токенов\n"
        f"💰 Средний баланс: {total_balance // total_users if total_users > 0 else 0} токенов"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="admin_stats")
    builder.button(text="🔙 Назад", callback_data="admin_panel")
    
    await safe_edit_message(callback, text, builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "admin_ban")
async def admin_ban_menu(callback: CallbackQuery, state: FSMContext):
    """Бан/Разбан пользователя"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await state.update_data(admin_action="ban")
    await state.set_state(AdminStates.waiting_for_user_id)
    
    await callback.answer()
    await callback.message.answer(
        "🔍 Введите Telegram ID или username (без @) пользователя, чтобы забанить/разбанить:"
    )

@dp.callback_query(F.data == "admin_give")
async def admin_give_menu(callback: CallbackQuery, state: FSMContext):
    """Выдать токены"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await state.update_data(admin_action="give")
    await state.set_state(AdminStates.waiting_for_user_id)
    
    await callback.answer()
    await callback.message.answer(
        "🔍 Введите Telegram ID или username (без @) пользователя, чтобы выдать токены:"
    )

@dp.callback_query(F.data == "admin_take")
async def admin_take_menu(callback: CallbackQuery, state: FSMContext):
    """Забрать токены"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await state.update_data(admin_action="take")
    await state.set_state(AdminStates.waiting_for_user_id)
    
    await callback.answer()
    await callback.message.answer(
        "🔍 Введите Telegram ID или username (без @) пользователя, чтобы забрать токены:"
    )

@dp.message(AdminStates.waiting_for_user_id)
async def process_admin_user_input(message: Message, state: FSMContext):
    """Получаем ID пользователя от админа"""
    user_input = message.text.strip()
    target_id = None
    
    # Пробуем найти пользователя
    if user_input.isdigit():
        target_id = int(user_input)
    else:
        clean_username = user_input.replace('@', '')
        res = find_user_by_username(clean_username)
        if res:
            target_id = res[0]
    
    if not target_id:
        await message.answer("❌ Пользователь не найден. Проверьте ID или username.")
        return
    
    # Проверяем, существует ли пользователь в БД
    user = get_user(target_id)
    if not user:
        await message.answer(f"❌ Пользователь с ID {target_id} не найден в базе данных.")
        return
    
    # Сохраняем target_id в состояние
    await state.update_data(target_id=target_id)
    
    # Получаем действие
    data = await state.get_data()
    action_type = data.get('admin_action')
    
    logging.info(f"Admin action: {action_type}, target_id: {target_id}")
    
    if action_type == "ban":
        # Сразу баним/разбаниваем
        new_status = 0 if user[3] == 1 else 1
        set_ban_status(target_id, new_status)
        status_text = "разбанен ✅" if new_status == 0 else "забанен 🚫"
        await message.answer(
            f"✅ Пользователь {target_id} (@{user[1]}) {status_text}"
        )
        await state.clear()
        await message.answer("🛠 Админ панель:", reply_markup=get_admin_keyboard())
    
    elif action_type in ["give", "take"]:
        # Переходим к запросу количества токенов
        await state.set_state(AdminStates.waiting_for_token_amount)
        action_name = "выдать" if action_type == "give" else "забрать"
        await message.answer(f"💰 Введите количество токенов, чтобы {action_name} пользователю {target_id} (@{user[1]}):\n\n(целое число, например: 1000)")
    
    else:
        await message.answer("❌ Ошибка: неизвестное действие")
        await state.clear()

@dp.message(AdminStates.waiting_for_token_amount)
async def process_admin_token_amount(message: Message, state: FSMContext):
    """Получаем количество токенов от админа"""
    if not message.text.isdigit():
        await message.answer("❌ Ошибка! Введите целое число (количество токенов):")
        return
    
    amount = int(message.text)
    if amount <= 0:
        await message.answer("❌ Количество токенов должно быть больше 0!")
        return
    
    # Получаем данные из состояния
    data = await state.get_data()
    target_id = data.get('target_id')
    action_type = data.get('admin_action')
    
    logging.info(f"Token amount process - target_id: {target_id}, action_type: {action_type}, amount: {amount}")
    
    if not target_id or not action_type:
        await message.answer("❌ Ошибка: данные потеряны. Начните заново с кнопки в админ-панели.")
        await state.clear()
        await message.answer("🛠 Админ панель:", reply_markup=get_admin_keyboard())
        return
    
    # Получаем пользователя
    user = get_user(target_id)
    if not user:
        await message.answer(f"❌ Пользователь с ID {target_id} не найден.")
        await state.clear()
        await message.answer("🛠 Админ панель:", reply_markup=get_admin_keyboard())
        return
    
    old_balance = user[2]
    
    if action_type == "give":
        new_balance = old_balance + amount
        update_tokens(target_id, new_balance)
        await message.answer(
            f"✅ Выдано {amount} токенов\n\n"
            f"👤 Пользователь: {target_id} (@{user[1]})\n"
            f"📊 Старый баланс: {old_balance}\n"
            f"➕ Добавлено: +{amount}\n"
            f"💎 Новый баланс: {new_balance}"
        )
    elif action_type == "take":
        new_balance = max(0, old_balance - amount)
        taken_amount = old_balance - new_balance
        update_tokens(target_id, new_balance)
        await message.answer(
            f"✅ Забрано {taken_amount} токенов\n\n"
            f"👤 Пользователь: {target_id} (@{user[1]})\n"
            f"📊 Старый баланс: {old_balance}\n"
            f"➖ Забрано: -{taken_amount}\n"
            f"💎 Новый баланс: {new_balance}"
        )
    
    # Очищаем состояние и возвращаем админ-панель
    await state.clear()
    await message.answer("🛠 Админ панель:", reply_markup=get_admin_keyboard())

# ================= ЗАПУСК =================
async def main():
    init_db()
    print("=" * 50)
    print("🚀 БОТ ЗАПУЩЕН!")
    print("=" * 50)
    print(f"👑 Админы: {ADMIN_IDS}")
    print(f"🖼 Image API: {IMAGES_GENERATIONS_ENDPOINT}")
    print(f"🤖 Доступные модели:")
    for key, val in MODELS.items():
        print(f"   - {val['name']} ({val['api_name']}) | x{val['multiplier']}")
    print(f"⚡ Режимы: {', '.join(PRIORITY_MODES.keys())}")
    print("=" * 50)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
