import asyncio
import base64
import io
import logging
import sqlite3
import json
import re
import aiohttp
from aiohttp import web
import tiktoken
from datetime import datetime, timedelta
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "8821975305:AAGyZwlu_5l2f2cD2iizZlQPVzcCXVxWtzY"  # Получи у @BotFather
BOT_USERNAME = "EnlaceAI_bot"  # Username бота (без @) — для successURL в ЮMoney
ADMIN_IDS = [5439940299]  # Твой Telegram ID
DAILY_LIMIT = 100000  # Дневной лимит токенов
CONTEXT_SIZE = 30  # Количество сообщений для сохранения контекста (пар: пользователь + ассистент)

# ================= НАСТРОЙКИ CODEX.SALE API =================
API_KEY = "sk-clb-cOqQjd24MYStR5-wn6EW5FEJDtlwxeiGNbztbuZi4jY"  # Твой API ключ
BASE_URL = "https://codex.sale"

# ================= НАСТРОЙКИ YOOMONEY =================
# ================= НАСТРОЙКИ YOOMONEY (ПРОСТОЙ КОШЕЛЕК) =================
YOOMONEY_WALLET = "4100119548062982"  
YOOMONEY_NOTIFICATION_SECRET = ""
YOOMONEY_WEBHOOK_URL = ""
YOOMONEY_CLIENT_ID = ""

# Пакеты токенов для покупки
TOKEN_PACKAGES = {
    "small": {"tokens": 100000, "price": 20, "label": "100 000 токенов"},
    "medium": {"tokens": 500000, "price": 90, "label": "500 000 токенов"},
    "large": {"tokens": 1000000, "price": 150, "label": "1 000 000 токенов"},
}

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

# ================= ХРАНИЛИЩЕ КОНТЕКСТА =================
# Структура: {user_id: {model_key: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}}
user_contexts = defaultdict(lambda: defaultdict(list))

def add_to_context(user_id: int, model_key: str, role: str, content: str):
    """Добавить сообщение в контекст пользователя для конкретной модели"""
    context = user_contexts[user_id][model_key]
    context.append({"role": role, "content": content})
    
    # Ограничиваем размер контекста (храним N последних пар сообщений)
    max_messages = CONTEXT_SIZE * 2  # *2 потому что user + assistant
    if len(context) > max_messages:
        user_contexts[user_id][model_key] = context[-max_messages:]

def get_context_messages(user_id: int, model_key: str, current_prompt: str) -> list:
    """Получить контекстные сообщения для отправки в API"""
    context = user_contexts[user_id].get(model_key, [])
    
    # Формируем список сообщений: сначала контекст, потом текущий запрос
    messages = []
    
    # Добавляем системный промпт для форматирования формул
    system_prompt = {
        "role": "system",
        "content": """Ты полезный ИИ-ассистент. Отвечай на русском языке.
ВАЖНО: Математические формулы и уравнения пиши в читаемом текстовом формате!
Примеры:
- Вместо "x^2 + y^2 = z^2" пиши "x² + y² = z²"
- Дроби пиши через дробную черту: "1/2", "3/4"
- Корни: "√(x)", "∛(x)"
- Интегралы: "∫ f(x) dx"
- Суммы: "∑ (i=1 to n) i²"
- Греческие буквы используй словами: "альфа", "бета", "пи"
- Для умножения используй "·" или "*"
- Для деления используй "/"
- Степени пиши с помощью Unicode: x², x³, x⁴, x⁵, x⁶, x⁷, x⁸, x⁹

Если нужно показать сложную формулу, разбивай её на несколько строк с пояснениями.
Используй символы Unicode для математических операций: ≠, ≤, ≥, ±, ∞, ≈, →, ⇒, ∀, ∃."""
    }
    messages.append(system_prompt)
    
    # Добавляем историю диалога
    messages.extend(context)
    
    # Добавляем текущий запрос
    messages.append({"role": "user", "content": current_prompt})
    
    return messages

def clear_context(user_id: int, model_key: str = None):
    """Очистить контекст пользователя"""
    if model_key:
        if model_key in user_contexts[user_id]:
            user_contexts[user_id][model_key] = []
    else:
        user_contexts[user_id] = defaultdict(list)

# ================= СОСТОЯНИЯ ДЛЯ АДМИНА =================
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_token_amount = State()
    waiting_for_broadcast_message = State()

# ================= ПОСТОЯННАЯ КЛАВИАТУРА =================
def get_main_reply_keyboard(user_id: int):
    """Постоянная клавиатура в самом боте (reply keyboard)"""
    keyboard = [
        [KeyboardButton(text="🤖 Модели"), KeyboardButton(text="⚡ Скорость")],
        [KeyboardButton(text="📊 Профиль"), KeyboardButton(text="💰 Купить токены")],
        [KeyboardButton(text="🧹 Очистить память")]
    ]
    
    # Добавляем кнопку админки для админов
    if user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton(text="🛠 Админ панель")])
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="💬 Напишите сообщение..."
    )

def get_subscribe_keyboard():
    """Инлайн клавиатура с кнопкой подписки на канал"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Подписаться на канал", url=CHANNEL_URL)
    builder.button(text="✅ Я подписался", callback_data="check_subscription")
    builder.adjust(1)
    return builder.as_markup()

# Канал для обязательной подписки
CHANNEL_USERNAME = "EnlaceAI"
CHANNEL_URL = "https://t.me/EnlaceAI"

def get_admin_reply_keyboard():
    """Клавиатура для админ-панели"""
    keyboard = [
        [KeyboardButton(text="👥 Список пользователей")],
        [KeyboardButton(text="🚫 Баны"), KeyboardButton(text="➕ Выдать токены")],
        [KeyboardButton(text="➖ Забрать токены"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📢 Рассылка")],
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

def format_response_for_telegram(text: str) -> str:
    """Форматирует ответ для корректного отображения в Telegram"""
    # Заменяем потенциально проблемные последовательности
    # Конвертируем некоторые LaTeX-подобные команды в Unicode
    replacements = {
        r'\alpha': 'α',
        r'\beta': 'β',
        r'\gamma': 'γ',
        r'\delta': 'δ',
        r'\epsilon': 'ε',
        r'\pi': 'π',
        r'\sigma': 'σ',
        r'\omega': 'ω',
        r'\infty': '∞',
        r'\neq': '≠',
        r'\le': '≤',
        r'\ge': '≥',
        r'\pm': '±',
        r'\cdot': '·',
        r'\times': '×',
        r'\sqrt': '√',
        r'\int': '∫',
        r'\sum': '∑',
        r'\prod': '∏',
    }
    
    for latex, unicode_char in replacements.items():
        text = text.replace(latex, unicode_char)
    
    # Убираем лишние символы, которые могут ломать Markdown
    # Но не экранируем весь текст, так как он в обычном режиме parse_mode=None
    
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

    # Таблица для отслеживания платежей
    cur.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            package_key TEXT,
            tokens_amount INTEGER,
            amount_rub INTEGER,
            payment_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            confirmed_at TEXT
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

# ================= YOOMONEY ПЛАТЕЖИ =================
def create_payment_record(user_id: int, package_key: str, payment_id: str):
    """Создать запись о платеже"""
    package = TOKEN_PACKAGES.get(package_key)
    if not package:
        return None
    
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute('''
        INSERT INTO payments (user_id, package_key, tokens_amount, amount_rub, payment_id, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, package_key, package["tokens"], package["price"], payment_id, "pending", now))
    conn.commit()
    payment_db_id = cur.lastrowid
    conn.close()
    return payment_db_id

def get_pending_payment(user_id: int, package_key: str):
    """Получить ожидающий платеж пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''
        SELECT id, payment_id FROM payments 
        WHERE user_id = ? AND package_key = ? AND status = 'pending'
        ORDER BY created_at DESC LIMIT 1
    ''', (user_id, package_key))
    row = cur.fetchone()
    conn.close()
    return row

def confirm_payment(payment_db_id: int, yoomoney_payment_id: str):
    """Подтвердить платеж и зачислить токены"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Получаем информацию о платеже
    cur.execute('SELECT user_id, tokens_amount, status FROM payments WHERE id = ?', (payment_db_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, "Платеж не найден"
    
    user_id, tokens_amount, status = row
    if status != 'pending':
        conn.close()
        return False, f"Платеж уже обработан (статус: {status})"
    
    # Зачисляем токены
    add_admin_tokens(user_id, tokens_amount)
    
    # Обновляем статус платежа
    now = datetime.now().isoformat()
    cur.execute('''
        UPDATE payments SET status = 'confirmed', confirmed_at = ?, payment_id = ?
        WHERE id = ?
    ''', (now, yoomoney_payment_id, payment_db_id))
    conn.commit()
    conn.close()
    
    logging.info(f"✅ Платеж {payment_db_id} подтвержден: пользователю {user_id} зачислено {tokens_amount} токенов")
    return True, f"Зачислено {tokens_amount} токенов"

def generate_yoomoney_payment_url(user_id: int, package_key: str) -> str | None:
    """Сгенерировать ссылку на оплату через ЮMoney"""
    if not YOOMONEY_WALLET:
        return None
    
    package = TOKEN_PACKAGES.get(package_key)
    if not package:
        return None
    
    # Генерируем уникальный ID платежа
    import uuid
    payment_label = f"tokens_{user_id}_{package_key}_{uuid.uuid4().hex[:8]}"
    
    # Создаем запись в БД
    create_payment_record(user_id, package_key, payment_label)
    
    # Формируем URL для ЮMoney (Quickpay / форму оплаты)
    # Используем форму оплаты ЮMoney
    params = {
        "receiver": YOOMONEY_WALLET,
        "quickpay-form": "shop",
        "targets": f"Покупка токенов: {package['label']}",
        "paymentType": "SB",
        "sum": str(package["price"]),
        "label": payment_label,
        "successURL": f"https://t.me/{BOT_USERNAME}",
    }
    
    from urllib.parse import urlencode
    url = f"https://yoomoney.ru/quickpay/confirm.xml?{urlencode(params)}"
    return url

# Вебхук для уведомлений от ЮMoney (нужен веб-сервер)
async def handle_yoomoney_webhook(request_data: dict):
    """Обработать уведомление от ЮMoney"""
    # Проверяем подпись (SHA256)
    # Для простоты пока просто логируем
    logging.info(f"ЮMoney webhook: {request_data}")
    
    # Парсим данные
    notification_type = request_data.get("notification_type")
    label = request_data.get("label")
    operation_id = request_data.get("operation_id")
    status = request_data.get("status")
    
    if notification_type == "p2p-incoming" and status == "success" and label:
        # Ищем платеж по label
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute('SELECT id FROM payments WHERE payment_id = ? AND status = "pending"', (label,))
        row = cur.fetchone()
        conn.close()
        
        if row:
            payment_db_id = row[0]
            success, msg = confirm_payment(payment_db_id, operation_id or label)
            return success, msg
    
    return False, "Неподходящее уведомление"

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
    """Запрос к API codex.sale с реальным подсчётом токенов из ответа API и поддержкой контекста"""
    # Проверяем и обновляем дневной баланс
    reset_daily_balance_if_needed(user_id)
    
    user = get_user(user_id)
    if not user:
        return None, "Пользователь не найден"
    
    model_key = user[4]
    model_info = MODELS.get(model_key, MODELS["gpt-5.4"])
    priority_mode = user[5]
    
    # Формируем сообщения с контекстом
    if image_url and model_key == "gpt-5.5":
        # Для vision запросов пока не поддерживаем контекст (сложнее)
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}}
        ]}]
    else:
        # Обычный текстовый запрос с контекстом
        messages = get_context_messages(user_id, model_key, prompt)
    
    # Используем правильное название модели из документации
    api_model_name = model_info["api_name"]
    
    payload = {
        "model": api_model_name,
        "messages": messages,
        "max_tokens": 4000,  # Увеличил для более длинных ответов
        "temperature": 0.7
    }
    
    # Добавляем приоритет, если Fast Speed
    if priority_mode == "fast":
        payload["priority"] = "high"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(API_URL, headers=HEADERS, json=payload, timeout=90) as resp:
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
                    # Fallback - грубая оценка
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
                
                # Сохраняем в контекст (только для текстовых запросов без изображений)
                if not image_url:
                    add_to_context(user_id, model_key, "user", prompt)
                    add_to_context(user_id, model_key, "assistant", answer)
                
                # Удаляем сообщение "ДУМАЮ..."
                if thinking_message:
                    try:
                        await thinking_message.delete()
                    except:
                        pass
                
                # Получаем информацию о балансе
                balance_info = get_balance_info(user_id)
                
                # Форматируем ответ для Telegram
                formatted_answer = format_response_for_telegram(answer)
                
                footer = f"\n\n💰 Списано: {user_tokens_cost:.0f}\n💎 Осталось: {balance_info['total']:.0f}"
                
                # Добавляем ответ ИИ с футером
                final_answer = formatted_answer + footer
                
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
    
    # Проверяем подписку на канал
    is_subscribed = await check_user_subscription(user_id)
    if not is_subscribed:
        await message.answer(
            f"👋 Привет, {message.from_user.first_name}!\n\n"
            f"Для использования бота необходимо подписаться на наш канал:\n"
            f"📢 @{CHANNEL_USERNAME}\n\n"
            f"Нажмите кнопку ниже, чтобы подписаться:",
            reply_markup=get_subscribe_keyboard()
        )
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
        f"🧠 Бот помнит последние {CONTEXT_SIZE} сообщений для каждой модели!\n"
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

@dp.message(Command("clear"))
async def cmd_clear_context(message: Message):
    """Очистить контекст диалога"""
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user or user[3] == 1:
        return
    
    model_key = user[4]
    clear_context(user_id, model_key)
    await message.answer(f"🧹 Контекст диалога для модели {MODELS[model_key]['name']} очищен!")

# ================= АДМИН ОБРАБОТЧИКИ (ДО общего текстового!) =================
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

@dp.message(F.text == "🧹 Очистить память")
async def clear_memory_button(message: Message):
    """Кнопка очистки памяти"""
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user or user[3] == 1:
        return
    
    model_key = user[4]
    clear_context(user_id, model_key)
    await message.answer(f"🧹 Контекст диалога для модели {MODELS[model_key]['name']} очищен! Теперь я не помню предыдущие сообщения.")

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
    
    # Подсчёт активных контекстов
    active_contexts = sum(1 for user in user_contexts for model in user_contexts[user] if user_contexts[user][model])
    
    text = (
        f"📊 Статистика бота:\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"🚫 Забанено: {banned_users}\n"
        f"💰 Общий дневной баланс: {total_daily}\n"
        f"💎 Общий админский баланс: {total_admin}\n"
        f"📊 Общий баланс: {total_balance}\n"
        f"📅 Дневной лимит на пользователя: {DAILY_LIMIT}\n"
        f"🧠 Активных контекстов: {active_contexts}\n"
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

@dp.message(F.text == "📢 Рассылка")
async def admin_broadcast_menu(message: Message, state: FSMContext):
    """Меню рассылки"""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await message.answer(
        "📢 Введите сообщение для рассылки всем пользователям:\n\n"
        "Поддерживается HTML-разметка:\n"
        "• <b>жирный</b>\n"
        "• <i>курсив</i>\n"
        "• <code>код</code>\n"
        "• <a href=\"https://t.me/EnlaceAI\">ссылка</a>\n\n"
        "Для отмены введите /cancel"
    )

@dp.message(AdminStates.waiting_for_broadcast_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка сообщения для рассылки"""
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    
    broadcast_text = message.html_text
    
    # Получаем всех пользователей
    users = get_all_users()
    if not users:
        await message.answer("📭 Нет пользователей для рассылки")
        await state.clear()
        return
    
    # Отправляем подтверждение
    confirm_text = (
        f"📢 Подтвердите рассылку\n\n"
        f"👥 Получателей: {len(users)}\n\n"
        f"📝 Текст сообщения:\n{broadcast_text[:500]}"
        f"{'...' if len(broadcast_text) > 500 else ''}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить", callback_data="confirm_broadcast")
    builder.button(text="❌ Отмена", callback_data="cancel_broadcast")
    builder.adjust(2)
    
    await state.update_data(broadcast_text=broadcast_text)
    await message.answer(confirm_text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "confirm_broadcast")
async def confirm_broadcast(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и отправка рассылки"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа")
        return
    
    data = await state.get_data()
    broadcast_text = data.get('broadcast_text')
    
    if not broadcast_text:
        await callback.answer("❌ Текст рассылки не найден")
        return
    
    users = get_all_users()
    sent = 0
    failed = 0
    
    await callback.message.edit_text("📤 Начинаю рассылку...")
    
    for user in users:
        user_id = user[0]
        if user[4] == 1:  # banned
            continue
        try:
            await bot.send_message(user_id, broadcast_text, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)  # Небольшая задержка чтобы не попасть в лимиты
        except Exception as e:
            failed += 1
            logging.error(f"Broadcast failed for {user_id}: {e}")
    
    await callback.message.edit_text(
        f"✅ Рассылка завершена\n\n"
        f"📤 Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}\n"
        f"👥 Всего пользователей: {len(users)}"
    )
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Отмена рассылки"""
    await state.clear()
    await callback.message.edit_text("❌ Рассылка отменена")
    await callback.answer()

# ================= ПРОВЕРКА ПОДПИСКИ =================
async def check_user_subscription(user_id: int) -> bool:
    """Проверить подписку пользователя на канал"""
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Subscription check error for {user_id}: {e}")
        return False

@dp.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery):
    """Проверка подписки при нажатии кнопки"""
    user_id = callback.from_user.id
    is_subscribed = await check_user_subscription(user_id)
    
    if is_subscribed:
        await callback.answer("✅ Вы подписаны на канал! Спасибо!")
        await callback.message.edit_text(
            "✅ Спасибо за подписку!\n\nТеперь вы можете пользоваться ботом.",
            reply_markup=None
        )
    else:
        await callback.answer("❌ Вы не подписаны на канал", show_alert=True)

# ================= ОСНОВНЫЕ КНОПКИ ПОЛЬЗОВАТЕЛЯ =================
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
        "• Фото + подпись → отредактирует картинку\n\n"
        "💡 При смене модели контекст диалога НЕ очищается автоматически.\n"
        "Используйте кнопку 🧹 Очистить память при необходимости."
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
    
    # Получаем размер контекста для текущей модели
    context_size = len(user_contexts[user_id].get(user[4], []))
    
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
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']}\n"
        f"🧠 Память: {context_size // 2} сообщений в истории\n\n"
        f"🚫 Статус: {'🔴 Забанен' if user[3] else '🟢 Активен'}\n"
        f"📅 Регистрация: {user[6][:10] if user[6] else 'сегодня'}\n\n"
        f"💡 Дневной лимит обновляется каждые 24 часа\n"
        f"💎 Админские токены можно купить у @SedoyDiada\n"
        f"🧹 Для очистки памяти используйте кнопку в меню"
    )
    
    await message.answer(text)

@dp.message(F.text == "💰 Купить токены")
async def buy_tokens_button(message: Message):
    """Кнопка покупки токенов"""
    user_id = message.from_user.id
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
    )
    
    builder = InlineKeyboardBuilder()
    
    # Добавляем кнопки оплаты через ЮMoney если настроено
    if YOOMONEY_WALLET:
        text += "💳 Оплатите через ЮMoney — токены зачислятся автоматически:\n\n"
        for key, pkg in TOKEN_PACKAGES.items():
            builder.button(
                text=f"{pkg['label']} — {pkg['price']}₽",
                callback_data=f"buy_tokens_{key}"
            )
        builder.button(text="📨 Написать создателю", url="https://t.me/SedoyDiada")
    else:
        text += (
            "📝 Как купить:\n"
            "1️⃣ Напишите @SedoyDiada\n"
            "2️⃣ Укажите нужное количество токенов\n"
            "3️⃣ Оплатите любым удобным способом\n"
            "4️⃣ Админские токены будут зачислены на баланс\n\n"
        )
        builder.button(text="📨 Написать создателю", url="https://t.me/SedoyDiada")
    
    # Важная инструкция с ID пользователя
    text += (
        "📝 **Важно!**\n"
        "В комментарии к переводу укажите свой Telegram ID:\n"
        f"`{user_id}`\n\n"
        "Как это сделать: при переводе есть поле «Комментарий»"
    )
    
    builder.button(text="🔄 Обновить профиль", callback_data="refresh_profile")
    builder.adjust(1)
    
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("buy_tokens_"))
async def process_token_purchase(callback: CallbackQuery):
    """Обработка покупки токенов через ЮMoney"""
    user_id = callback.from_user.id
    package_key = callback.data.replace("buy_tokens_", "")
    
    package = TOKEN_PACKAGES.get(package_key)
    if not package:
        await callback.answer("❌ Пакет не найден")
        return
    
    if not YOOMONEY_WALLET:
        await callback.answer("❌ Оплата временно недоступна")
        return
    
    # Генерируем ссылку на оплату
    payment_url = generate_yoomoney_payment_url(user_id, package_key)
    
    if not payment_url:
        await callback.answer("❌ Ошибка создания платежа")
        return
    
    text = (
        f"💳 Оплата: {package['label']} — {package['price']}₽\n\n"
        f"Нажмите кнопку ниже для перехода к оплате через ЮMoney.\n"
        f"После успешной оплаты токены ({package['tokens']:,}) зачислятся автоматически.\n\n"
        f"📝 **Важно!**\n"
        f"В комментарии к переводу укажите свой Telegram ID:\n"
        f"`{user_id}`\n\n"
        f"Как это сделать: при переводе есть поле «Комментарий»"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить через ЮMoney", url=payment_url)
    builder.button(text="🔙 Назад", callback_data="back_to_buy_tokens")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@dp.callback_query(F.data == "back_to_buy_tokens")
async def back_to_buy_tokens(callback: CallbackQuery):
    """Возврат в меню покупки токенов"""
    await buy_tokens_button(callback.message)
    await callback.answer()

# ================= ОБЩИЙ ОБРАБОТЧИК ТЕКСТА (В САМЫЙ КОНЕЦ!) =================
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
    
    # Проверяем подписку
    is_subscribed = await check_user_subscription(user_id)
    if not is_subscribed:
        await message.answer(
            f"📢 Для использования бота необходимо подписаться на канал @{CHANNEL_USERNAME}",
            reply_markup=get_subscribe_keyboard()
        )
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
        # Отправляем ответ без parse_mode, чтобы не ломались формулы
        await message.answer(answer, parse_mode=None)

# ================= ОБРАБОТЧИК ФОТО =================
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
    
    # Проверяем подписку
    is_subscribed = await check_user_subscription(user_id)
    if not is_subscribed:
        await message.answer(
            f"📢 Для использования бота необходимо подписаться на канал @{CHANNEL_USERNAME}",
            reply_markup=get_subscribe_keyboard()
        )
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
        await message.answer(answer, parse_mode=None)

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
    
    context_size = len(user_contexts[user_id].get(user[4], []))
    
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
        f"⚡ Скорость: {priority_info['emoji']} {priority_info['name']}\n"
        f"🧠 Память: {context_size // 2} сообщений в истории\n\n"
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
        
        text = f"✅ Модель изменена на {model_info['name']}\n\n"
        
        if model_key == "gpt-image-2":
            text += (
                "🖼 Как пользоваться:\n"
                "• Напишите текст — бот сгенерирует картинку\n"
                "• Отправьте фото с подписью — бот отредактирует её\n\n"
            )
        else:
            text += (
                f"🧠 Контекст диалога для этой модели сохранён отдельно.\n"
                f"💡 Для очистки памяти используйте кнопку 🧹 Очистить память"
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

# ================= YOOMONEY WEBHOOK СЕРВЕР =================

async def yoomoney_webhook_handler(request):
    """Обработчик вебхуков от ЮMoney"""
    try:
        data = await request.post()
        data_dict = dict(data)
        logging.info(f"ЮMoney webhook received: {data_dict}")
        
        # Проверяем подпись (опционально, для безопасности)
        # Здесь можно добавить проверку SHA256 хеша
        
        notification_type = data_dict.get("notification_type")
        label = data_dict.get("label")
        operation_id = data_dict.get("operation_id")
        status = data_dict.get("status")
        amount = data_dict.get("amount")
        withdraw_amount = data_dict.get("withdraw_amount")
        
        if notification_type == "p2p-incoming" and status == "success" and label:
            # Ищем платеж по label
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute('SELECT id FROM payments WHERE payment_id = ? AND status = "pending"', (label,))
            row = cur.fetchone()
            conn.close()
            
            if row:
                payment_db_id = row[0]
                success, msg = confirm_payment(payment_db_id, operation_id or label)
                if success:
                    # Уведомляем пользователя
                    try:
                        cur = conn.cursor()
                        cur.execute('SELECT user_id, tokens_amount FROM payments WHERE id = ?', (payment_db_id,))
                        pay_info = cur.fetchone()
                        conn.close()
                        if pay_info:
                            user_id, tokens_amount = pay_info
                            await bot.send_message(
                                user_id,
                                f"✅ Оплата прошла успешно!\n\n"
                                f"💎 Зачислено: {tokens_amount:,} админских токенов\n"
                                f"💳 Сумма: {withdraw_amount or amount}₽\n\n"
                                f"Токены уже доступны для использования!"
                            )
                    except Exception as e:
                        logging.error(f"Ошибка уведомления пользователя: {e}")
                    return web.Response(text="OK")
        
        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return web.Response(text="ERROR", status=500)


async def start_webhook_server():
    """Запуск веб-сервера для вебхуков"""
    if not YOOMONEY_WALLET:
        logging.info("ЮMoney не настроен, вебхук сервер не запускается")
        return None
    
    app = web.Application()
    app.router.add_post("/yoomoney-webhook", yoomoney_webhook_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logging.info("🌐 Вебхук сервер запущен на порту 8080")
    return runner


# ================= ЗАПУСК =================
async def main():
    init_db()
    
    # Запускаем фоновую задачу для периодической проверки сброса балансов
    async def periodic_reset_check():
        while True:
            await asyncio.sleep(3600)  # Проверяем каждый час
            check_and_reset_all_users()
    
    asyncio.create_task(periodic_reset_check())
    
    # Запускаем вебхук сервер для ЮMoney
    webhook_runner = await start_webhook_server()
    
    print("=" * 50)
    print("🚀 БОТ ЗАПУЩЕН!")
    print("=" * 50)
    print(f"👑 Админы: {ADMIN_IDS}")
    print(f"📅 Дневной лимит: {DAILY_LIMIT} токенов (обновляется каждые 24 часа)")
    print(f"💰 Честное списание токенов по данным от API")
    print(f"💎 Админские токены накапливаются и не сгорают")
    print(f"🧠 Контекст диалога: {CONTEXT_SIZE} сообщений")
    if YOOMONEY_WALLET:
        print(f"💳 ЮMoney: настроен (вебхук на порту 8080)")
    else:
        print(f"💳 ЮMoney: НЕ настроен (добавьте YOOMONEY_WALLET)")
    print("=" * 50)
    
    try:
        await dp.start_polling(bot)
    finally:
        if webhook_runner:
            await webhook_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())