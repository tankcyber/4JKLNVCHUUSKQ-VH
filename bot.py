import asyncio
import base64
import io
import logging
import sqlite3
import json
import re
import aiohttp
import tiktoken
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "8821975305:AAGyZwlu_5l2f2cD2iizZlQPVzcCXVxWtzY"  # Получи у @BotFather
ADMIN_IDS = [5439940299]  # Твой Telegram ID
DAILY_LIMIT = 100000  # Дневной лимит токенов

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

# ================= НАСТРОЙКИ СТОИМОСТИ =================
MODELS = {
    "gpt-5.4": {
        "name": "GPT-5.4", 
        "multiplier": 1.5, 
        "api_name": "gpt-5.4",
        "description": "Базовый тариф"
    },
    "gpt-5.4-mini": {
        "name": "GPT-5.4 Mini", 
        "multiplier": 1.0, 
        "api_name": "gpt-5.4-mini",
        "description": "Экономичная модель"
    },
    "gpt-5.5": {
        "name": "GPT-5.5", 
        "multiplier": 4.5, 
        "api_name": "gpt-5.5",
        "description": "Мощная модель"
    },
    "gpt-image-2": {
        "name": "GPT-IMAGE 2", 
        "multiplier": 3.0, 
        "api_name": "gpt-image-2",
        "description": "Для изображений"
    }
}

# Приоритетность запросов
PRIORITY_MODES = {
    "normal": {"name": "Обычный", "multiplier": 1.0, "emoji": "🐢"},
    "fast": {"name": "Fast Speed", "multiplier": 2.0, "emoji": "⚡"}
}

# База данных
DB_NAME = 'users.db'

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= СОСТОЯНИЯ ДЛЯ АДМИНА =================
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_token_amount = State()

# ================= ПОСТОЯННАЯ КЛАВИАТУРА =================
def get_main_reply_keyboard(user_id: int):
    """Постоянная клавиатура в самом боте (reply keyboard)"""
    keyboard = [
        [KeyboardButton(text="🤖 Модели"), KeyboardButton(text="⚡ Скорость")],
        [KeyboardButton(text="📊 Профиль"), KeyboardButton(text="💰 Купить токены")]
    ]
    
    # Добавляем кнопку админки для админов
    if user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton(text="🛠 Админ панель")])
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="💬 Напишите сообщение..."
    )

def get_admin_reply_keyboard():
    """Клавиатура для админ-панели"""
    keyboard = [
        [KeyboardButton(text="👥 Список пользователей")],
        [KeyboardButton(text="🚫 Баны"), KeyboardButton(text="➕ Выдать токены")],
        [KeyboardButton(text="➖ Забрать токены"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔙 Главное меню")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="🛠 Выберите действие..."
    )

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
    # Основная таблица пользователей
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            tokens_balance INTEGER DEFAULT 100000,
            is_banned INTEGER DEFAULT 0,
            current_model TEXT DEFAULT 'gpt-5.4',
            priority_mode TEXT DEFAULT 'normal',
            registered_at TEXT,
            admin_tokens INTEGER DEFAULT 0,
            last_daily_reset TEXT
        )
    ''')
    
    # Проверяем и добавляем новые колонки, если их нет
    cur.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cur.fetchall()]
    
    if 'admin_tokens' not in columns:
        cur.execute('ALTER TABLE users ADD COLUMN admin_tokens INTEGER DEFAULT 0')
    if 'last_daily_reset' not in columns:
        cur.execute('ALTER TABLE users ADD COLUMN last_daily_reset TEXT')
    
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
        cur.execute('''
            INSERT INTO users (user_id, username, registered_at, tokens_balance, admin_tokens, last_daily_reset) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, now, DAILY_LIMIT, 0, now))
        conn.commit()
    except Exception as e:
        logging.error(f"Add user error: {e}")
    conn.close()

def update_user_balance(user_id, tokens_to_spend):
    """Списать токены (сначала из дневного лимита, потом из админских)"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Получаем текущие балансы
    cur.execute('SELECT tokens_balance, admin_tokens FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    
    daily_balance = row[0]
    admin_balance = row[1]
    
    # Списываем сначала из дневного баланса
    if daily_balance >= tokens_to_spend:
        new_daily = daily_balance - tokens_to_spend
        new_admin = admin_balance
    else:
        # Не хватает дневных - берем из админских
        needed_from_admin = tokens_to_spend - daily_balance
        if admin_balance >= needed_from_admin:
            new_daily = 0
            new_admin = admin_balance - needed_from_admin
        else:
            # Не хватает даже с админскими
            conn.close()
            return False
    
    cur.execute('UPDATE users SET tokens_balance = ?, admin_tokens = ? WHERE user_id = ?', 
                (new_daily, new_admin, user_id))
    conn.commit()
    conn.close()
    return True

def get_total_balance(user_id):
    """Получить общий баланс (дневной + админский)"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT tokens_balance, admin_tokens FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0] + row[1]
    return 0

def add_admin_tokens(user_id, amount):
    """Добавить админские токены (не сгорают)"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('UPDATE users SET admin_tokens = admin_tokens + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def remove_admin_tokens(user_id, amount):
    """Забрать админские токены"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('UPDATE users SET admin_tokens = MAX(admin_tokens - ?, 0) WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def reset_daily_balance_if_needed(user_id):
    """Обновить дневной баланс, если прошло 24 часа с последнего сброса"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    cur.execute('SELECT tokens_balance, last_daily_reset FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    
    daily_balance = row[0]
    last_reset_str = row[1]
    
    now = datetime.now()
    need_reset = False
    
    if last_reset_str:
        try:
            last_reset = datetime.fromisoformat(last_reset_str)
            if now - last_reset >= timedelta(hours=24):
                need_reset = True
        except:
            need_reset = True
    else:
        need_reset = True
    
    if need_reset:
        # Сбрасываем дневной баланс до лимита (не трогаем admin_tokens)
        cur.execute('UPDATE users SET tokens_balance = ?, last_daily_reset = ? WHERE user_id = ?', 
                    (DAILY_LIMIT, now.isoformat(), user_id))
        conn.commit()
        logging.info(f"🔄 Дневной баланс сброшен для пользователя {user_id} до {DAILY_LIMIT}")
    
    conn.close()

def get_balance_info(user_id):
    """Получить детальную информацию о балансе"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT tokens_balance, admin_tokens, last_daily_reset FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        daily = row[0]
        admin = row[1]
        last_reset = row[2]
        
        # Проверяем, когда будет следующий сброс
        next_reset = None
        if last_reset:
            try:
                last_reset_time = datetime.fromisoformat(last_reset)
                next_reset = last_reset_time + timedelta(hours=24)
            except:
                pass
        
        return {
            'daily': daily,
            'admin': admin,
            'total': daily + admin,
            'last_reset': last_reset,
            'next_reset': next_reset
        }
    return None

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    res = cur.execute('SELECT user_id, username, tokens_balance, admin_tokens, is_banned FROM users').fetchall()
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

def check_and_reset_all_users():
    """Проверить и сбросить дневные балансы всех пользователей"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    cur.execute('SELECT user_id, last_daily_reset FROM users')
    users = cur.fetchall()
    
    now = datetime.now()
    reset_count = 0
    
    for user_id, last_reset_str in users:
        need_reset = False
        if last_reset_str:
            try:
                last_reset = datetime.fromisoformat(last_reset_str)
                if now - last_reset >= timedelta(hours=24):
                    need_reset = True
            except:
                need_reset = True
        else:
            need_reset = True
        
        if need_reset:
            cur.execute('UPDATE users SET tokens_balance = ?, last_daily_reset = ? WHERE user_id = ?', 
                        (DAILY_LIMIT, now.isoformat(), user_id))
            reset_count += 1
    
    conn.commit()
    conn.close()
    if reset_count > 0:
        logging.info(f"🔄 Сброшены дневные балансы для {reset_count} пользователей")

# ================= РАСЧЁТ СТОИМОСТИ =================
async def calculate_cost(user_id: int, prompt_tokens: int, completion_tokens: int = 0):
    """Рассчитать стоимость в токенах пользователя"""
    user = get_user(user_id)
    if not user:
        return 0

    model_key = user[4]
    model_mult = MODELS.get(model_key, {"multiplier": 1.0})["multiplier"]
    priority_key = user[5]
    priority_mult = PRIORITY_MODES.get(priority_key, {"multiplier": 1.0})["multiplier"]
    
    total_real_tokens = prompt_tokens + completion_tokens
    
    # Токены пользователя = реальные токены API * множитель модели * множитель скорости
    user_tokens = total_real_tokens * model_mult * priority_mult
    
    return user_tokens

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_models_inline_keyboard():
    """Инлайн клавиатура для выбора моделей"""
    builder = InlineKeyboardBuilder()
    for key, val in MODELS.items():
        builder.button(text=f"{val['name']} — {val['description']}", 
                      callback_data=f"model_{key}")
    builder.button(text="🔙 Назад", callback_data="back_home")
    builder.adjust(1)
    return builder.as_markup()

def get_priority_inline_keyboard(current_mode):
    """Инлайн клавиатура для выбора скорости"""
    builder = InlineKeyboardBuilder()
    for key, val in PRIORITY_MODES.items():
        marker = "✅ " if current_mode == key else ""
        builder.button(text=f"{marker}{val['emoji']} {val['name']}", callback_data=f"priority_{key}")
    builder.button(text="🔙 Назад", callback_data="back_home")
    builder.adjust(1)
    return builder.as_markup()

async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup=None, parse_mode=None):
    """Безопасное редактирование сообщения"""
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

async def _delete_thinking_message(thinking_message: Message | None):
    if thinking_message:
        try:
            await thinking_message.delete()
        except:
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

async def generate_image(user_id: int, prompt: str, thinking_message: Message = None):
    """Генерация изображения — списываем РЕАЛЬНЫЕ токены из ответа API"""
    reset_daily_balance_if_needed(user_id)
    
    user = get_user(user_id)
    if not user:
        return None, None, "Пользователь не найден"
    
    # Получаем множители
    model_mult = MODELS["gpt-image-2"]["multiplier"]  # 3.0
    priority_mult = PRIORITY_MODES.get(user[5], {"multiplier": 1.0})["multiplier"]
    
    # Максимально возможная стоимость изображения (подстраховка)
    # GPT-IMAGE 2 может тратить до 200000 токенов за изображение
    MAX_TOKENS_PER_IMAGE = 200000
    max_possible_cost = MAX_TOKENS_PER_IMAGE * model_mult * priority_mult
    
    total_balance = get_total_balance(user_id)
    if total_balance < max_possible_cost:
        await _delete_thinking_message(thinking_message)
        return None, None, f"❌ Недостаточно токенов! Нужно хотя бы {max_possible_cost:.0f} токенов на балансе, у вас {total_balance:.0f}"
    
    # Отправляем запрос к API
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
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Ошибка генерации ({resp.status}):\n{error_text[:300]}"

                data = await resp.json()
                
                # Получаем реальные токены из ответа API
                real_tokens = 0
                if 'usage' in data:
                    real_tokens = data['usage'].get('total_tokens', 0)
                else:
                    logging.error(f"No usage field in API response: {data}")
                    await _delete_thinking_message(thinking_message)
                    return None, None, "❌ Ошибка: API не вернул информацию о затраченных токенах"
                
                # Стоимость для пользователя
                cost = real_tokens * model_mult * priority_mult
                
                # Проверяем, хватает ли ТОЧНО токенов сейчас
                current_balance = get_total_balance(user_id)
                if current_balance < cost:
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Недостаточно токенов для оплаты! Нужно: {cost:.0f}, есть: {current_balance:.0f}"
                
                # Списываем токены
                success = update_user_balance(user_id, cost)
                if not success:
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Ошибка списания токенов"
                
                # Получаем изображение
                image_bytes = _parse_image_api_response(data)
                if not image_bytes and data.get("data"):
                    url = data["data"][0].get("url")
                    if url:
                        image_bytes = await _download_image_from_url(session, url)

                if not image_bytes:
                    # Возвращаем токены
                    update_user_balance(user_id, -cost)
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Не удалось получить изображение"

                await _delete_thinking_message(thinking_message)
                balance_info = get_balance_info(user_id)
                caption = (
                    f"🎨 Сгенерировано по запросу\n"
                    f"💰 Списано: {cost:.0f}\n"
                    f"💎 Осталось: {balance_info['total']:.0f}"
                )
                return image_bytes, caption, None

        except Exception as e:
            await _delete_thinking_message(thinking_message)
            logging.error(f"Image generation error: {e}", exc_info=True)
            return None, None, f"❌ Ошибка: {str(e)}"

async def edit_image(user_id: int, prompt: str, image_bytes: bytes, thinking_message: Message = None):
    """Редактирование изображения — списываем РЕАЛЬНЫЕ токены из ответа API"""
    reset_daily_balance_if_needed(user_id)
    
    user = get_user(user_id)
    if not user:
        return None, None, "Пользователь не найден"
    
    model_mult = MODELS["gpt-image-2"]["multiplier"]  # 3.0
    priority_mult = PRIORITY_MODES.get(user[5], {"multiplier": 1.0})["multiplier"]
    
    # Максимально возможная стоимость редактирования
    MAX_TOKENS_PER_EDIT = 200000
    max_possible_cost = MAX_TOKENS_PER_EDIT * model_mult * priority_mult
    
    total_balance = get_total_balance(user_id)
    if total_balance < max_possible_cost:
        await _delete_thinking_message(thinking_message)
        return None, None, f"❌ Недостаточно токенов! Нужно хотя бы {max_possible_cost:.0f} токенов на балансе, у вас {total_balance:.0f}"

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
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Ошибка редактирования ({resp.status}):\n{error_text[:300]}"

                data = await resp.json()
                
                # Получаем реальные токены из ответа API
                real_tokens = 0
                if 'usage' in data:
                    real_tokens = data['usage'].get('total_tokens', 0)
                else:
                    logging.error(f"No usage field in API response: {data}")
                    await _delete_thinking_message(thinking_message)
                    return None, None, "❌ Ошибка: API не вернул информацию о затраченных токенах"
                
                # Стоимость для пользователя
                cost = real_tokens * model_mult * priority_mult
                
                # Проверяем, хватает ли ТОЧНО токенов сейчас
                current_balance = get_total_balance(user_id)
                if current_balance < cost:
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Недостаточно токенов для оплаты! Нужно: {cost:.0f}, есть: {current_balance:.0f}"
                
                # Списываем токены
                success = update_user_balance(user_id, cost)
                if not success:
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Ошибка списания токенов"
                
                # Получаем изображение
                result_bytes = _parse_image_api_response(data)
                if not result_bytes and data.get("data"):
                    url = data["data"][0].get("url")
                    if url:
                        result_bytes = await _download_image_from_url(session, url)

                if not result_bytes:
                    update_user_balance(user_id, -cost)
                    await _delete_thinking_message(thinking_message)
                    return None, None, f"❌ Не удалось получить изображение"

                await _delete_thinking_message(thinking_message)
                balance_info = get_balance_info(user_id)
                caption = (
                    f"✏️ Отредактировано по запросу\n"
                    f"💰 Списано: {cost:.0f}\n"
                    f"💎 Осталось: {balance_info['total']:.0f}"
                )
                return result_bytes, caption, None

        except Exception as e:
            await _delete_thinking_message(thinking_message)
            logging.error(f"Image edit error: {e}", exc_info=True)
            return None, None, f"❌ Ошибка: {str(e)}"

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
    # Проверяем и обновляем дневной баланс
    reset_daily_balance_if_needed(user_id)
    
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
                
                # Извлекаем ответ
                answer = None
                if 'choices' in data and len(data['choices']) > 0:
                    if 'message' in data['choices'][0]:
                        answer = data['choices'][0]['message']['content']
                    elif 'text' in data['choices'][0]:
                        answer = data['choices'][0]['text']
                
                if not answer:
                    return None, f"❌ Неожиданный формат ответа:\n{str(data)[:200]}"
                
                # ===== БЕРЁМ РЕАЛЬНЫЕ ТОКЕНЫ ИЗ ОТВЕТА API =====
                prompt_tokens = 0
                completion_tokens = 0
                
                if 'usage' in data:
                    prompt_tokens = data['usage'].get('prompt_tokens', 0)
                    completion_tokens = data['usage'].get('completion_tokens', 0)
                else:
                    prompt_tokens = len(prompt) // 2 + 50
                    completion_tokens = len(answer) // 2 + 50
                
                # Расчёт стоимости в токенах пользователя (с учётом множителей)
                user_tokens_cost = await calculate_cost(user_id, prompt_tokens, completion_tokens)
                
                # Проверяем общий баланс
                total_balance = get_total_balance(user_id)
                if total_balance < user_tokens_cost:
                    if thinking_message:
                        try:
                            await thinking_message.delete()
                        except:
                            pass
                    return None, f"❌ Недостаточно токенов! Нужно: {user_tokens_cost:.0f}, есть: {total_balance:.0f}"
                
                # Списываем токены
                success = update_user_balance(user_id, user_tokens_cost)
                if not success:
                    if thinking_message:
                        try:
                            await thinking_message.delete()
                        except:
                            pass
                    return None, f"❌ Ошибка списания токенов"
                
                # Удаляем сообщение "ДУМАЮ..."
                if thinking_message:
                    try:
                        await thinking_message.delete()
                    except:
                        pass
                
                # Получаем информацию о балансе
                balance_info = get_balance_info(user_id)
                
                footer = f"\n\n💰 Списано: {user_tokens_cost:.0f}\n💎 Осталось: {balance_info['total']:.0f}"
                
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
    
    # Проверяем и обновляем дневной баланс
    reset_daily_balance_if_needed(user_id)
    
    balance_info = get_balance_info(user_id)
    model_info = MODELS[user[4]]
    priority_info = PRIORITY_MODES[user[5]]
    
    await message.answer(
        f"✨ Привет, {message.from_user.first_name}!\n\n"
        f"💰 Дневной лимит: {balance_info['daily']} / {DAILY_LIMIT}\n"
        f"💎 Админские токены: {balance_info['admin']}\n"
        f"📊 Всего токенов: {balance_info['total']}\n\n"
        f"🧠 Модель: {model_info['name']}\n"
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']}\n\n"
        f"📝 Просто напиши сообщение или отправь фото!\n\n"
        f"🖼 GPT-IMAGE 2: генерация фото + редактирование\n\n"
        f"📅 Дневной лимит обновляется каждые 24 часа!\n"
        f"🔽 Используйте кнопки внизу для навигации:",
        reply_markup=get_main_reply_keyboard(user_id)
    )

@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 Ваш Telegram ID: {message.from_user.id}")

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отменить текущее действие админа"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("✅ Действие отменено.", reply_markup=get_main_reply_keyboard(message.from_user.id))

# ================= ОБРАБОТЧИКИ ТЕКСТА И ФОТО =================
@dp.message(F.text == "🤖 Модели")
async def models_button(message: Message):
    """Кнопка выбора моделей"""
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user or user[3] == 1:
        return
    
    text = (
        "🎛 Выберите модель ИИ\n\n"
        "📊 Доступные модели:\n"
        "• GPT-5.4 — базовый тариф\n"
        "• GPT-5.4 Mini — экономная\n"
        "• GPT-5.5 — мощная\n"
        "• GPT-IMAGE 2 — генерация и редактирование изображений\n\n"
        "🖼 GPT-IMAGE 2:\n"
        "• Текст → сгенерирует картинку\n"
        "• Фото + подпись → отредактирует картинку"
    )
    await message.answer(text, reply_markup=get_models_inline_keyboard())

@dp.message(F.text == "⚡ Скорость")
async def priority_button(message: Message):
    """Кнопка выбора скорости"""
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user or user[3] == 1:
        return
    
    current_mode = user[5]
    text = (
        "⚡ Выбор скорости запросов\n\n"
        "• 🐢 Обычный режим — стандартная скорость\n"
        "• ⚡ Fast Speed — приоритетные запросы (ускоренная обработка)"
    )
    await message.answer(text, reply_markup=get_priority_inline_keyboard(current_mode))

@dp.message(F.text == "📊 Профиль")
async def profile_button(message: Message):
    """Кнопка профиля"""
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user:
        await cmd_start(message)
        return
    
    if user[3] == 1:
        await message.answer("❌ Вы забанены")
        return
    
    # Проверяем и обновляем дневной баланс
    reset_daily_balance_if_needed(user_id)
    
    balance_info = get_balance_info(user_id)
    model_info = MODELS[user[4]]
    priority_info = PRIORITY_MODES[user[5]]
    
    # Формируем информацию о следующем сбросе
    next_reset_text = "Скоро"
    if balance_info['next_reset']:
        now = datetime.now()
        time_left = balance_info['next_reset'] - now
        if time_left.total_seconds() > 0:
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            next_reset_text = f"через {hours}ч {minutes}мин"
    
    text = (
        f"👤 Ваш профиль\n\n"
        f"🆔 ID: {user[0]}\n"
        f"📛 Username: @{user[1]}\n\n"
        f"💰 Дневной баланс: {balance_info['daily']} / {DAILY_LIMIT}\n"
        f"💎 Админские токены: {balance_info['admin']}\n"
        f"📊 Всего токенов: {balance_info['total']}\n"
        f"⏳ Сброс дневного лимита: {next_reset_text}\n\n"
        f"🧠 Модель: {model_info['name']}\n"
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']}\n\n"
        f"🚫 Статус: {'🔴 Забанен' if user[3] else '🟢 Активен'}\n"
        f"📅 Регистрация: {user[6][:10] if user[6] else 'сегодня'}\n\n"
        f"💡 Дневной лимит обновляется каждые 24 часа\n"
        f"💎 Админские токены можно купить у @SedoyDiada"
    )
    
    await message.answer(text)

@dp.message(F.text == "💰 Купить токены")
async def buy_tokens_button(message: Message):
    """Кнопка покупки токенов"""
    text = (
        "💰 Покупка админских токенов\n\n"
        "📊 Цены:\n"
        "• 100 000 токенов — 20₽\n"
        "• 500 000 токенов — 90₽\n"
        "• 1 000 000 токенов — 150₽\n\n"
        "💡 Особенности:\n"
        "• Дневной лимит (100 000 токенов) обновляется каждые 24 часа\n"
        "• Админские токены накапливаются и НЕ сгорают\n"
        "• Сначала тратятся дневные токены, потом админские\n\n"
        "📝 Как купить:\n"
        "1️⃣ Напишите @SedoyDiada\n"
        "2️⃣ Укажите нужное количество токенов\n"
        "3️⃣ Оплатите любым удобным способом\n"
        "4️⃣ Админские токены будут зачислены на баланс\n\n"
        "💡 По вопросам: @SedoyDiada"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📨 Написать создателю", url="https://t.me/SedoyDiada")
    builder.button(text="🔄 Обновить профиль", callback_data="refresh_profile")
    
    await message.answer(text, reply_markup=builder.as_markup())

@dp.message(F.text == "🛠 Админ панель")
async def admin_panel_button(message: Message):
    """Кнопка админ панели"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа к админ панели")
        return
    
    await message.answer("🛠 Админ панель:", reply_markup=get_admin_reply_keyboard())

@dp.message(F.text == "🔙 Главное меню")
async def back_to_main_menu(message: Message):
    """Возврат в главное меню"""
    user_id = message.from_user.id
    await message.answer("🏠 Главное меню:", reply_markup=get_main_reply_keyboard(user_id))

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

# ================= INLINE CALLBACK ОБРАБОТЧИКИ =================
@dp.callback_query(F.data == "refresh_profile")
async def refresh_profile(callback: CallbackQuery):
    """Обновление профиля"""
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    if not user:
        await callback.answer("Пользователь не найден")
        return
    
    reset_daily_balance_if_needed(user_id)
    balance_info = get_balance_info(user_id)
    model_info = MODELS[user[4]]
    priority_info = PRIORITY_MODES[user[5]]
    
    next_reset_text = "Скоро"
    if balance_info['next_reset']:
        now = datetime.now()
        time_left = balance_info['next_reset'] - now
        if time_left.total_seconds() > 0:
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            next_reset_text = f"через {hours}ч {minutes}мин"
    
    text = (
        f"👤 Ваш профиль\n\n"
        f"🆔 ID: {user[0]}\n"
        f"📛 Username: @{user[1]}\n\n"
        f"💰 Дневной баланс: {balance_info['daily']} / {DAILY_LIMIT}\n"
        f"💎 Админские токены: {balance_info['admin']}\n"
        f"📊 Всего токенов: {balance_info['total']}\n"
        f"⏳ Сброс дневного лимита: {next_reset_text}\n\n"
        f"🧠 Модель: {model_info['name']}\n"
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']}\n\n"
        f"🚫 Статус: {'🔴 Забанен' if user[3] else '🟢 Активен'}"
    )
    
    await callback.message.edit_text(text)
    await callback.answer("✅ Профиль обновлён")

@dp.callback_query(F.data == "back_home")
async def back_home(callback: CallbackQuery):
    """Возврат в главное меню"""
    user_id = callback.from_user.id
    user = get_user(user_id)
    reset_daily_balance_if_needed(user_id)
    balance_info = get_balance_info(user_id)
    model_info = MODELS[user[4]]
    priority_info = PRIORITY_MODES[user[5]]
    
    text = (
        f"🏠 Главное меню\n\n"
        f"💰 Дневной баланс: {balance_info['daily']} / {DAILY_LIMIT}\n"
        f"💎 Админские токены: {balance_info['admin']}\n"
        f"📊 Всего: {balance_info['total']}\n"
        f"🧠 Модель: {model_info['name']}\n"
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']}"
    )
    
    await safe_edit_message(callback, text)
    await callback.answer()

@dp.callback_query(F.data.startswith("model_"))
async def set_user_model(callback: CallbackQuery):
    """Установка модели через инлайн кнопку"""
    model_key = callback.data.replace("model_", "")
    user_id = callback.from_user.id
    
    if model_key in MODELS:
        set_model(user_id, model_key)
        model_info = MODELS[model_key]
        
        text = f"✅ Модель изменена на {model_info['name']}"
        
        if model_key == "gpt-image-2":
            text += (
                "\n\n🖼 Как пользоваться:\n"
                "• Напишите текст — бот сгенерирует картинку\n"
                "• Отправьте фото с подписью — бот отредактирует её"
            )
        await safe_edit_message(callback, text)
    await callback.answer()

@dp.callback_query(F.data.startswith("priority_"))
async def set_priority(callback: CallbackQuery):
    """Установка скорости через инлайн кнопку"""
    priority_key = callback.data.replace("priority_", "")
    user_id = callback.from_user.id
    
    if priority_key in PRIORITY_MODES:
        set_priority_mode(user_id, priority_key)
        priority_info = PRIORITY_MODES[priority_key]
        
        text = f"✅ Скорость изменена на {priority_info['emoji']} {priority_info['name']}"
        await safe_edit_message(callback, text)
    await callback.answer()

# ================= АДМИН ОБРАБОТЧИКИ =================
@dp.message(F.text == "👥 Список пользователей")
async def admin_list_users(message: Message):
    """Список пользователей"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    users = get_all_users()
    if not users:
        await message.answer("📭 Нет пользователей")
        return
    
    text = "👥 Список пользователей:\n\n"
    for user in users[:20]:
        status = "🔴 БАН" if user[4] else "🟢 ОК"
        total = user[2] + user[3]  # daily + admin
        text += f"• ID: {user[0]} | @{user[1]} | Дневной: {user[2]} | Админ: {user[3]} | Всего: {total} | {status}\n"
    
    if len(users) > 20:
        text += f"\n📊 Всего: {len(users)} пользователей"
    
    await message.answer(text)

@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: Message):
    """Статистика"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    users = get_all_users()
    total_users = len(users)
    total_daily = sum(user[2] for user in users)
    total_admin = sum(user[3] for user in users)
    total_balance = total_daily + total_admin
    banned_users = sum(1 for user in users if user[4])
    
    text = (
        f"📊 Статистика бота:\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"🚫 Забанено: {banned_users}\n"
        f"💰 Общий дневной баланс: {total_daily}\n"
        f"💎 Общий админский баланс: {total_admin}\n"
        f"📊 Общий баланс: {total_balance}\n"
        f"📅 Дневной лимит на пользователя: {DAILY_LIMIT}\n"
        f"⏰ Сброс каждые 24 часа"
    )
    
    await message.answer(text)

@dp.message(F.text == "🚫 Баны")
async def admin_ban_menu(message: Message, state: FSMContext):
    """Бан/Разбан пользователя"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await state.update_data(admin_action="ban")
    await state.set_state(AdminStates.waiting_for_user_id)
    
    await message.answer(
        "🔍 Введите Telegram ID или username (без @) пользователя, чтобы забанить/разбанить:\n\n"
        "Для отмены введите /cancel"
    )

@dp.message(F.text == "➕ Выдать токены")
async def admin_give_menu(message: Message, state: FSMContext):
    """Выдать админские токены"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await state.update_data(admin_action="give")
    await state.set_state(AdminStates.waiting_for_user_id)
    
    await message.answer(
        "🔍 Введите Telegram ID или username (без @) пользователя, чтобы выдать админские токены:\n\n"
        "Для отмены введите /cancel"
    )

@dp.message(F.text == "➖ Забрать токены")
async def admin_take_menu(message: Message, state: FSMContext):
    """Забрать админские токены"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await state.update_data(admin_action="take")
    await state.set_state(AdminStates.waiting_for_user_id)
    
    await message.answer(
        "🔍 Введите Telegram ID или username (без @) пользователя, чтобы забрать админские токены:\n\n"
        "Для отмены введите /cancel"
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
    
    elif action_type in ["give", "take"]:
        # Переходим к запросу количества токенов
        await state.set_state(AdminStates.waiting_for_token_amount)
        action_name = "выдать" if action_type == "give" else "забрать"
        await message.answer(f"💰 Введите количество АДМИНСКИХ токенов, чтобы {action_name} пользователю {target_id} (@{user[1]}):\n\n(целое число, например: 1000)\n\nДля отмены введите /cancel")
    
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
        return
    
    # Получаем пользователя
    user = get_user(target_id)
    if not user:
        await message.answer(f"❌ Пользователь с ID {target_id} не найден.")
        await state.clear()
        return
    
    balance_info = get_balance_info(target_id)
    
    if action_type == "give":
        add_admin_tokens(target_id, amount)
        await message.answer(
            f"✅ Выдано {amount} админских токенов\n\n"
            f"👤 Пользователь: {target_id} (@{user[1]})\n"
            f"📊 Было дневных: {balance_info['daily']}\n"
            f"💎 Было админских: {balance_info['admin']}\n"
            f"➕ Добавлено: +{amount}\n"
            f"💎 Новый админский баланс: {balance_info['admin'] + amount}\n"
            f"📊 Новый общий баланс: {balance_info['daily'] + balance_info['admin'] + amount}"
        )
    elif action_type == "take":
        taken_amount = min(amount, balance_info['admin'])
        remove_admin_tokens(target_id, taken_amount)
        new_balance_info = get_balance_info(target_id)
        await message.answer(
            f"✅ Забрано {taken_amount} админских токенов\n\n"
            f"👤 Пользователь: {target_id} (@{user[1]})\n"
            f"📊 Было дневных: {balance_info['daily']}\n"
            f"💎 Было админских: {balance_info['admin']}\n"
            f"➖ Забрано: -{taken_amount}\n"
            f"💎 Новый админский баланс: {new_balance_info['admin']}\n"
            f"📊 Новый общий баланс: {new_balance_info['total']}"
        )
    
    # Очищаем состояние
    await state.clear()
    await message.answer("🛠 Продолжайте работу в админ панели:", reply_markup=get_admin_reply_keyboard())

# ================= ЗАПУСК =================
async def main():
    init_db()
    
    # Запускаем фоновую задачу для периодической проверки сброса балансов
    async def periodic_reset_check():
        while True:
            await asyncio.sleep(3600)  # Проверяем каждый час
            check_and_reset_all_users()
    
    asyncio.create_task(periodic_reset_check())
    
    print("=" * 50)
    print("🚀 БОТ ЗАПУЩЕН!")
    print("=" * 50)
    print(f"👑 Админы: {ADMIN_IDS}")
    print(f"📅 Дневной лимит: {DAILY_LIMIT} токенов (обновляется каждые 24 часа)")
    print(f"💰 Честное списание токенов по данным от API")
    print(f"💎 Админские токены накапливаются и не сгорают")
    print("=" * 50)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
