import logging
import asyncio
import random
import os
import threading
import sqlite3
from fastapi import FastAPI
import uvicorn
import warnings

warnings.filterwarnings("ignore")

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime

# ================= FASTAPI =================
web_app = FastAPI()

@web_app.get("/")
def home():
    return {"status": "anime bot ishlayapti"}

def run_web():
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(web_app, host="0.0.0.0", port=port, log_level="error")

# ================= LOGGING =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8699312044:AAFIEA2X9ENCOM9FJlEB8_T3q6NCH3H5E0E")
SUPER_ADMIN_ID = int(os.environ.get("SUPER_ADMIN_ID", "8134296521"))

DB_PATH = os.environ.get("DB_PATH", "/data/anime_bot.db")
if not os.path.exists(os.path.dirname(DB_PATH)):
    DB_PATH = "anime_bot.db"

# ==================== GLOBAL STATE ====================
auto_post_running = False

# ==================== DATABASE ====================

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            name TEXT DEFAULT '',
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- Anime seriyasi (bir yoki ko'p qismli)
        CREATE TABLE IF NOT EXISTS anime (
            code TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            total_parts INTEGER DEFAULT 1,
            genre TEXT DEFAULT 'Anime',
            language TEXT DEFAULT "O'zbek tilida",
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- Har bir qism uchun video
        CREATE TABLE IF NOT EXISTS anime_parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_code TEXT NOT NULL,
            part_number INTEGER NOT NULL,
            file_id TEXT NOT NULL UNIQUE,
            duration INTEGER DEFAULT 0,
            caption TEXT DEFAULT '',
            FOREIGN KEY (anime_code) REFERENCES anime(code)
        );

        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            name TEXT DEFAULT '',
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS required_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            channel_link TEXT NOT NULL,
            channel_title TEXT DEFAULT 'Kanal'
        );

        CREATE TABLE IF NOT EXISTS post_channel (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            channel_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS news_channel (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            channel_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS auto_post_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_running INTEGER DEFAULT 0,
            current_index INTEGER DEFAULT 0
        );
    """)

    conn.commit()
    conn.close()
    logger.info(f"✅ DB initialized: {DB_PATH}")

# --- Users ---
def db_add_user(user_id, username, name):
    try:
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, name) VALUES (?,?,?)",
            (user_id, username or "", name or "")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"db_add_user xato: {e}")

def db_get_all_users():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_user_count():
    conn = get_conn()
    c = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return c

# --- Anime ---
def db_add_anime(code, title, total_parts, genre="Anime", language="O'zbek tilida"):
    try:
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO anime (code, title, total_parts, genre, language) VALUES (?,?,?,?,?)",
            (code, title, total_parts, genre, language)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"db_add_anime xato: {e}")
        return False

def db_get_anime(code):
    conn = get_conn()
    row = conn.execute("SELECT * FROM anime WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None

def db_anime_exists(code):
    return db_get_anime(code) is not None

def db_delete_anime(code):
    conn = get_conn()
    conn.execute("DELETE FROM anime_parts WHERE anime_code=?", (code,))
    conn.execute("DELETE FROM anime WHERE code=?", (code,))
    conn.commit()
    conn.close()

def db_anime_count():
    conn = get_conn()
    c = conn.execute("SELECT COUNT(*) FROM anime").fetchone()[0]
    conn.close()
    return c

def db_get_all_anime():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM anime ORDER BY added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- Anime Parts ---
def db_add_anime_part(anime_code, part_number, file_id, duration=0, caption=""):
    try:
        conn = get_conn()
        existing = conn.execute("SELECT id FROM anime_parts WHERE file_id=?", (file_id,)).fetchone()
        if existing:
            conn.close()
            return False
        conn.execute(
            "INSERT INTO anime_parts (anime_code, part_number, file_id, duration, caption) VALUES (?,?,?,?,?)",
            (anime_code, part_number, file_id, duration, caption)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"db_add_anime_part xato: {e}")
        return False

def db_get_anime_parts(anime_code):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM anime_parts WHERE anime_code=? ORDER BY part_number ASC",
        (anime_code,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_get_part_count(anime_code):
    conn = get_conn()
    c = conn.execute("SELECT COUNT(*) FROM anime_parts WHERE anime_code=?", (anime_code,)).fetchone()[0]
    conn.close()
    return c

def db_file_id_exists_in_parts(file_id):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM anime_parts WHERE file_id=?", (file_id,)).fetchone()
    conn.close()
    return row is not None

# --- Admins ---
def db_add_admin(user_id, name=""):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO admins (user_id, name) VALUES (?,?)", (user_id, name))
    conn.commit()
    conn.close()

def db_remove_admin(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def db_is_admin(user_id):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None

def db_get_all_admins():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM admins").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- Required Channels ---
def db_add_required_channel(channel_id, channel_link, channel_title):
    conn = get_conn()
    conn.execute(
        "INSERT INTO required_channels (channel_id, channel_link, channel_title) VALUES (?,?,?)",
        (channel_id, channel_link, channel_title)
    )
    conn.commit()
    conn.close()

def db_remove_required_channel(ch_id):
    conn = get_conn()
    conn.execute("DELETE FROM required_channels WHERE id=?", (ch_id,))
    conn.commit()
    conn.close()

def db_get_required_channels():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM required_channels").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- Post Channel ---
def db_set_post_channel(channel_id):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO post_channel (id, channel_id) VALUES (1,?)", (channel_id,))
    conn.commit()
    conn.close()

def db_get_post_channel():
    conn = get_conn()
    row = conn.execute("SELECT channel_id FROM post_channel WHERE id=1").fetchone()
    conn.close()
    return row['channel_id'] if row else None

def db_remove_post_channel():
    conn = get_conn()
    conn.execute("DELETE FROM post_channel WHERE id=1")
    conn.commit()
    conn.close()

# --- News Channel ---
def db_set_news_channel(channel_id):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO news_channel (id, channel_id) VALUES (1,?)", (channel_id,))
    conn.commit()
    conn.close()

def db_get_news_channel():
    conn = get_conn()
    row = conn.execute("SELECT channel_id FROM news_channel WHERE id=1").fetchone()
    conn.close()
    return row['channel_id'] if row else None

def db_remove_news_channel():
    conn = get_conn()
    conn.execute("DELETE FROM news_channel WHERE id=1")
    conn.commit()
    conn.close()

# --- Auto Post State ---
def db_get_auto_post_state():
    conn = get_conn()
    row = conn.execute("SELECT * FROM auto_post_state WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {'is_running': 0, 'current_index': 0}

def db_set_auto_post_running(is_running, current_index=0):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO auto_post_state (id, is_running, current_index) VALUES (1,?,?)",
        (int(is_running), current_index)
    )
    conn.commit()
    conn.close()

def db_update_auto_post_index(index):
    conn = get_conn()
    conn.execute("UPDATE auto_post_state SET current_index=? WHERE id=1", (index,))
    conn.commit()
    conn.close()

# ==================== HELPERS ====================

def is_admin(user_id):
    return user_id == SUPER_ADMIN_ID or db_is_admin(user_id)

def is_super_admin(user_id):
    return user_id == SUPER_ADMIN_ID

def format_duration(seconds):
    if not seconds or seconds == 0:
        return "Noma'lum"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h} soat {m} daqiqa"
    elif m > 0:
        return f"{m} daqiqa {s} sekund"
    else:
        return f"{s} sekund"

def generate_anime_caption(title, part_number, total_parts, duration=0, code=""):
    dur_text = format_duration(duration) if duration > 0 else "—"
    if total_parts == 1:
        part_text = "🎬 <b>To'liq film</b>"
    else:
        part_text = f"📺 <b>{part_number}-qism / {total_parts}-qism</b>"

    caption = (
        f"🌸 <b>{title}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{part_text}\n"
        f"⏱ <b>Davomiyligi:</b> {dur_text}\n"
        f"🌟 <b>Janri:</b> Anime\n"
        f"🌐 <b>Til:</b> O'zbek tilida\n"
    )
    if code:
        caption += f"🔑 <b>Kod:</b> <code>{code}</code>\n"
    caption += (
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"📲 <b>Kodlarni olish:</b> @namelessanim"
        f"💾 <b>Bizning botimiz:</b> @@namelessanimbot"
    )
    return caption

# ==================== YANGILIK ====================

STATIC_NEWS_POOL = [
    {
        "title": "\"Demon Slayer: Infinity Castle\" — rasmiy treyler!",
        "body": "Kimetsu no Yaiba filmining rasmiy treylerida Mugen Chekimsehan Qal'a yoyi ko'rsatildi. Film 2025-yil may oyida Yaponiyada premyera qiladi.\n\nJahon bo'ylab kutilayotgan eng katta anime kinofilm!",
        "source": "Ufotable"
    },
    {
        "title": "\"One Piece\" 1100+ epizodga yetdi",
        "body": "Toei Animation'ning mashhur seriali 1100 dan ortiq epizod bilan dunyo rekordi yangiladi. Eichiro Oda manga Final Saga'sida davom etmoqda.",
        "source": "Toei Animation"
    },
    {
        "title": "\"Jujutsu Kaisen\" Season 3 tasdiqlandi",
        "body": "MAPPA studiyasi JJK 3-seasonini rasmiy tasdiqladi. Culling Game yoyi davomi 2025-yil kuzida ekranlarga chiqadi.",
        "source": "MAPPA"
    },
    {
        "title": "\"Attack on Titan\" — eng yaxshi anime deb topildi",
        "body": "MyAnimeList reytingida Attack on Titan barcha zamonlarning eng yaxshi animesi sifatida birinchi o'rinni egalladi. Jami epizodlar: 87.",
        "source": "MyAnimeList"
    },
    {
        "title": "\"Chainsaw Man\" Part 2 — 2025-yil",
        "body": "MAPPA studiyasi Chainsaw Man 2-qismini ishlab chiqmoqda. Fujimoto Tatsuki mangasining Akademiya yoyi animatsion adaptatsiyasi kutilmoqda.",
        "source": "Weekly Shonen Jump"
    },
    {
        "title": "\"Solo Leveling\" Season 2 tasdiqlanmdi",
        "body": "A-1 Pictures studiyasi Solo Leveling animesining 2-sezoni uchun rasmiy e'lon qildi. Sung Jin-Woo sarguzashtlari davom etadi!",
        "source": "Crunchyroll"
    },
    {
        "title": "\"Naruto\" — yangi anime seriyasi e'lon qilindi",
        "body": "Studio Pierrot Boruto: Two Blue Vortex animatsiyasini rasman boshladi. Yangi avlod sarguzashtlari 2025-yilda.",
        "source": "Studio Pierrot"
    },
    {
        "title": "\"Dragon Ball DAIMA\" — yangi seriya",
        "body": "Akira Toriyamaning so'nggi loyihasi Dragon Ball Daima animatsiyasi nihoyat boshlandi. Super Saiyan yangi shakllar bilan qaytdi!",
        "source": "Toei Animation"
    },
]

def get_random_news() -> str:
    news = random.choice(STATIC_NEWS_POOL)
    date_str = datetime.now().strftime("%d.%m.%Y")
    text = (
        f"🌸 <b>{news['title']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{news['body']}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"📰 <i>Manba: {news['source']}</i>\n"
        f"📅 <i>{date_str}</i>\n\n"
        f"🎌 Yangi animeler uchun: @anime_uzb_bot"
    )
    return text

# ==================== ADMIN PANEL KEYBOARD ====================

def get_admin_panel_keyboard(user_id):
    is_super = is_super_admin(user_id)

    keyboard = [
        [
            InlineKeyboardButton("➕ Anime Qo'shish", callback_data="ap:add_anime"),
            InlineKeyboardButton("🗑 Anime O'chirish", callback_data="ap:delete_anime"),
        ],
    ]

    if is_super:
        keyboard.append([
            InlineKeyboardButton("👑 Admin Qo'shish", callback_data="ap:add_admin"),
            InlineKeyboardButton("🚫 Admin O'chirish", callback_data="ap:remove_admin"),
        ])

    keyboard += [
        [InlineKeyboardButton("📢 Obuna Kanal Qo'shish", callback_data="ap:add_channel")],
        [InlineKeyboardButton("❌ Obuna Kanal O'chirish", callback_data="ap:remove_channel")],
        [
            InlineKeyboardButton("📣 Post Kanal", callback_data="ap:set_post_channel"),
            InlineKeyboardButton("📰 Yangilik Kanal", callback_data="ap:set_news_channel"),
        ],
        [
            InlineKeyboardButton("🗑 Post Kanal O'chi...", callback_data="ap:del_post_channel"),
            InlineKeyboardButton("🗑 Yangilik Kanal O'chi...", callback_data="ap:del_news_channel"),
        ],
        [InlineKeyboardButton("📨 Reklama Yuborish", callback_data="ap:broadcast")],
        [InlineKeyboardButton("📰 Hozir Yangilik Yuborish", callback_data="ap:send_news")],
        [InlineKeyboardButton("🚀 Barcha Animeni Joylash (Auto Post)", callback_data="ap:start_autopost")],
        [InlineKeyboardButton("⏹ Auto Postni To'xtatish", callback_data="ap:stop_autopost")],
        [InlineKeyboardButton("📊 Statistika", callback_data="ap:stats")],
    ]

    return InlineKeyboardMarkup(keyboard)

async def send_admin_panel(target, user_id, edit=False):
    anime_count = db_anime_count()
    users_count = db_user_count()
    admins = db_get_all_admins()
    channels = db_get_required_channels()
    post_ch = db_get_post_channel() or "—"
    news_ch = db_get_news_channel() or "—"
    status = "✅ Ishlayapti" if auto_post_running else "🔴 To'xtatilgan"

    text = (
        f"👑 <b>Anime Bot — Admin Panel</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count}</b>\n"
        f"🎌 Jami animeler: <b>{anime_count}</b>\n"
        f"👮 Adminlar: <b>{len(admins)}</b>\n"
        f"📢 Obuna kanallari: <b>{len(channels)}</b>\n"
        f"📣 Post kanal: <code>{post_ch}</code>\n"
        f"📰 Yangilik kanali: <code>{news_ch}</code>\n"
        f"🤖 Auto post: {status}\n"
    )

    keyboard = get_admin_panel_keyboard(user_id)

    try:
        if edit and hasattr(target, 'edit_message_text'):
            await target.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        elif hasattr(target, 'reply_text'):
            await target.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        elif hasattr(target, 'message'):
            await target.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"send_admin_panel xato: {e}")

# ==================== SUBSCRIPTION CHECK ====================

async def check_subscriptions(bot, user_id):
    channels = db_get_required_channels()
    not_subscribed = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch['channel_id'], user_id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(ch)
        except Exception:
            not_subscribed.append(ch)
    return not_subscribed

async def send_subscription_message(update_or_query, context, not_subscribed, pending_code=None):
    text = (
        "⚠️ <b>Animeni olish uchun quyidagi kanallarga obuna bo'ling!</b>\n\n"
        "📌 Obuna bo'lgach, <b>✅ Tekshirish</b> tugmasini bosing.\n"
    )
    keyboard = []
    for ch in not_subscribed:
        keyboard.append([InlineKeyboardButton(
            f"📢 {ch['channel_title']}",
            url=ch['channel_link']
        )])
    check_data = f"check_sub:{pending_code}" if pending_code else "check_sub:none"
    keyboard.append([InlineKeyboardButton("✅ Obuna bo'ldim — Tekshirish", callback_data=check_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        elif hasattr(update_or_query, 'callback_query') and update_or_query.callback_query:
            await update_or_query.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"send_subscription_message xato: {e}")

async def send_anime_to_user(bot, chat_id, anime_code):
    """Anime barcha qismlarini foydalanuvchiga yuborish"""
    anime = db_get_anime(anime_code)
    if not anime:
        try:
            await bot.send_message(chat_id=chat_id, text="❌ Anime topilmadi.")
        except Exception:
            pass
        return

    parts = db_get_anime_parts(anime_code)
    if not parts:
        try:
            await bot.send_message(chat_id=chat_id, text="❌ Anime qismlari topilmadi.")
        except Exception:
            pass
        return

    total = anime['total_parts']
    title = anime['title']

    # Kirish xabari
    intro = (
        f"🌸 <b>{title}</b>\n\n"
        f"📺 Jami qismlar: <b>{len(parts)}</b>\n"
        f"🌐 Til: O'zbek tilida\n\n"
        f"⬇️ Qismlar yuborilmoqda..."
    )
    try:
        await bot.send_message(chat_id=chat_id, text=intro, parse_mode="HTML")
    except Exception:
        pass

    for part in parts:
        caption = generate_anime_caption(
            title,
            part['part_number'],
            total,
            part['duration'],
            anime_code
        )
        try:
            await bot.send_video(
                chat_id=chat_id,
                video=part['file_id'],
                caption=caption,
                parse_mode="HTML"
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Qism yuborishda xato: {e}")
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ {part['part_number']}-qismni yuborishda xato yuz berdi."
                )
            except Exception:
                pass

# ==================== POST KANALGA ====================

async def post_anime_to_channel(context, anime_code, title, total_parts):
    channel_id = db_get_post_channel()
    if not channel_id:
        return
    try:
        bot_me = await context.bot.get_me()
        bot_username = bot_me.username

        if total_parts == 1:
            parts_text = "🎬 To'liq film"
        else:
            parts_text = f"📺 {total_parts} qismli"

        full_text = (
            f"🌸 <b>{title}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{parts_text}\n"
            f"🌐 <b>Til:</b> O'zbek tilida\n"
            f"🔑 <b>Kod:</b> <code>{anime_code}</code>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"👇 Animeni olish uchun pastdagi tugmani bosing!"
        )

        keyboard = [[InlineKeyboardButton(
            f"🎌 {title} — Olish",
            url=f"https://t.me/{bot_username}?start={anime_code}"
        )]]

        await context.bot.send_message(
            chat_id=channel_id,
            text=full_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Kanalga yuborishda xato: {e}")

# ==================== AUTO POST (har 1 daqiqada) ====================

async def auto_post_loop(bot):
    global auto_post_running

    channel_id = db_get_post_channel() or db_get_news_channel()
    if not channel_id:
        logger.warning("Auto post: kanal topilmadi!")
        auto_post_running = False
        return

    logger.info(f"🚀 Auto post boshlandi → {channel_id}")

    try:
        bot_me = await bot.get_me()
        bot_username = bot_me.username
    except Exception as e:
        logger.error(f"Bot ma'lumoti olinmadi: {e}")
        auto_post_running = False
        return

    state = db_get_auto_post_state()
    current_index = state.get('current_index', 0)

    while auto_post_running:
        try:
            all_anime = db_get_all_anime()
            if not all_anime:
                await asyncio.sleep(60)
                continue

            if current_index >= len(all_anime):
                current_index = 0
                db_update_auto_post_index(0)
                logger.info("Auto post: boshidan boshlandi")

            anime = all_anime[current_index]
            code = anime['code']
            title = anime['title']
            total_parts = anime['total_parts']

            if total_parts == 1:
                parts_text = "🎬 To'liq film"
            else:
                parts_text = f"📺 {total_parts} qismli"

            text = (
                f"🌸 <b>{title}</b>\n\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{parts_text}\n"
                f"🌐 <b>Til:</b> O'zbek tilida\n"
                f"🔑 <b>Kod:</b> <code>{code}</code>\n"
                f"━━━━━━━━━━━━━━━━━\n\n"
                f"👇 Animeni olish uchun pastdagi tugmani bosing!"
            )

            keyboard = [[InlineKeyboardButton(
                "🎌 Animeni olish",
                url=f"https://t.me/{bot_username}?start={code}"
            )]]

            await bot.send_message(
                chat_id=channel_id,
                text=text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            current_index += 1
            db_update_auto_post_index(current_index)

        except Exception as e:
            logger.error(f"Auto post loop xato: {e}")

        await asyncio.sleep(60)

# ==================== CMD HANDLERS ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_add_user(user.id, user.username or "", user.full_name or "")

    if context.args:
        code = context.args[0].strip()
        anime = db_get_anime(code)
        if anime:
            not_subscribed = await check_subscriptions(context.bot, user.id)
            if not_subscribed:
                await send_subscription_message(update, context, not_subscribed, pending_code=code)
                return
            await send_anime_to_user(context.bot, update.effective_chat.id, code)
            return
        else:
            await update.message.reply_text("❌ Bunday anime topilmadi.")
            return

    if is_admin(user.id):
        await send_admin_panel(update.message, user.id)
    else:
        await update.message.reply_text(
            "👋 <b>Salom! Anime botiga xush kelibsiz!</b>\n\n"
            "🎌 Anime kodini yuboring va barcha qismlarni oling!\n\n"
            "📲 Kanal: @anime_uzb_bot",
            parse_mode="HTML"
        )

async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ruxsat yo'q.")
        return
    await send_admin_panel(update.message, update.effective_user.id)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ruxsat yo'q.")
        return
    users = db_user_count()
    animes = db_anime_count()
    admins = len(db_get_all_admins())
    await update.message.reply_text(
        f"📊 <b>Statistika:</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"🎌 Jami animeler: <b>{animes}</b>\n"
        f"👮 Adminlar: <b>{admins}</b>",
        parse_mode="HTML"
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('pending_action', None)
    context.user_data.pop('pending_data', None)
    context.user_data.pop('pending_step', None)
    await update.message.reply_text("❌ Bekor qilindi.")
    if is_admin(update.effective_user.id):
        await send_admin_panel(update.message, update.effective_user.id)

# ==================== ADMIN PANEL CALLBACK HANDLER ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_post_running

    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # Subscription check
    if data.startswith("check_sub:"):
        code = data.split(":", 1)[1]
        not_subscribed = await check_subscriptions(context.bot, user_id)
        if not_subscribed:
            await query.message.reply_text("❌ Hali ham barcha kanallarga obuna bo'lmadingiz!")
            return

        if code and code != "none":
            anime = db_get_anime(code)
            if anime:
                await send_anime_to_user(context.bot, query.message.chat_id, code)
            else:
                await query.message.reply_text("❌ Anime topilmadi.")
        else:
            await query.message.reply_text("✅ Obuna tasdiqlandi! Endi anime kodini yuboring.")
        return

    if not data.startswith("ap:"):
        return

    if not is_admin(user_id):
        await query.answer("❌ Ruxsat yo'q!", show_alert=True)
        return

    action = data[3:]

    if action == "stats":
        await send_admin_panel(query, user_id, edit=True)
        return

    if action == "send_news":
        news_channel = db_get_news_channel() or db_get_post_channel()
        if not news_channel:
            await query.answer("❌ Kanal o'rnatilmagan!", show_alert=True)
            return
        try:
            news_text = get_random_news()
            await context.bot.send_message(chat_id=news_channel, text=news_text, parse_mode="HTML")
            await query.answer("✅ Yangilik yuborildi!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ Xato: {e}", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    if action == "start_autopost":
        if auto_post_running:
            await query.answer("⚠️ Auto post allaqachon ishlayapti!", show_alert=True)
        else:
            channel_id = db_get_post_channel() or db_get_news_channel()
            if not channel_id:
                await query.answer("❌ Avval post kanal o'rnating!", show_alert=True)
                return
            auto_post_running = True
            db_set_auto_post_running(True)
            asyncio.create_task(auto_post_loop(context.bot))
            await query.answer("✅ Auto post yoqildi! Har 1 daqiqada 1 anime joylashadi.", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    if action == "stop_autopost":
        auto_post_running = False
        db_set_auto_post_running(False)
        await query.answer("🛑 Auto post to'xtatildi!", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    if action == "del_post_channel":
        db_remove_post_channel()
        await query.answer("✅ Post kanal o'chirildi!", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    if action == "del_news_channel":
        db_remove_news_channel()
        await query.answer("✅ Yangilik kanali o'chirildi!", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    if action == "remove_channel":
        channels = db_get_required_channels()
        if not channels:
            await query.answer("❌ Hech qanday kanal yo'q!", show_alert=True)
            await send_admin_panel(query, user_id, edit=True)
            return
        keyboard = []
        for ch in channels:
            keyboard.append([InlineKeyboardButton(
                f"❌ {ch['channel_title']} ({ch['channel_id']})",
                callback_data=f"ap:rm_ch:{ch['id']}"
            )])
        keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="ap:stats")])
        try:
            await query.edit_message_text(
                "Qaysi kanalni o'chirish kerak?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass
        return

    if action.startswith("rm_ch:"):
        ch_id = int(action.split(":")[1])
        db_remove_required_channel(ch_id)
        await query.answer("✅ Kanal o'chirildi!", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    action_prompts = {
        "add_anime": (
            "🎌 <b>Anime qo'shish</b>\n\n"
            "1️⃣ Anime kodini kiriting (masalan: naruto, aot, op):\n\n"
            "/cancel — bekor qilish"
        ),
        "delete_anime": "🗑 O'chirmoqchi bo'lgan anime kodini kiriting:\n\n/cancel — bekor qilish",
        "add_admin": "👑 Yangi admin Telegram ID sini kiriting:\n\n/cancel — bekor qilish",
        "remove_admin": "🚫 O'chirmoqchi bo'lgan admin ID sini kiriting:\n\n/cancel — bekor qilish",
        "add_channel": "📢 Kanal invite linkini kiriting\n(masalan: https://t.me/kanalim):\n\n/cancel — bekor qilish",
        "set_post_channel": "📣 Post kanal IDsini kiriting\n(masalan: -1001234567890):\n\n/cancel — bekor qilish",
        "set_news_channel": "📰 Yangilik kanal IDsini kiriting:\n\n/cancel — bekor qilish",
        "broadcast": "📨 Tarqatmoqchi bo'lgan xabarni kiriting:\n\n/cancel — bekor qilish",
    }

    if action in action_prompts:
        context.user_data['pending_action'] = action
        context.user_data['pending_data'] = {}
        context.user_data['pending_step'] = 0
        try:
            await query.message.reply_text(action_prompts[action], parse_mode="HTML")
        except Exception as e:
            logger.error(f"Action prompt xato: {e}")
        return

# ==================== PENDING ACTION HANDLER ====================

async def pending_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    text = update.message.text or ""

    if text.startswith("/"):
        return

    action = context.user_data.get('pending_action')
    if not action:
        await handle_anime_code(update, context)
        return

    step = context.user_data.get('pending_step', 0)
    data = context.user_data.get('pending_data', {})

    # ---- ADD ANIME ----
    if action == "add_anime":
        if step == 0:
            # Kod
            code = text.strip().lower().replace(" ", "_")
            if db_anime_exists(code):
                await update.message.reply_text(
                    f"⚠️ <code>{code}</code> kodi allaqachon mavjud!\n"
                    f"Boshqa kod kiriting yoki /cancel bosing.",
                    parse_mode="HTML"
                )
                return
            data['code'] = code
            context.user_data['pending_data'] = data
            context.user_data['pending_step'] = 1
            await update.message.reply_text(
                f"✅ Kod: <code>{code}</code>\n\n"
                f"2️⃣ Anime nomini kiriting (masalan: Naruto Shippuden):",
                parse_mode="HTML"
            )
        elif step == 1:
            # Nom
            data['title'] = text.strip()
            context.user_data['pending_data'] = data
            context.user_data['pending_step'] = 2
            await update.message.reply_text(
                f"✅ Nom: <b>{data['title']}</b>\n\n"
                f"3️⃣ Nechta qismdan iborat? (raqam kiriting, masalan: 1, 12, 24):",
                parse_mode="HTML"
            )
        elif step == 2:
            # Qismlar soni
            try:
                total_parts = int(text.strip())
                if total_parts < 1:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Noto'g'ri raqam. Musbat son kiriting:")
                return

            data['total_parts'] = total_parts
            data['current_part'] = 1
            context.user_data['pending_data'] = data
            context.user_data['pending_step'] = 3

            # Anime yozuvini bazaga qo'shish
            db_add_anime(data['code'], data['title'], total_parts)

            await update.message.reply_text(
                f"✅ Anime yaratildi!\n\n"
                f"🔑 Kod: <code>{data['code']}</code>\n"
                f"🎌 Nom: <b>{data['title']}</b>\n"
                f"📺 Qismlar: <b>{total_parts}</b>\n\n"
                f"4️⃣ Endi <b>1-qism</b> videosini yuboring:",
                parse_mode="HTML"
            )
        return

    # ---- DELETE ANIME ----
    if action == "delete_anime":
        code = text.strip()
        if db_anime_exists(code):
            db_delete_anime(code)
            await update.message.reply_text(f"✅ Anime o'chirildi (kod: <code>{code}</code>)", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Bunday anime topilmadi.")
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- ADD ADMIN ----
    if action == "add_admin":
        if not is_super_admin(user.id):
            await update.message.reply_text("❌ Faqat super admin qo'sha oladi!")
            context.user_data.pop('pending_action', None)
            return
        try:
            uid = int(text.strip())
            db_add_admin(uid)
            await update.message.reply_text(f"✅ Admin qo'shildi: <code>{uid}</code>", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID. Raqam kiriting.")
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- REMOVE ADMIN ----
    if action == "remove_admin":
        if not is_super_admin(user.id):
            await update.message.reply_text("❌ Faqat super admin o'chira oladi!")
            context.user_data.pop('pending_action', None)
            return
        try:
            uid = int(text.strip())
            if uid == SUPER_ADMIN_ID:
                await update.message.reply_text("❌ Super adminni o'chirib bo'lmaydi!")
            else:
                db_remove_admin(uid)
                await update.message.reply_text(f"✅ Admin o'chirildi: <code>{uid}</code>", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID.")
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- ADD CHANNEL ----
    if action == "add_channel":
        if step == 0:
            data['link'] = text.strip()
            context.user_data['pending_data'] = data
            context.user_data['pending_step'] = 1
            await update.message.reply_text("Kanal nomini kiriting (masalan: Anime Uzbek):")
        elif step == 1:
            link = data['link']
            title = text.strip()
            if "t.me/" in link:
                username = "@" + link.split("t.me/")[-1].split("/")[0].strip("+")
            else:
                username = link
            db_add_required_channel(username, link, title)
            await update.message.reply_text(
                f"✅ Kanal qo'shildi!\n"
                f"📌 Nom: {title}\n"
                f"🔗 Link: {link}\n"
                f"👤 ID: {username}"
            )
            context.user_data.pop('pending_action', None)
            await send_admin_panel(update.message, user.id)
        return

    # ---- SET POST CHANNEL ----
    if action == "set_post_channel":
        channel_id = text.strip()
        db_set_post_channel(channel_id)
        await update.message.reply_text(
            f"✅ Post kanal o'rnatildi: <code>{channel_id}</code>", parse_mode="HTML"
        )
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- SET NEWS CHANNEL ----
    if action == "set_news_channel":
        channel_id = text.strip()
        db_set_news_channel(channel_id)
        await update.message.reply_text(
            f"✅ Yangilik kanali o'rnatildi: <code>{channel_id}</code>", parse_mode="HTML"
        )
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- BROADCAST ----
    if action == "broadcast":
        users = db_get_all_users()
        sent = 0
        failed = 0
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'],
                    text=text,
                    parse_mode="HTML"
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        await update.message.reply_text(
            f"✅ Reklama yuborildi!\n"
            f"📤 Yuborildi: {sent}\n"
            f"❌ Yuborilmadi: {failed}"
        )
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    await handle_anime_code(update, context)


async def pending_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    action = context.user_data.get('pending_action')

    if action != "add_anime":
        return

    step = context.user_data.get('pending_step', 0)
    if step != 3:
        return

    data = context.user_data.get('pending_data', {})
    msg = update.message

    if not msg.video:
        await update.message.reply_text("❌ Faqat video fayl yuboring!")
        return

    file_id = msg.video.file_id
    duration = msg.video.duration or 0
    anime_code = data.get('code')
    current_part = data.get('current_part', 1)
    total_parts = data.get('total_parts', 1)
    title = data.get('title', 'Nomsiz anime')

    if db_file_id_exists_in_parts(file_id):
        await update.message.reply_text(
            "⚠️ Bu video allaqachon bazada mavjud!\n"
            "Boshqa video yuboring yoki /cancel bosing."
        )
        return

    caption = generate_anime_caption(title, current_part, total_parts, duration, anime_code)
    success = db_add_anime_part(anime_code, current_part, file_id, duration, caption)

    if not success:
        await update.message.reply_text("❌ Qismni qo'shishda xato!")
        return

    if current_part < total_parts:
        # Keyingi qism
        data['current_part'] = current_part + 1
        context.user_data['pending_data'] = data
        await update.message.reply_text(
            f"✅ <b>{current_part}-qism</b> qo'shildi! ({format_duration(duration)})\n\n"
            f"📤 Endi <b>{current_part + 1}-qism</b> videosini yuboring:\n"
            f"({current_part}/{total_parts} tayyor)",
            parse_mode="HTML"
        )
    else:
        # Barcha qismlar tugadi
        context.user_data.pop('pending_action', None)
        context.user_data.pop('pending_data', None)
        context.user_data.pop('pending_step', None)

        await update.message.reply_text(
            f"🎉 <b>Anime to'liq qo'shildi!</b>\n\n"
            f"🔑 Kod: <code>{anime_code}</code>\n"
            f"🎌 Nom: <b>{title}</b>\n"
            f"📺 Qismlar: <b>{total_parts}</b>\n\n"
            f"📣 Kanal postiga joylash uchun /panel",
            parse_mode="HTML"
        )
        await post_anime_to_channel(context, anime_code, title, total_parts)
        await send_admin_panel(update.message, user.id)


async def handle_anime_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    code = update.message.text.strip()

    anime = db_get_anime(code)
    if not anime:
        await update.message.reply_text(
            "❌ Bunday anime topilmadi.\n\n"
            "🎌 Anime kodini to'g'ri yuboring yoki kanal orqali kodni toping.\n"
            "📲 @anime_uzb_bot"
        )
        return

    not_subscribed = await check_subscriptions(context.bot, user.id)
    if not_subscribed:
        await send_subscription_message(update, context, not_subscribed, pending_code=code)
        return

    await send_anime_to_user(context.bot, update.effective_chat.id, code)

# ==================== STARTUP ====================

async def on_startup(app: Application):
    global auto_post_running

    logger.info("🎌 Anime Bot ishga tushdi")
    logger.info(f"📁 DB joyi: {DB_PATH}")
    logger.info(f"👑 Super admin: {SUPER_ADMIN_ID}")

    state = db_get_auto_post_state()
    channel_id = db_get_post_channel() or db_get_news_channel()

    if channel_id and state.get('is_running', 0):
        auto_post_running = True
        db_set_auto_post_running(True)
        logger.info(f"🚀 Auto post qayta yoqildi (index: {state.get('current_index', 0)})")
        asyncio.create_task(auto_post_loop(app.bot))
    else:
        logger.info("ℹ️ Auto post o'chirilgan yoki kanal yo'q")

# ==================== MAIN ====================

def main():
    init_db()

    threading.Thread(target=run_web, daemon=True).start()
    logger.info("🌐 Web server thread boshlandi")

    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    app.add_handler(CallbackQueryHandler(callback_handler))

    # Admin: video yuklash (faqat video, private)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.VIDEO,
        pending_media_handler
    ))

    # Matn xabarlari (faqat private chat)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        pending_message_handler
    ))

    logger.info("▶️ Anime Bot polling boshlandi...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=30
    )

if __name__ == "__main__":
    main()
