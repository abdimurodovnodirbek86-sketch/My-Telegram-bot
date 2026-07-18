# ============================
# KODLI KINO BOT (bot.py)
# Til: O'zbekcha
# Texnologiya: Python + aiogram 3.x + SQLite
# ============================

import asyncio
import logging
import os
import sqlite3
import json
import random
from datetime import datetime, timedelta
from typing import List, Tuple

from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery, Message, ChatMemberStatus
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ============================
# 1. KONFIGURATSIYA
# ============================
# Render'da bu qiymatlar Dashboard -> Environment bo'limidan kiritiladi.
# Lokal (Termux) test uchun pastdagi standart qiymatlarni o'zgartirishingiz mumkin.
BOT_TOKEN = os.getenv("8788482418:AAFzkfi_DOpFWBN2lq41RqAsxAZ7X3izRgU", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "8283067497").split(",") if x.strip()]
PORT = int(os.getenv("PORT", 10000))  # Render avtomatik PORT beradi

# ============================
# 2. SQLite BAZA (DB)
# ============================
DB_NAME = "bot_database.db"

def init_db():
    """Barcha kerakli jadvallarni yaratadi (agar mavjud bo'lmasa)"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Foydalanuvchilar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        status TEXT DEFAULT 'oddiy',
        referrer_id INTEGER DEFAULT NULL,
        bonus_balance INTEGER DEFAULT 0,
        last_daily_bonus TEXT DEFAULT NULL,
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
        total_requests INTEGER DEFAULT 0,
        is_banned BOOLEAN DEFAULT 0,
        last_request_time TEXT DEFAULT NULL,
        request_count INTEGER DEFAULT 0
    )''')

    # Kinolar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS movies (
        code TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        file_id TEXT,
        category TEXT,
        is_vip BOOLEAN DEFAULT 0,
        is_premium BOOLEAN DEFAULT 0,
        added_by INTEGER,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        views INTEGER DEFAULT 0,
        rating REAL DEFAULT 0,
        rating_count INTEGER DEFAULT 0
    )''')

    # Kategoriyalar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        name TEXT PRIMARY KEY,
        emoji TEXT
    )''')

    # Referallar jadvali
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        referrer_id INTEGER,
        referred_id INTEGER PRIMARY KEY,
        bonus_given INTEGER DEFAULT 0,
        date TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Loglar jadvali (har bir harakat qayd etiladi)
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        details TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Umumiy sozlamalar (masalan: majburiy obuna kanallari) doimiy saqlanadi
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    conn.commit()
    conn.close()

init_db()

# ============================
# 3. BAZA YORDAMCHI FUNKSIYALARI
# ============================
def db_execute(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Har qanday SQL so'rovni bajaruvchi universal funksiya"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    result = None
    if fetchone:
        result = c.fetchone()
    elif fetchall:
        result = c.fetchall()
    if commit:
        conn.commit()
    conn.close()
    return result

# ------------------------------
# Foydalanuvchi bilan bog'liq funksiyalar
# ------------------------------
def add_user(user_id, username, first_name, last_name, referrer_id=None):
    """Yangi foydalanuvchini ro'yxatdan o'tkazadi yoki mavjudini yangilaydi"""
    user = db_execute("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not user:
        db_execute(
            "INSERT INTO users (user_id, username, first_name, last_name, referrer_id) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, first_name, last_name, referrer_id),
            commit=True
        )
        # Agar referal orqali kelgan bo'lsa - referal beruvchiga bonus beriladi
        if referrer_id:
            db_execute("UPDATE users SET bonus_balance = bonus_balance + 10 WHERE user_id=?", (referrer_id,), commit=True)
            db_execute("INSERT INTO referrals (referrer_id, referred_id, bonus_given) VALUES (?, ?, ?)",
                       (referrer_id, user_id, 10), commit=True)
        log_action(user_id, "register", f"Referrer: {referrer_id}")
    else:
        db_execute("UPDATE users SET username=?, first_name=?, last_name=? WHERE user_id=?",
                   (username, first_name, last_name, user_id), commit=True)

def get_user_status(user_id):
    """Foydalanuvchi statusini qaytaradi: oddiy / vip / premium / admin"""
    row = db_execute("SELECT status FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return row[0] if row else "oddiy"

def set_user_status(user_id, status):
    db_execute("UPDATE users SET status=? WHERE user_id=?", (status, user_id), commit=True)

def get_user_bonus(user_id):
    row = db_execute("SELECT bonus_balance FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return row[0] if row else 0

def update_bonus(user_id, amount):
    db_execute("UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id=?", (amount, user_id), commit=True)

def check_daily_bonus(user_id):
    """Foydalanuvchi bugun kunlik bonus olganmi-yo'qmi tekshiradi"""
    row = db_execute("SELECT last_daily_bonus FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if row and row[0]:
        last = datetime.fromisoformat(row[0])
        if datetime.now() - last < timedelta(days=1):
            return False
    return True

def set_daily_bonus(user_id):
    db_execute("UPDATE users SET last_daily_bonus=? WHERE user_id=?", (datetime.now().isoformat(), user_id), commit=True)

def is_user_banned(user_id):
    row = db_execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,), fetchone=True)
    return bool(row and row[0] == 1)

def set_user_ban(user_id, banned=True):
    db_execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if banned else 0, user_id), commit=True)

def check_spam(user_id):
    """Spam himoyasi: daqiqasiga 10 tadan ortiq so'rov yuborilsa bloklanadi"""
    now = datetime.now()
    row = db_execute("SELECT last_request_time, request_count FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not row:
        return True
    last_time_str, count = row
    if last_time_str:
        last_time = datetime.fromisoformat(last_time_str)
        if now - last_time > timedelta(minutes=1):
            db_execute("UPDATE users SET request_count=1, last_request_time=? WHERE user_id=?", (now.isoformat(), user_id), commit=True)
            return True
        else:
            if count >= 10:
                return False
            db_execute("UPDATE users SET request_count=request_count+1 WHERE user_id=?", (user_id,), commit=True)
            return True
    else:
        db_execute("UPDATE users SET request_count=1, last_request_time=? WHERE user_id=?", (now.isoformat(), user_id), commit=True)
        return True

def log_action(user_id, action, details=""):
    """Har bir muhim harakatni logs jadvaliga yozadi"""
    db_execute("INSERT INTO logs (user_id, action, details) VALUES (?, ?, ?)", (user_id, action, details), commit=True)

# ------------------------------
# Kinolar bilan bog'liq funksiyalar
# ------------------------------
def add_movie(code, title, description, file_id, category, is_vip=False, is_premium=False, added_by=0):
    db_execute(
        "INSERT OR REPLACE INTO movies (code, title, description, file_id, category, is_vip, is_premium, added_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (code, title, description, file_id, category, is_vip, is_premium, added_by),
        commit=True
    )

def get_movie(code):
    return db_execute("SELECT * FROM movies WHERE code=?", (code,), fetchone=True)

def delete_movie(code):
    db_execute("DELETE FROM movies WHERE code=?", (code,), commit=True)

def get_all_movies():
    return db_execute("SELECT code, title FROM movies ORDER BY added_at DESC", fetchall=True)

def get_movies_by_category(category):
    return db_execute("SELECT code, title FROM movies WHERE category=? ORDER BY added_at DESC", (category,), fetchall=True)

def get_categories():
    return db_execute("SELECT name, emoji FROM categories", fetchall=True)

def add_category(name, emoji="📁"):
    db_execute("INSERT OR IGNORE INTO categories (name, emoji) VALUES (?, ?)", (name, emoji), commit=True)

def increment_views(code):
    db_execute("UPDATE movies SET views = views + 1 WHERE code=?", (code,), commit=True)

def update_rating(code, rating):
    """Kino reytingini yangi baho bilan qayta hisoblaydi (o'rtacha qiymat)"""
    movie = get_movie(code)
    if movie:
        old_rating = movie[10] or 0
        old_count = movie[11] or 0
        new_count = old_count + 1
        new_rating = (old_rating * old_count + rating) / new_count
        db_execute("UPDATE movies SET rating=?, rating_count=? WHERE code=?", (new_rating, new_count, code), commit=True)

def search_movies(query):
    return db_execute(
        "SELECT code, title FROM movies WHERE code LIKE ? OR title LIKE ? ORDER BY added_at DESC",
        (f"%{query}%", f"%{query}%"),
        fetchall=True
    )

# ------------------------------
# Majburiy obuna kanallari (DB orqali doimiy saqlanadi)
# ------------------------------
def get_channels():
    row = db_execute("SELECT value FROM settings WHERE key='channels'", fetchone=True)
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    return []

def save_channels(channels):
    db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
               ("channels", json.dumps(channels)), commit=True)

CHANNELS = get_channels()  # Bot ishga tushganda bazadan kanal ro'yxati yuklanadi

# ============================
# 4. OBUNA TEKSHIRISH FUNKSIYASI
# ============================
async def is_subscribed(user_id: int, bot: Bot) -> bool:
    """Foydalanuvchi barcha majburiy kanallarga obuna bo'lganmi tekshiradi"""
    if not CHANNELS:
        return True
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return False
        except Exception:
            return False
    return True

# ============================
# 5. STATE MACHINE (Admin bosqichma-bosqich amallar uchun)
# ============================
class AdminStates(StatesGroup):
    waiting_movie_code = State()
    waiting_movie_title = State()
    waiting_movie_desc = State()
    waiting_movie_file = State()
    waiting_movie_category = State()
    waiting_movie_vip = State()
    waiting_movie_premium = State()
    waiting_delete_code = State()
    waiting_broadcast_text = State()
    waiting_broadcast_confirm = State()
    waiting_give_status_user = State()
    waiting_give_status_type = State()
    waiting_remove_status_user = State()
    waiting_add_channel = State()
    waiting_remove_channel = State()
    waiting_search_query = State()
    waiting_ban_user = State()

# ============================
# 6. KLAVIATURALAR
# ============================
def main_reply_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎬 Kinolar")
    builder.button(text="🔍 Qidirish")
    builder.button(text="🏆 Top kinolar")
    builder.button(text="🆕 Yangi kinolar")
    builder.button(text="🎁 Bonus")
    builder.button(text="👤 Profil")
    builder.button(text="📂 Kategoriyalar")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def main_inline_keyboard(user_id):
    status = get_user_status(user_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 Barcha kinolar", callback_data="movies_menu")
    builder.button(text="🔍 Qidirish", callback_data="search_movie")
    builder.button(text="🏆 Top kinolar", callback_data="top_movies")
    builder.button(text="🆕 Yangi kinolar", callback_data="new_movies")
    builder.button(text="🎁 Bonus", callback_data="bonus_menu")
    builder.button(text="👤 Profil", callback_data="profile")
    builder.button(text="📂 Kategoriyalar", callback_data="categories_menu")
    if status in ["vip", "premium", "admin"]:
        builder.button(text="💎 Maxsus kinolar", callback_data="special_movies")
    if status == "admin":
        builder.button(text="⚙️ Admin panel", callback_data="admin_panel")
    builder.adjust(2)
    return builder.as_markup()

def admin_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Kino qo'shish", callback_data="admin_add_movie")
    builder.button(text="➖ Kino o'chirish", callback_data="admin_delete_movie")
    builder.button(text="📊 Statistika", callback_data="admin_stats")
    builder.button(text="📢 Broadcast", callback_data="admin_broadcast")
    builder.button(text="👑 Status berish", callback_data="admin_give_status")
    builder.button(text="👑 Status olib tashlash", callback_data="admin_remove_status")
    builder.button(text="📡 Kanal sozlash", callback_data="admin_channels")
    builder.button(text="📋 Foydalanuvchilar", callback_data="admin_users")
    builder.button(text="🚫 Ban qilish", callback_data="admin_ban")
    builder.button(text="🔙 Orqaga", callback_data="back_main")
    builder.adjust(2)
    return builder.as_markup()

def movie_list_keyboard(movies: List[Tuple[str, str]], page=0, per_page=5):
    total = len(movies)
    start = page * per_page
    end = min(start + per_page, total)
    builder = InlineKeyboardBuilder()
    for code, title in movies[start:end]:
        builder.button(text=f"{title} ({code})", callback_data=f"movie_{code}")
    builder.adjust(1)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"movies_page_{page-1}"))
    if end < total:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"movies_page_{page+1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
    return builder.as_markup()

def category_list_keyboard(categories):
    builder = InlineKeyboardBuilder()
    for name, emoji in categories:
        builder.button(text=f"{emoji} {name}", callback_data=f"category_{name}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main"))
    return builder.as_markup()

def movie_action_keyboard(code):
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐️ Baholash", callback_data=f"rate_{code}")
    builder.button(text="🔙 Orqaga", callback_data="movies_menu")
    builder.adjust(2)
    return builder.as_markup()

def rate_keyboard(code):
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.button(text=f"{i}⭐️", callback_data=f"rate_val_{code}_{i}")
    builder.adjust(5)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"movie_{code}"))
    return builder.as_markup()

def bonus_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Kunlik bonus", callback_data="daily_bonus")
    builder.button(text="💰 Bonus balansim", callback_data="bonus_balance")
    builder.button(text="🔗 Referal havola", callback_data="referral_link")
    builder.button(text="🔙 Orqaga", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()

def subscribe_keyboard():
    builder = InlineKeyboardBuilder()
    for ch in CHANNELS:
        builder.button(text=f"📢 Obuna bo'lish: {ch}", url=f"https://t.me/{ch.lstrip('@')}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription"))
    return builder.as_markup()

def channel_settings_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Kanal qo'shish", callback_data="admin_add_channel")
    builder.button(text="➖ Kanal o'chirish", callback_data="admin_remove_channel")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_panel"))
    return builder.as_markup()

# ============================
# 7. BOT VA DISPATCHER
# ============================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ============================
# 8. HANDLERLAR
# ============================

# ---------- /start komandasi ----------
@dp.message(Command("start"))
async def start_command(message: Message, command: CommandObject, state: FSMContext):
    """Botni ishga tushiruvchi asosiy komanda. Referal linkni ham qabul qiladi: /start <referrer_id>"""
    user = message.from_user
    args = command.args
    referrer_id = None
    if args and args.isdigit():
        referrer_id = int(args)
        if referrer_id == user.id:
            referrer_id = None
    try:
        add_user(user.id, user.username, user.first_name, user.last_name, referrer_id)
        if user.id in ADMIN_IDS:
            set_user_status(user.id, "admin")
        await state.clear()

        await message.answer(
            f"👋 Assalomu alaykum, {user.first_name}!\n\n"
            f"🎬 Men <b>KODLI KINO BOT</b>man.\n"
            f"🔰 Sizning holatingiz: <b>{get_user_status(user.id)}</b>\n\n"
            f"🎞 Kino ko'rish uchun kino kodini yuboring (masalan: <code>101</code>)\n"
            f"yoki quyidagi menyudan foydalaning 👇",
            reply_markup=main_reply_keyboard(),
            parse_mode="HTML"
        )

        if not await is_subscribed(user.id, bot):
            await message.answer(
                "❗️ Botdan foydalanish uchun avval quyidagi kanal(lar)ga obuna bo'ling:",
                reply_markup=subscribe_keyboard()
            )
        else:
            await message.answer("📋 Asosiy menyu:", reply_markup=main_inline_keyboard(user.id))
    except Exception as e:
        logging.exception(f"Start xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")

# ---------- Obuna tekshirish tugmasi ----------
@dp.callback_query(F.data == "check_subscription")
async def check_subscription_callback(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        if await is_subscribed(user_id, bot):
            await call.message.delete()
            await call.message.answer(
                "✅ Obuna tasdiqlandi! Endi botdan to'liq foydalanishingiz mumkin.",
                reply_markup=main_inline_keyboard(user_id)
            )
        else:
            await call.answer("❌ Siz hali barcha kanallarga obuna bo'lmagansiz!", show_alert=True)
    except Exception as e:
        logging.exception(f"Obuna tekshirish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Reply menyu tugmalari ----------
@dp.message(F.text.in_(["🎬 Kinolar", "🔍 Qidirish", "🏆 Top kinolar", "🆕 Yangi kinolar", "🎁 Bonus", "👤 Profil", "📂 Kategoriyalar"]))
async def reply_menu_handler(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        if is_user_banned(user_id):
            await message.answer("🚫 Siz botdan foydalanish huquqidan mahrum qilingansiz.")
            return
        if not await is_subscribed(user_id, bot):
            await message.answer("❗️ Iltimos, avval kanal(lar)ga obuna bo'ling.", reply_markup=subscribe_keyboard())
            return

        text = message.text
        if text == "🎬 Kinolar":
            movies = get_all_movies()
            if not movies:
                await message.answer("❌ Hozircha kinolar mavjud emas.")
                return
            await message.answer("🎬 Barcha kinolar ro'yxati:", reply_markup=movie_list_keyboard(movies, 0))

        elif text == "🔍 Qidirish":
            await message.answer("🔍 Qidirish uchun kino nomi yoki kodini yozing:")
            await state.set_state(AdminStates.waiting_search_query)

        elif text == "🏆 Top kinolar":
            movies = db_execute("SELECT code, title, rating FROM movies ORDER BY rating DESC LIMIT 10", fetchall=True)
            if not movies:
                await message.answer("📊 Hozircha reyting mavjud emas.")
                return
            ans = "🏆 <b>Eng mashhur kinolar:</b>\n\n"
            for i, (code, title, rating) in enumerate(movies, 1):
                ans += f"{i}. {title} (<code>{code}</code>) — ⭐️ {round(rating or 0, 1)}\n"
            await message.answer(ans, parse_mode="HTML")

        elif text == "🆕 Yangi kinolar":
            movies = db_execute("SELECT code, title FROM movies ORDER BY added_at DESC LIMIT 10", fetchall=True)
            if not movies:
                await message.answer("🆕 Hozircha yangi kinolar yo'q.")
                return
            ans = "🆕 <b>So'nggi qo'shilgan kinolar:</b>\n\n"
            for code, title in movies:
                ans += f"• {title} (<code>{code}</code>)\n"
            await message.answer(ans, parse_mode="HTML")

        elif text == "🎁 Bonus":
            await message.answer("🎁 Bonus menyusi:", reply_markup=bonus_keyboard())

        elif text == "👤 Profil":
            await message.answer(profile_text(user_id), parse_mode="HTML")

        elif text == "📂 Kategoriyalar":
            categories = get_categories()
            if not categories:
                await message.answer("📂 Hozircha kategoriyalar mavjud emas.")
                return
            await message.answer("📂 Kategoriyalar:", reply_markup=category_list_keyboard(categories))
    except Exception as e:
        logging.exception(f"Reply menyu xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")

def profile_text(user_id):
    """Profil matnini shakllantiruvchi yordamchi funksiya"""
    user = db_execute(
        "SELECT username, first_name, status, total_requests, bonus_balance FROM users WHERE user_id=?",
        (user_id,), fetchone=True
    )
    if not user:
        return "❌ Ma'lumot topilmadi."
    username, first_name, status, total_requests, bonus = user
    status_emoji = {"oddiy": "👤", "vip": "👑", "premium": "💎", "admin": "⚙️"}.get(status, "👤")
    return (
        f"👤 <b>Sizning profilingiz:</b>\n\n"
        f"Ism: {first_name}\n"
        f"Username: @{username or 'yo\u02bcq'}\n"
        f"Status: {status_emoji} {status}\n"
        f"So'rovlar soni: {total_requests}\n"
        f"Bonus balans: {bonus} ball"
    )

# ---------- Qidiruv so'rovi ----------
@dp.message(AdminStates.waiting_search_query)
async def handle_search_query(message: Message, state: FSMContext):
    try:
        query = message.text.strip()
        if len(query) < 2:
            await message.answer("❌ Kamida 2 ta belgi kiriting.")
            return
        results = search_movies(query)
        if not results:
            await message.answer("❌ Hech narsa topilmadi.")
        else:
            await message.answer(f"🔍 '{query}' bo'yicha natijalar:", reply_markup=movie_list_keyboard(results, 0))
    except Exception as e:
        logging.exception(f"Qidiruv xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi.")
    await state.clear()

# ---------- Kategoriya bo'yicha kinolar ----------
@dp.callback_query(F.data.startswith("category_"))
async def category_movies(call: CallbackQuery):
    try:
        category = call.data.split("_", 1)[1]
        movies = get_movies_by_category(category)
        if not movies:
            await call.answer("Bu kategoriyada kinolar yo'q", show_alert=True)
            return
        await call.message.edit_text(f"📂 {category} kategoriyasi:", reply_markup=movie_list_keyboard(movies, 0))
    except Exception as e:
        logging.exception(f"Kategoriya xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data == "categories_menu")
async def categories_menu(call: CallbackQuery):
    try:
        categories = get_categories()
        if not categories:
            await call.message.edit_text("📂 Hozircha kategoriyalar mavjud emas.")
            return
        await call.message.edit_text("📂 Kategoriyalar:", reply_markup=category_list_keyboard(categories))
    except Exception as e:
        logging.exception(f"Kategoriya menyusi xatosi: {e}")
    await call.answer()

# ---------- Top va yangi kinolar (inline) ----------
@dp.callback_query(F.data == "top_movies")
async def top_movies_callback(call: CallbackQuery):
    try:
        movies = db_execute("SELECT code, title, rating FROM movies ORDER BY rating DESC LIMIT 10", fetchall=True)
        if not movies:
            await call.answer("Hozircha reyting mavjud emas.", show_alert=True)
            return
        ans = "🏆 <b>Eng mashhur kinolar:</b>\n\n"
        for i, (code, title, rating) in enumerate(movies, 1):
            ans += f"{i}. {title} (<code>{code}</code>) — ⭐️ {round(rating or 0, 1)}\n"
        await call.message.edit_text(ans, parse_mode="HTML", reply_markup=main_inline_keyboard(call.from_user.id))
    except Exception as e:
        logging.exception(f"Top kinolar xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "new_movies")
async def new_movies_callback(call: CallbackQuery):
    try:
        movies = db_execute("SELECT code, title FROM movies ORDER BY added_at DESC LIMIT 10", fetchall=True)
        if not movies:
            await call.answer("Hozircha yangi kinolar yo'q.", show_alert=True)
            return
        ans = "🆕 <b>So'nggi qo'shilgan kinolar:</b>\n\n"
        for code, title in movies:
            ans += f"• {title} (<code>{code}</code>)\n"
        await call.message.edit_text(ans, parse_mode="HTML", reply_markup=main_inline_keyboard(call.from_user.id))
    except Exception as e:
        logging.exception(f"Yangi kinolar xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "search_movie")
async def search_movie_callback(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.edit_text("🔍 Qidirish uchun kino nomi yoki kodini yozing:")
        await state.set_state(AdminStates.waiting_search_query)
    except Exception as e:
        logging.exception(f"Qidiruv callback xatosi: {e}")
    await call.answer()

# ---------- Maxsus kinolar (VIP/Premium) ----------
@dp.callback_query(F.data == "special_movies")
async def special_movies(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        status = get_user_status(user_id)
        if status not in ["vip", "premium", "admin"]:
            await call.answer("💎 Bu bo'lim faqat VIP va PREMIUM foydalanuvchilar uchun!", show_alert=True)
            return
        if status == "vip":
            movies = db_execute("SELECT code, title FROM movies WHERE is_vip=1", fetchall=True)
        elif status == "premium":
            movies = db_execute("SELECT code, title FROM movies WHERE is_premium=1", fetchall=True)
        else:
            movies = db_execute("SELECT code, title FROM movies WHERE is_vip=1 OR is_premium=1", fetchall=True)
        if not movies:
            await call.message.edit_text("💎 Hozircha maxsus kinolar mavjud emas.")
            return
        await call.message.edit_text("💎 Maxsus kinolar:", reply_markup=movie_list_keyboard(movies, 0))
    except Exception as e:
        logging.exception(f"Maxsus kinolar xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

# ---------- Kino ko'rsatish (umumiy funksiya) ----------
async def send_movie(target_message: Message, user_id: int, code: str):
    """Kino kodini tekshirib, ruxsat bo'lsa videoni yuboradi"""
    if is_user_banned(user_id):
        await target_message.answer("🚫 Siz botdan foydalanish huquqidan mahrum qilingansiz.")
        return
    if not await is_subscribed(user_id, bot):
        await target_message.answer("❗️ Iltimos, avval kanal(lar)ga obuna bo'ling.", reply_markup=subscribe_keyboard())
        return
    if not check_spam(user_id):
        await target_message.answer("⏳ Juda ko'p so'rov yubordingiz. Bir daqiqa kuting.")
        return

    movie = get_movie(code)
    if not movie:
        await target_message.answer("❌ Bunday kodli kino topilmadi. Kodni tekshirib qayta yuboring.")
        return

    status = get_user_status(user_id)
    if movie[5] and status not in ["vip", "premium", "admin"]:
        await target_message.answer("👑 Bu kino faqat VIP foydalanuvchilar uchun!")
        return
    if movie[6] and status not in ["premium", "admin"]:
        await target_message.answer("💎 Bu kino faqat PREMIUM foydalanuvchilar uchun!")
        return

    caption = (
        f"🎬 <b>{movie[1]}</b>\n"
        f"📝 {movie[2]}\n"
        f"⭐️ Reyting: {round(movie[10] or 0, 1)} ({movie[11] or 0} ta baho)\n"
        f"👁 Ko'rishlar: {movie[9]}"
    )
    await target_message.answer_video(video=movie[3], caption=caption, parse_mode="HTML", reply_markup=movie_action_keyboard(code))
    increment_views(code)
    db_execute("UPDATE users SET total_requests = total_requests + 1 WHERE user_id=?", (user_id,), commit=True)
    log_action(user_id, "view_movie", f"code={code}")

@dp.callback_query(F.data.startswith("movie_"))
async def movie_detail(call: CallbackQuery):
    try:
        code = call.data.split("_", 1)[1]
        await send_movie(call.message, call.from_user.id, code)
    except Exception as e:
        logging.exception(f"Kino ko'rish xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data == "movies_menu")
async def movies_menu(call: CallbackQuery):
    try:
        movies = get_all_movies()
        if not movies:
            await call.message.edit_text("❌ Hozircha kinolar mavjud emas.")
            return
        await call.message.edit_text("🎬 Barcha kinolar ro'yxati:", reply_markup=movie_list_keyboard(movies, 0))
    except Exception as e:
        logging.exception(f"Kinolar menyusi xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data.startswith("movies_page_"))
async def movies_page(call: CallbackQuery):
    try:
        page = int(call.data.split("_")[-1])
        movies = get_all_movies()
        await call.message.edit_reply_markup(reply_markup=movie_list_keyboard(movies, page))
    except Exception as e:
        logging.exception(f"Sahifalash xatosi: {e}")
    await call.answer()

# ---------- Baholash ----------
@dp.callback_query(F.data.startswith("rate_val_"))
async def rate_value(call: CallbackQuery):
    try:
        _, _, code, rating = call.data.split("_")
        rating = int(rating)
        update_rating(code, rating)
        await call.message.edit_text(f"✅ Siz {rating}⭐️ baho berdingiz. Rahmat!")
    except Exception as e:
        logging.exception(f"Baholash qiymati xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("rate_"))
async def rate_movie(call: CallbackQuery):
    try:
        code = call.data.split("_", 1)[1]
        await call.message.edit_text(f"⭐️ \"{code}\" kodli kinoga baho bering:", reply_markup=rate_keyboard(code))
    except Exception as e:
        logging.exception(f"Baholash boshlash xatosi: {e}")
    await call.answer()

# ---------- Bonus tizimi ----------
@dp.callback_query(F.data == "bonus_menu")
async def bonus_menu(call: CallbackQuery):
    try:
        await call.message.edit_text("🎁 Bonus menyusi:", reply_markup=bonus_keyboard())
    except Exception as e:
        logging.exception(f"Bonus menyusi xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        if check_daily_bonus(user_id):
            bonus = random.randint(5, 20)
            update_bonus(user_id, bonus)
            set_daily_bonus(user_id)
            await call.message.edit_text(f"✅ Kunlik bonusingiz: +{bonus} ball!\n💰 Jami balans: {get_user_bonus(user_id)} ball")
            log_action(user_id, "daily_bonus", f"bonus={bonus}")
        else:
            await call.answer("❌ Siz bugun bonusni allaqachon olgansiz! Ertaga qayting.", show_alert=True)
    except Exception as e:
        logging.exception(f"Kunlik bonus xatosi: {e}")
        await call.answer("Xatolik yuz berdi.", show_alert=True)
    await call.answer()

@dp.callback_query(F.data == "bonus_balance")
async def bonus_balance(call: CallbackQuery):
    try:
        bal = get_user_bonus(call.from_user.id)
        await call.message.edit_text(f"💰 Sizning bonus balansingiz: {bal} ball.")
    except Exception as e:
        logging.exception(f"Bonus balans xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "referral_link")
async def referral_link(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start={user_id}"
        await call.message.edit_text(
            f"🔗 Sizning shaxsiy referal havolangiz:\n{link}\n\n"
            f"👥 Har bir taklif qilingan foydalanuvchi uchun 10 ball bonus olasiz."
        )
    except Exception as e:
        logging.exception(f"Referal havola xatosi: {e}")
    await call.answer()

# ---------- Profil ----------
@dp.callback_query(F.data == "profile")
async def profile_callback(call: CallbackQuery):
    try:
        await call.message.edit_text(profile_text(call.from_user.id), parse_mode="HTML", reply_markup=main_inline_keyboard(call.from_user.id))
    except Exception as e:
        logging.exception(f"Profil xatosi: {e}")
    await call.answer()

# ---------- Admin panel ----------
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Siz admin emassiz!", show_alert=True)
            return
        await call.message.edit_text("⚙️ Admin panel:", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Admin panel xatosi: {e}")
    await call.answer()

# ---------- Kino qo'shish (bosqichma-bosqich) ----------
@dp.callback_query(F.data == "admin_add_movie")
async def admin_add_movie_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("➕ Yangi kino qo'shish.\n\n1️⃣ Kodni kiriting (masalan: 101):")
        await state.set_state(AdminStates.waiting_movie_code)
    except Exception as e:
        logging.exception(f"Admin qo'shish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_movie_code)
async def admin_add_movie_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        if get_movie(code):
            await message.answer("❌ Bu kod allaqachon mavjud. Boshqa kod kiriting.")
            return
        await state.update_data(code=code)
        await message.answer("2️⃣ Kino nomini kiriting:")
        await state.set_state(AdminStates.waiting_movie_title)
    except Exception as e:
        logging.exception(f"Admin kino kodi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_title)
async def admin_add_movie_title(message: Message, state: FSMContext):
    try:
        await state.update_data(title=message.text.strip())
        await message.answer("3️⃣ Kino tavsifini kiriting:")
        await state.set_state(AdminStates.waiting_movie_desc)
    except Exception as e:
        logging.exception(f"Admin kino nomi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_desc)
async def admin_add_movie_desc(message: Message, state: FSMContext):
    try:
        await state.update_data(desc=message.text.strip())
        await message.answer("4️⃣ Endi kino faylini (video) yuboring:")
        await state.set_state(AdminStates.waiting_movie_file)
    except Exception as e:
        logging.exception(f"Admin kino tavsifi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_file, F.video)
async def admin_add_movie_file(message: Message, state: FSMContext):
    try:
        await state.update_data(file_id=message.video.file_id)
        await message.answer("5️⃣ Kategoriyasini kiriting (masalan: Jangari):")
        await state.set_state(AdminStates.waiting_movie_category)
    except Exception as e:
        logging.exception(f"Admin kino fayl xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_file)
async def admin_add_movie_file_invalid(message: Message):
    """Agar admin video o'rniga boshqa narsa yuborsa"""
    await message.answer("❗️ Iltimos, video fayl yuboring.")

@dp.message(AdminStates.waiting_movie_category)
async def admin_add_movie_category(message: Message, state: FSMContext):
    try:
        category = message.text.strip()
        add_category(category, "📁")
        await state.update_data(category=category)
        await message.answer("6️⃣ VIP kinomi? (ha / yo'q):")
        await state.set_state(AdminStates.waiting_movie_vip)
    except Exception as e:
        logging.exception(f"Admin kategoriya xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_vip)
async def admin_add_movie_vip(message: Message, state: FSMContext):
    try:
        vip = message.text.lower() in ["ha", "yes", "1", "true"]
        await state.update_data(vip=vip)
        await message.answer("7️⃣ PREMIUM kinomi? (ha / yo'q):")
        await state.set_state(AdminStates.waiting_movie_premium)
    except Exception as e:
        logging.exception(f"Admin VIP xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_movie_premium)
async def admin_add_movie_premium(message: Message, state: FSMContext):
    try:
        premium = message.text.lower() in ["ha", "yes", "1", "true"]
        data = await state.get_data()
        add_movie(
            data["code"], data["title"], data["desc"], data["file_id"],
            data["category"], data["vip"], premium, message.from_user.id
        )
        await message.answer(
            f"✅ Kino muvaffaqiyatli qo'shildi!\n\n"
            f"🔑 Kod: {data['code']}\n"
            f"🎬 Nomi: {data['title']}\n"
            f"📂 Kategoriya: {data['category']}\n"
            f"👑 VIP: {'Ha' if data['vip'] else 'Yo\u02bcq'}\n"
            f"💎 PREMIUM: {'Ha' if premium else 'Yo\u02bcq'}"
        )
        log_action(message.from_user.id, "add_movie", f"code={data['code']}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin kino qo'shish yakunlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Kino o'chirish ----------
@dp.callback_query(F.data == "admin_delete_movie")
async def admin_delete_movie_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("➖ O'chirish uchun kino kodini kiriting:")
        await state.set_state(AdminStates.waiting_delete_code)
    except Exception as e:
        logging.exception(f"Admin o'chirish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_delete_code)
async def admin_delete_movie_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        if not get_movie(code):
            await message.answer("❌ Bunday kodli kino topilmadi.")
            return
        delete_movie(code)
        await message.answer(f"✅ Kino o'chirildi: {code}")
        log_action(message.from_user.id, "delete_movie", f"code={code}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin o'chirish xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

# ---------- Statistika ----------
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        total_users = db_execute("SELECT COUNT(*) FROM users", fetchone=True)[0]
        total_movies = db_execute("SELECT COUNT(*) FROM movies", fetchone=True)[0]
        total_views = db_execute("SELECT SUM(views) FROM movies", fetchone=True)[0] or 0
        vip_count = db_execute("SELECT COUNT(*) FROM users WHERE status='vip'", fetchone=True)[0]
        premium_count = db_execute("SELECT COUNT(*) FROM users WHERE status='premium'", fetchone=True)[0]
        banned_count = db_execute("SELECT COUNT(*) FROM users WHERE is_banned=1", fetchone=True)[0]
        text = (
            f"📊 <b>Bot statistikasi:</b>\n\n"
            f"👥 Foydalanuvchilar: {total_users}\n"
            f"🚫 Bloklangan: {banned_count}\n"
            f"🎬 Kinolar: {total_movies}\n"
            f"👁 Ko'rishlar: {total_views}\n"
            f"👑 VIP: {vip_count}\n"
            f"💎 PREMIUM: {premium_count}"
        )
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Admin statistika xatosi: {e}")
    await call.answer()

# ---------- Broadcast (hammaga xabar) ----------
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("📢 Broadcast xabar matnini yozing:")
        await state.set_state(AdminStates.waiting_broadcast_text)
    except Exception as e:
        logging.exception(f"Admin broadcast boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_broadcast_text)
async def admin_broadcast_text(message: Message, state: FSMContext):
    try:
        if not message.text:
            await message.answer("❗️ Iltimos, matn ko'rinishida xabar yuboring.")
            return
        await state.update_data(broadcast_text=message.text)
        await message.answer(f"📢 Quyidagi xabar yuborilsinmi?\n\n{message.text}\n\n(ha / yo'q)")
        await state.set_state(AdminStates.waiting_broadcast_confirm)
    except Exception as e:
        logging.exception(f"Admin broadcast matn xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_broadcast_confirm)
async def admin_broadcast_confirm(message: Message, state: FSMContext):
    try:
        if message.text.lower() not in ["ha", "yes"]:
            await message.answer("❌ Bekor qilindi.")
            await state.clear()
            return
        data = await state.get_data()
        text = data["broadcast_text"]
        users = db_execute("SELECT user_id FROM users WHERE is_banned=0", fetchall=True)
        count = 0
        for (user_id,) in users:
            try:
                await bot.send_message(user_id, text)
                count += 1
            except Exception:
                pass
        await message.answer(f"✅ Xabar {count} ta foydalanuvchiga yuborildi.")
        log_action(message.from_user.id, "broadcast", f"sent to {count}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin broadcast tasdiqlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- VIP/PREMIUM status berish ----------
@dp.callback_query(F.data == "admin_give_status")
async def admin_give_status_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("👑 Status berish.\nFoydalanuvchi ID sini kiriting:")
        await state.set_state(AdminStates.waiting_give_status_user)
    except Exception as e:
        logging.exception(f"Admin status berish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_give_status_user)
async def admin_give_status_user(message: Message, state: FSMContext):
    try:
        try:
            user_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Noto'g'ri ID. Faqat raqam kiriting.")
            return
        if not db_execute("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True):
            await message.answer("❌ Bunday foydalanuvchi topilmadi.")
            return
        await state.update_data(give_user_id=user_id)
        await message.answer("Qaysi status berilsin? (vip / premium / admin):")
        await state.set_state(AdminStates.waiting_give_status_type)
    except Exception as e:
        logging.exception(f"Admin status berish ID xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")

@dp.message(AdminStates.waiting_give_status_type)
async def admin_give_status_type(message: Message, state: FSMContext):
    try:
        status = message.text.lower().strip()
        if status not in ["vip", "premium", "admin"]:
            await message.answer("❌ Noto'g'ri status. Faqat vip, premium yoki admin kiriting.")
            return
        data = await state.get_data()
        user_id = data["give_user_id"]
        set_user_status(user_id, status)
        await message.answer(f"✅ Foydalanuvchi {user_id} ga {status} statusi berildi.")
        log_action(message.from_user.id, "give_status", f"user={user_id} status={status}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin status berish turi xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Status olib tashlash ----------
@dp.callback_query(F.data == "admin_remove_status")
async def admin_remove_status_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("👑 Status olib tashlash.\nFoydalanuvchi ID sini kiriting:")
        await state.set_state(AdminStates.waiting_remove_status_user)
    except Exception as e:
        logging.exception(f"Admin status olib tashlash boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_remove_status_user)
async def admin_remove_status_user(message: Message, state: FSMContext):
    try:
        try:
            user_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Noto'g'ri ID.")
            return
        if not db_execute("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True):
            await message.answer("❌ Foydalanuvchi topilmadi.")
            return
        set_user_status(user_id, "oddiy")
        await message.answer(f"✅ Foydalanuvchi {user_id} statusi 'oddiy' ga o'zgartirildi.")
        log_action(message.from_user.id, "remove_status", f"user={user_id}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin status olib tashlash xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Kanal sozlash ----------
@dp.callback_query(F.data == "admin_channels")
async def admin_channels(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        text = "📡 Hozirgi majburiy kanallar:\n" + "\n".join(CHANNELS) if CHANNELS else "📡 Hozircha kanal biriktirilmagan."
        await call.message.edit_text(text, reply_markup=channel_settings_keyboard())
    except Exception as e:
        logging.exception(f"Admin kanal sozlash xatosi: {e}")
    await call.answer()

@dp.callback_query(F.data == "admin_add_channel")
async def admin_add_channel_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("➕ Yangi kanal username'ini kiriting (masalan: @my_channel):")
        await state.set_state(AdminStates.waiting_add_channel)
    except Exception as e:
        logging.exception(f"Admin kanal qo'shish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_add_channel)
async def admin_add_channel(message: Message, state: FSMContext):
    try:
        channel = message.text.strip()
        if not channel.startswith("@"):
            channel = "@" + channel
        if channel in CHANNELS:
            await message.answer("❌ Bu kanal allaqachon ro'yxatda mavjud.")
            return
        CHANNELS.append(channel)
        save_channels(CHANNELS)
        await message.answer(f"✅ Kanal qo'shildi: {channel}")
        log_action(message.from_user.id, "add_channel", channel)
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin kanal qo'shish xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

@dp.callback_query(F.data == "admin_remove_channel")
async def admin_remove_channel_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("➖ O'chirish uchun kanal username'ini kiriting (masalan: @my_channel):")
        await state.set_state(AdminStates.waiting_remove_channel)
    except Exception as e:
        logging.exception(f"Admin kanal o'chirish boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_remove_channel)
async def admin_remove_channel(message: Message, state: FSMContext):
    try:
        channel = message.text.strip()
        if not channel.startswith("@"):
            channel = "@" + channel
        if channel not in CHANNELS:
            await message.answer("❌ Bunday kanal ro'yxatda yo'q.")
            return
        CHANNELS.remove(channel)
        save_channels(CHANNELS)
        await message.answer(f"✅ Kanal o'chirildi: {channel}")
        log_action(message.from_user.id, "remove_channel", channel)
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin kanal o'chirish xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Foydalanuvchilar ro'yxati ----------
@dp.callback_query(F.data == "admin_users")
async def admin_users(call: CallbackQuery):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        users = db_execute("SELECT user_id, username, first_name, status, is_banned FROM users ORDER BY registered_at DESC LIMIT 30", fetchall=True)
        if not users:
            await call.message.edit_text("📋 Foydalanuvchilar yo'q.", reply_markup=admin_panel_keyboard())
            return
        text = "📋 <b>Foydalanuvchilar (oxirgi 30 tasi):</b>\n\n"
        for u in users:
            ban_icon = "🚫" if u[4] else "✅"
            text += f"{ban_icon} ID: <code>{u[0]}</code> — @{u[1] or 'no_username'} — {u[3]}\n"
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception as e:
        logging.exception(f"Admin foydalanuvchilar xatosi: {e}")
    await call.answer()

# ---------- Ban qilish ----------
@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(call: CallbackQuery, state: FSMContext):
    try:
        if call.from_user.id not in ADMIN_IDS:
            await call.answer("⛔️ Ruxsat yo'q", show_alert=True)
            return
        await call.message.edit_text("🚫 Ban qilish uchun foydalanuvchi ID sini kiriting:")
        await state.set_state(AdminStates.waiting_ban_user)
    except Exception as e:
        logging.exception(f"Admin ban boshlash xatosi: {e}")
    await call.answer()

@dp.message(AdminStates.waiting_ban_user)
async def admin_ban_user(message: Message, state: FSMContext):
    try:
        try:
            user_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Noto'g'ri ID.")
            return
        if not db_execute("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True):
            await message.answer("❌ Foydalanuvchi topilmadi.")
            return
        set_user_ban(user_id, True)
        await message.answer(f"✅ Foydalanuvchi {user_id} banlandi.")
        log_action(message.from_user.id, "ban_user", f"user={user_id}")
        await state.clear()
    except Exception as e:
        logging.exception(f"Admin ban xatosi: {e}")
        await message.answer("Xatolik yuz berdi.")
        await state.clear()

# ---------- Orqaga qaytish ----------
@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery):
    try:
        await call.message.edit_text("📋 Asosiy menyu:", reply_markup=main_inline_keyboard(call.from_user.id))
    except Exception as e:
        logging.exception(f"Orqaga qaytish xatosi: {e}")
    await call.answer()

# ---------- Foydalanuvchi kino kodini yozganda (asosiy oqim) ----------
@dp.message(F.text)
async def handle_movie_code(message: Message, state: FSMContext):
    try:
        code = message.text.strip()
        await send_movie(message, message.from_user.id, code)
    except Exception as e:
        logging.exception(f"Kino kodi xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")

# ============================
# 9. XATOLIKLARNI GLOBAL USHLASH
# ============================
@dp.errors()
async def errors_handler(update, exception):
    logging.exception(f"Kutilmagan xatolik: {exception}")
    return True

# ============================
# 10. RENDER UCHUN HEALTH-CHECK SERVER
# ============================
async def health_check(request):
    """Render bu manzilga so'rov yuboradi — bot tirikligini bildiradi"""
    return web.Response(text="🤖 KODLI KINO BOT ishlayapti!")

async def start_web_server():
    """Render Web Service uchun majburiy port ochiladi"""
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"🌐 Health-check server {PORT}-portda ishga tushdi")

# ============================
# 11. BOTNI ISHGA TUSHIRISH
# ============================
async def main():
    logging.basicConfig(level=logging.INFO)
    print("🤖 Bot ishga tushmoqda...")
    await start_web_server()
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
