import asyncio
import logging
import sqlite3
from datetime import datetime, date, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = {"maxtroid", "serotonin_high"}
PAYMENT_PHONE = "89999998266"
PAYMENT_NAME = "Елена (Т-Банк)"
ADDRESS = "(надо с вами обсудить, исправим)"
CLOTHES_INFO = "(надо с вами обсудить, исправим)"
SHOOT_RECOMMENDATIONS = "(надо с вами обсудить, исправим)"

PRICES = {
    "single": {"name": "Разовое занятие", "price": 4500},
    "four":   {"name": "4 занятия",        "price": 12000},
    "full":   {"name": "Полный пакет",     "price": 20000},
}

# Расписание групп
SCHEDULE = {
    1: [
        {"date": date(2026, 3, 31), "type": "dance",   "time": "20:00-22:00"},
        {"date": date(2026, 4, 2),  "type": "dance",   "time": "20:00-22:00"},
        {"date": date(2026, 4, 4),  "type": "meeting", "time": "20:00-21:00"},
        {"date": date(2026, 4, 7),  "type": "dance",   "time": "20:00-22:00"},
        {"date": date(2026, 4, 9),  "type": "dance",   "time": "20:00-22:00"},
        {"date": date(2026, 4, 11), "type": "shoot",   "time": "19:00-21:00"},
    ],
    2: [
        {"date": date(2026, 4, 14), "type": "dance",   "time": "20:00-22:00"},
        {"date": date(2026, 4, 16), "type": "dance",   "time": "20:00-22:00"},
        {"date": date(2026, 4, 18), "type": "meeting", "time": "20:00-21:00"},
        {"date": date(2026, 4, 21), "type": "dance",   "time": "20:00-22:00"},
        {"date": date(2026, 4, 23), "type": "dance",   "time": "20:00-22:00"},
        {"date": date(2026, 4, 25), "type": "shoot",   "time": "19:00-21:00"},
    ],
    3: [
        {"date": date(2026, 4, 28), "type": "dance", "time": "20:00-22:00"},
        {"date": date(2026, 4, 30), "type": "dance", "time": "20:00-22:00"},
        {"date": date(2026, 5, 5),  "type": "dance", "time": "20:00-22:00"},
        {"date": date(2026, 5, 7),  "type": "dance", "time": "20:00-22:00"},
    ],
}

# Группы доступные для полного пакета
FULL_PACKAGE_GROUPS = {1, 2}

EVENT_EMOJI = {"dance": "💃", "meeting": "🤝", "shoot": "📸"}
EVENT_NAME  = {"dance": "Танцы", "meeting": "Встреча", "shoot": "Съёмка"}

RU_MONTHS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}
RU_MONTHS_SHORT = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр",
    5: "май", 6: "июн", 7: "июл", 8: "авг",
    9: "сен", 10: "окт", 11: "ноя", 12: "дек"
}

def fmt_date(d, short=False):
    """Форматирует дату на русском: '31 марта' или '31 мар'"""
    months = RU_MONTHS_SHORT if short else RU_MONTHS
    return f"{d.day} {months[d.month]}"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# ── База данных ───────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            full_name     TEXT,
            discount      INTEGER DEFAULT 0,
            discount_type TEXT DEFAULT '',
            ref_code      TEXT UNIQUE,
            referred_by   INTEGER,
            paid          INTEGER DEFAULT 0,
            group_id      INTEGER DEFAULT 0,
            tariff        TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS old_students (
            username TEXT PRIMARY KEY
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_payments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            tariff       TEXT,
            group_id     INTEGER,
            amount       INTEGER,
            discount     INTEGER,
            final_amount INTEGER,
            status       TEXT DEFAULT 'pending'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ref_used (
            user_id  INTEGER,
            ref_code TEXT,
            PRIMARY KEY (user_id, ref_code)
        )
    """)
    try:
        c.execute("ALTER TABLE users ADD COLUMN created_at TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # столбец уже есть
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def upsert_user(user_id, username, full_name, discount=0, discount_type="", ref_code=None, referred_by=None):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    now = str(date.today())
    c.execute("""
        INSERT INTO users (user_id, username, full_name, discount, discount_type, ref_code, referred_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name
    """, (user_id, username, full_name, discount, discount_type, ref_code, referred_by, now))
    conn.commit()
    conn.close()

def set_discount(user_id, discount, discount_type):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("UPDATE users SET discount=?, discount_type=? WHERE user_id=?", (discount, discount_type, user_id))
    conn.commit()
    conn.close()

def is_old_student(username):
    if not username:
        return False
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT 1 FROM old_students WHERE username = ?", (username.lower(),))
    row = c.fetchone()
    conn.close()
    return row is not None

def get_user_by_ref(ref_code):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE ref_code = ?", (ref_code,))
    row = c.fetchone()
    conn.close()
    return row

def has_used_ref(user_id, ref_code):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT 1 FROM ref_used WHERE user_id=? AND ref_code=?", (user_id, ref_code))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_ref_used(user_id, ref_code):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO ref_used (user_id, ref_code) VALUES (?,?)", (user_id, ref_code))
    conn.commit()
    conn.close()

def add_pending_payment(user_id, tariff, group_id, amount, discount, final_amount):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("INSERT INTO pending_payments (user_id, tariff, group_id, amount, discount, final_amount) VALUES (?,?,?,?,?,?)",
              (user_id, tariff, group_id, amount, discount, final_amount))
    pid = c.lastrowid
    conn.commit()
    conn.close()
    return pid

def confirm_payment(payment_id):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT user_id, tariff, group_id FROM pending_payments WHERE id=?", (payment_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None, None, None
    user_id, tariff, group_id = row
    c.execute("UPDATE pending_payments SET status='confirmed' WHERE id=?", (payment_id,))
    c.execute("UPDATE users SET paid=1, discount=0, discount_type='', tariff=?, group_id=? WHERE user_id=?",
              (tariff, group_id, user_id))
    conn.commit()
    conn.close()
    return user_id, tariff, group_id

def reject_payment(payment_id):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("UPDATE pending_payments SET status='rejected' WHERE id=?", (payment_id,))
    c.execute("SELECT user_id FROM pending_payments WHERE id=?", (payment_id,))
    row = c.fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else None

def get_all_paid_users():
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT user_id, username, full_name, tariff, group_id FROM users WHERE paid=1")
    rows = c.fetchall()
    conn.close()
    return rows

def get_paid_users_by_group(group_id):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE paid=1 AND group_id=?", (group_id,))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_all_users():
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_users_with_discount():
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT user_id, username, full_name, discount, discount_type FROM users WHERE discount > 0")
    rows = c.fetchall()
    conn.close()
    return rows

def get_stats():
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    today = date.today()
    week_ago = today - timedelta(days=7)

    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM users WHERE paid=1")
    paid = c.fetchone()[0]

    c.execute("SELECT SUM(final_amount) FROM pending_payments WHERE status='confirmed'")
    money = c.fetchone()[0] or 0

    # Конверсия
    conversion = round(paid / total * 100) if total else 0

    # Ожидающие и отклонённые
    c.execute("SELECT COUNT(*) FROM pending_payments WHERE status='pending'")
    pending = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM pending_payments WHERE status='rejected'")
    rejected = c.fetchone()[0]

    # Новые пользователи
    c.execute("SELECT COUNT(*) FROM users WHERE DATE(created_at) = ?", (str(today),))
    new_today = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE DATE(created_at) >= ?", (str(week_ago),))
    new_week = c.fetchone()[0]

    # Выручка по тарифам
    c.execute("SELECT tariff, COUNT(*), SUM(final_amount) FROM pending_payments WHERE status='confirmed' GROUP BY tariff")
    tariff_stats = c.fetchall()

    # Распределение по группам
    c.execute("SELECT group_id, COUNT(*) FROM users WHERE paid=1 GROUP BY group_id")
    group_stats = c.fetchall()

    # Скидки по типам
    c.execute("SELECT discount_type, COUNT(*) FROM users WHERE discount_type != '' GROUP BY discount_type")
    discount_stats = c.fetchall()

    conn.close()
    return {
        "total": total, "paid": paid, "money": money, "conversion": conversion,
        "pending": pending, "rejected": rejected,
        "new_today": new_today, "new_week": new_week,
        "tariff_stats": tariff_stats, "group_stats": group_stats,
        "discount_stats": discount_stats,
    }

# ── FSM ───────────────────────────────────────────────────────────────────────

class Form(StatesGroup):
    waiting_screenshot        = State()
    waiting_repost_screenshot = State()
    choosing_group            = State()
    broadcast_text            = State()
    broadcast_group_text      = State()
    reset_discount_username   = State()
    addold_collecting         = State()

# ── Клавиатуры ────────────────────────────────────────────────────────────────

def main_menu_keyboard(is_admin=False):
    buttons = [
        [InlineKeyboardButton(text="💳 Записаться на курс",        callback_data="enroll")],
        [InlineKeyboardButton(text="📅 Моё расписание",            callback_data="my_schedule")],
        [InlineKeyboardButton(text="🎁 Моя реферальная ссылка",    callback_data="my_ref")],
        [InlineKeyboardButton(text="📸 Скидка за репост",          callback_data="repost")],
        [InlineKeyboardButton(text="❓ Вопросы",                    callback_data="faq")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="⚙️ Админ панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def tariff_keyboard(discount):
    buttons = []
    for key, val in PRICES.items():
        final = int(val["price"] * (1 - discount / 100))
        label = f"{val['name']} — {final:,} ₽" + (f" (скидка {discount}%)" if discount else "")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"tariff_{key}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def group_keyboard(tariff_key):
    buttons = []
    for gid, events in SCHEDULE.items():
        if tariff_key == "full" and gid not in FULL_PACKAGE_GROUPS:
            continue
        dance_dates = [e for e in events if e["type"] == "dance"]
        first = fmt_date(dance_dates[0]["date"], short=True)
        last  = fmt_date(dance_dates[-1]["date"], short=True)
        extra = ""
        if tariff_key == "full" and any(e["type"] != "dance" for e in events):
            extra = " + встреча + съёмка"
        label = f"Группа {gid}: {first} – {last}{extra}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"group_{gid}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="enroll")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список участниц",     callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Статистика",          callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка всем",       callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📢 Рассылка по группе",  callback_data="admin_broadcast_group")],
        [InlineKeyboardButton(text="➕ Добавить старых",      callback_data="admin_addold")],
        [InlineKeyboardButton(text="🗑 Сбросить скидку",     callback_data="admin_resetdiscount")],
        [InlineKeyboardButton(text="◀️ Назад",               callback_data="back_menu")],
    ])

def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
    ])

def menu_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📋 Меню")]],
        resize_keyboard=True,
        persistent=True
    )

def format_schedule(group_id, tariff):
    events = SCHEDULE.get(group_id, [])
    today = date.today()
    lines = []
    for e in events:
        if tariff != "full" and e["type"] != "dance":
            continue
        emoji = EVENT_EMOJI[e["type"]]
        name  = EVENT_NAME[e["type"]]
        d     = fmt_date(e["date"])
        time  = e["time"]
        line  = f"{emoji} {d} — {name}, {time}"
        if e["date"] < today:
            line = f"<s>{line}</s>"
        lines.append(line)
    return "\n".join(lines) if lines else "Расписание пока не добавлено"

# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user      = message.from_user
    username  = user.username or ""
    full_name = user.full_name or ""
    user_id   = user.id
    is_admin  = username in ADMINS

    discount = 0; discount_type = ""; referred_by = None
    args  = message.text.split()
    param = args[1] if len(args) > 1 else ""

    if is_old_student(username):
        discount = 13; discount_type = "old_student"
    elif param.startswith("ref_"):
        ref_owner = get_user_by_ref(param)
        if ref_owner and ref_owner[0] != user_id and not has_used_ref(user_id, param):
            discount = 5; discount_type = "referral"; referred_by = ref_owner[0]
            mark_ref_used(user_id, param)

    upsert_user(user_id, username, full_name, discount, discount_type, f"ref_{user_id}", referred_by)

    discount_text = ""
    if discount_type == "old_student":
        discount_text = "\n\n🎉 <b>Ты из прошлого потока — скидка 13% уже применена!</b>"
    elif discount_type == "referral":
        discount_text = "\n\n🎁 <b>Тебя пригласили — скидка 5% уже применена!</b>"

    await message.answer(
        f"Привет, {full_name}! 👋\n\n"
        f"Добро пожаловать в <b>курс танцев с Лё</b> 💃\n\n"
        f"Здесь можно записаться, узнать цены и задать вопросы."
        f"{discount_text}",
        reply_markup=main_menu_keyboard(is_admin)
    )
    await message.answer("✨", reply_markup=menu_reply_keyboard())

# ── Записаться ────────────────────────────────────────────────────────────────

@dp.message(F.text == "📋 Меню")
async def menu_button(message: Message, state: FSMContext):
    await state.clear()
    username = message.from_user.username or ""
    is_admin = username in ADMINS
    await message.answer("Главное меню 👇", reply_markup=main_menu_keyboard(is_admin))

@dp.callback_query(F.data == "enroll")
async def enroll(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    discount = user[3] if user else 0
    await callback.message.edit_text("Выбери тариф 👇", reply_markup=tariff_keyboard(discount))

def single_date_keyboard():
    """Клавиатура с отдельными датами для разового занятия"""
    buttons = []
    for gid, events in SCHEDULE.items():
        for e in events:
            if e["type"] != "dance":
                continue
            label = f"💃 {fmt_date(e['date'])} (Группа {gid}), {e['time']}"
            buttons.append([InlineKeyboardButton(
                text=label,
                callback_data=f"single_{gid}_{e['date'].strftime('%Y%m%d')}"
            )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="enroll")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(F.data.startswith("tariff_"))
async def choose_tariff(callback: CallbackQuery, state: FSMContext):
    key    = callback.data.replace("tariff_", "")
    tariff = PRICES[key]
    user   = get_user(callback.from_user.id)
    discount = user[3] if user else 0
    final  = int(tariff["price"] * (1 - discount / 100))

    await state.update_data(tariff_key=key, tariff_name=tariff["name"],
                             original=tariff["price"], discount=discount, final=final)
    await state.set_state(Form.choosing_group)

    if key == "single":
        await callback.message.edit_text(
            f"Ты выбрала <b>{tariff['name']}</b>\n\nВыбери дату занятия 👇",
            reply_markup=single_date_keyboard()
        )
    else:
        await callback.message.edit_text(
            f"Ты выбрала <b>{tariff['name']}</b>\n\nТеперь выбери группу 👇",
            reply_markup=group_keyboard(key)
        )

@dp.callback_query(Form.choosing_group, F.data.startswith("single_"))
async def choose_single_date(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")  # single_GID_YYYYMMDD
    gid = int(parts[1])
    date_str = parts[2]
    chosen_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
    data = await state.get_data()
    await state.update_data(group_id=gid)

    # Найдём время этого занятия
    event_time = ""
    for e in SCHEDULE.get(gid, []):
        if e["date"] == chosen_date and e["type"] == "dance":
            event_time = e["time"]
            break

    date_label = f"{fmt_date(chosen_date)} {chosen_date.year}"
    await callback.message.edit_text(
        f"Отлично! Твоё занятие:\n\n"
        f"💃 {date_label}, {event_time}\n\n"
        f"💰 К оплате: <b>{data['final']:,} ₽</b>" + (f" (скидка {data['discount']}%)" if data['discount'] else "") +
        f"\n\n📱 Переведи на номер:\n<code>{PAYMENT_PHONE}</code>\n({PAYMENT_NAME})\n\n"
        f"После оплаты отправь сюда <b>скриншот перевода</b> 📸",
        reply_markup=back_keyboard()
    )
    await state.set_state(Form.waiting_screenshot)

@dp.callback_query(Form.choosing_group, F.data.startswith("group_"))
async def choose_group(callback: CallbackQuery, state: FSMContext):
    group_id = int(callback.data.replace("group_", ""))
    data = await state.get_data()
    await state.update_data(group_id=group_id)

    schedule_text = format_schedule(group_id, data["tariff_key"])

    await callback.message.edit_text(
        f"Отлично! Твоя группа:\n\n{schedule_text}\n\n"
        f"💰 К оплате: <b>{data['final']:,} ₽</b>" + (f" (скидка {data['discount']}%)" if data['discount'] else "") +
        f"\n\n📱 Переведи на номер:\n<code>{PAYMENT_PHONE}</code>\n({PAYMENT_NAME})\n\n"
        f"После оплаты отправь сюда <b>скриншот перевода</b> 📸",
        reply_markup=back_keyboard()
    )
    await state.set_state(Form.waiting_screenshot)

@dp.message(Form.waiting_screenshot, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user
    pid  = add_pending_payment(user.id, data["tariff_key"], data["group_id"],
                                data["original"], data["discount"], data["final"])

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{pid}"),
        InlineKeyboardButton(text="❌ Отклонить",   callback_data=f"reject_{pid}"),
    ]])
    price_line = f"💰 {data['original']:,} ₽"
    if data['discount']:
        price_line += f"\n💸 {data['final']:,} ₽ (скидка {data['discount']}%)"
    caption = (
        f"💳 <b>Новая оплата #{pid}</b>\n\n"
        f"👤 {user.full_name} (@{user.username or 'нет'})\n"
        f"📦 {data['tariff_name']}\n"
        f"👥 Группа {data['group_id']}\n"
        f"{price_line}"
    )
    try:
        await bot.send_photo(chat_id=429779513, photo=message.photo[-1].file_id,
                             caption=caption, reply_markup=kb)
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")

    await message.answer("✅ Скриншот получен! Ожидай подтверждения 🙏")
    await state.clear()

@dp.message(Form.waiting_screenshot)
async def screenshot_wrong(message: Message):
    await message.answer("Пожалуйста, отправь <b>скриншот</b> (фото) перевода 📸")

# ── Подтверждение / отклонение ────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("confirm_"))
async def admin_confirm(callback: CallbackQuery):
    pid = int(callback.data.replace("confirm_", ""))
    user_id, tariff, group_id = confirm_payment(pid)
    if user_id:
        schedule_text = format_schedule(group_id, tariff)
        try:
            await bot.send_message(user_id,
                f"🎉 <b>Оплата подтверждена! Ты в курсе!</b>\n\n"
                f"📍 Адрес: {ADDRESS}\n"
                f"👗 Что надеть: {CLOTHES_INFO}\n\n"
                f"📅 Твоё расписание:\n{schedule_text}\n\n"
                f"До встречи! 💃"
            )
        except Exception as e:
            logging.error(e)
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ <b>Подтверждено</b>")

@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject(callback: CallbackQuery):
    pid     = int(callback.data.replace("reject_", ""))
    user_id = reject_payment(pid)
    if user_id:
        try:
            await bot.send_message(user_id,
                "😔 Оплата не подтверждена. Напиши @maxtroid чтобы разобраться 🙏")
        except Exception as e:
            logging.error(e)
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ <b>Отклонено</b>")

# ── Моё расписание ────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "my_schedule")
async def my_schedule(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    if not user or not user[7]:  # paid
        await callback.message.edit_text(
            "У тебя пока нет активной записи 🙁\n\nЗапишись на курс чтобы увидеть расписание!",
            reply_markup=back_keyboard()
        )
        return
    group_id = user[8]
    tariff   = user[9]
    schedule_text = format_schedule(group_id, tariff)
    await callback.message.edit_text(
        f"📅 <b>Твоё расписание:</b>\n\n{schedule_text}\n\n📍 Адрес: {ADDRESS}",
        reply_markup=back_keyboard()
    )

# ── Реферальная ссылка ────────────────────────────────────────────────────────

@dp.callback_query(F.data == "my_ref")
async def my_ref(callback: CallbackQuery):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{callback.from_user.id}"
    await callback.message.edit_text(
        f"🎁 <b>Твоя реферальная ссылка:</b>\n\n<code>{link}</code>\n\n"
        f"Поделись с подругой — она получит <b>скидку 5%</b> при записи!",
        reply_markup=back_keyboard()
    )

# ── Репост ────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "repost")
async def repost(callback: CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    if user and user[4] in ("old_student", "referral", "repost"):
        await callback.message.edit_text(
            "У тебя уже есть скидка 🎉 Скидки не суммируются.",
            reply_markup=back_keyboard()
        )
        return
    await callback.message.edit_text(
        "📸 <b>Скидка 5% за репост</b>\n\n"
        "1. Сделай репост любого поста из канала Лё к себе в сторис или на стену\n"
        "2. Сделай скриншот и отправь сюда\n\nПолина проверит и скидка активируется ✅",
        reply_markup=back_keyboard()
    )
    await state.set_state(Form.waiting_repost_screenshot)

@dp.message(Form.waiting_repost_screenshot, F.photo)
async def receive_repost(message: Message, state: FSMContext):
    user = message.from_user
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Дать скидку", callback_data=f"repost_ok_{user.id}"),
        InlineKeyboardButton(text="❌ Отклонить",   callback_data=f"repost_no_{user.id}"),
    ]])
    try:
        await bot.send_photo(
            chat_id="@serotonin_high",
            photo=message.photo[-1].file_id,
            caption=f"📸 <b>Репост на проверку</b>\n\n👤 {user.full_name} (@{user.username or 'нет'})\nID: {user.id}",
            reply_markup=kb
        )
    except Exception as e:
        logging.error(e)
    await message.answer("Скриншот отправлен на проверку Полине! 🙏")
    await state.clear()

@dp.callback_query(F.data.startswith("repost_ok_"))
async def repost_ok(callback: CallbackQuery):
    uid = int(callback.data.replace("repost_ok_", ""))
    set_discount(uid, 5, "repost")
    try:
        await bot.send_message(uid, "🎉 Репост подтверждён! Скидка 5% активирована.\n\nНажми /start чтобы выбрать тариф")
    except Exception as e:
        logging.error(e)
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ Скидка выдана")

@dp.callback_query(F.data.startswith("repost_no_"))
async def repost_no(callback: CallbackQuery):
    uid = int(callback.data.replace("repost_no_", ""))
    try:
        await bot.send_message(uid, "😔 Репост не подтверждён. Напиши @serotonin_high если есть вопросы.")
    except Exception as e:
        logging.error(e)
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ Отклонено")

# ── FAQ ───────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "faq")
async def faq(callback: CallbackQuery):
    await callback.message.edit_text(
        "❓ <b>Частые вопросы</b>\n\n"
        "<b>Где проходят занятия?</b>\n"
        f"{ADDRESS}\n\n"
        "<b>Можно перенести занятие?</b>\n"
        "Напиши @serotonin_high заранее\n\n"
        "<b>Для кого курс?</b>\n"
        "Для всех, уровень не важен 💃\n\n"
        "<b>Остались вопросы?</b>\n"
        "Напиши @serotonin_high",
        reply_markup=back_keyboard()
    )

@dp.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery):
    username = callback.from_user.username or ""
    is_admin = username in ADMINS
    await callback.message.edit_text("Главное меню 👇", reply_markup=main_menu_keyboard(is_admin))

# ── Админ панель ──────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if (callback.from_user.username or "") not in ADMINS:
        return
    await callback.message.edit_text("⚙️ <b>Админ панель</b>", reply_markup=admin_keyboard())

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if (callback.from_user.username or "") not in ADMINS:
        return
    s = get_stats()

    # Выручка по тарифам
    tariff_lines = []
    for tariff, count, total_sum in s["tariff_stats"]:
        name = PRICES.get(tariff, {}).get("name", tariff)
        tariff_lines.append(f"  • {name}: {count} чел. — {(total_sum or 0):,} ₽")
    tariff_text = "\n".join(tariff_lines) if tariff_lines else "  нет данных"

    # Группы
    group_lines = []
    for gid, count in sorted(s["group_stats"]):
        group_lines.append(f"  • Группа {gid}: {count} чел.")
    group_text = "\n".join(group_lines) if group_lines else "  нет данных"

    # Скидки
    discount_map = {"old_student": "Старые ученики", "referral": "Рефералы", "repost": "Репосты"}
    discount_lines = []
    for dtype, count in s["discount_stats"]:
        label = discount_map.get(dtype, dtype)
        discount_lines.append(f"  • {label}: {count} чел.")
    discount_text = "\n".join(discount_lines) if discount_lines else "  нет"

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего в боте: {s['total']}\n"
        f"✅ Оплатили: {s['paid']}\n"
        f"📈 Конверсия: {s['conversion']}%\n"
        f"⏳ Ждут подтверждения: {s['pending']}\n"
        f"❌ Отклонено платежей: {s['rejected']}\n\n"
        f"💰 <b>Выручка: {s['money']:,} ₽</b>\n"
        f"По тарифам:\n{tariff_text}\n\n"
        f"👥 <b>По группам:</b>\n{group_text}\n\n"
        f"🎁 <b>Скидки:</b>\n{discount_text}\n\n"
        f"🆕 Новых сегодня: {s['new_today']}\n"
        f"🆕 Новых за неделю: {s['new_week']}"
    )
    await callback.message.edit_text(text, reply_markup=admin_keyboard())

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if (callback.from_user.username or "") not in ADMINS:
        return
    users = get_all_paid_users()
    if not users:
        await callback.message.edit_text("Пока никто не оплатил", reply_markup=admin_keyboard())
        return
    lines = []
    for uid, uname, fname, tariff, gid in users:
        t = PRICES.get(tariff, {}).get("name", tariff)
        lines.append(f"👤 {fname} (@{uname or 'нет'}) — {t}, Группа {gid}")
    await callback.message.edit_text(
        f"👥 <b>Записавшиеся ({len(users)}):</b>\n\n" + "\n".join(lines),
        reply_markup=admin_keyboard()
    )

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if (callback.from_user.username or "") not in ADMINS:
        return
    await callback.message.edit_text("📢 Напиши сообщение для рассылки всем участницам:", reply_markup=back_keyboard())
    await state.set_state(Form.broadcast_text)

@dp.message(Form.broadcast_text)
async def do_broadcast(message: Message, state: FSMContext):
    if (message.from_user.username or "") not in ADMINS:
        return
    users = get_all_users()
    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 {message.text}")
            sent += 1
        except Exception:
            pass
    await message.answer(f"✅ Рассылка отправлена {sent} участницам", reply_markup=main_menu_keyboard(True))
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast_group")
async def admin_broadcast_group(callback: CallbackQuery, state: FSMContext):
    if (callback.from_user.username or "") not in ADMINS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=f"Группа {g}", callback_data=f"bcastgroup_{g}")]
          for g in SCHEDULE.keys()],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
    ])
    await callback.message.edit_text("Выбери группу для рассылки:", reply_markup=kb)

@dp.callback_query(F.data.startswith("bcastgroup_"))
async def pick_broadcast_group(callback: CallbackQuery, state: FSMContext):
    gid = int(callback.data.replace("bcastgroup_", ""))
    await state.update_data(broadcast_group=gid)
    await callback.message.edit_text(f"📢 Напиши сообщение для Группы {gid}:", reply_markup=back_keyboard())
    await state.set_state(Form.broadcast_group_text)

@dp.message(Form.broadcast_group_text)
async def do_broadcast_group(message: Message, state: FSMContext):
    if (message.from_user.username or "") not in ADMINS:
        return
    data = await state.get_data()
    gid  = data.get("broadcast_group")
    users = get_paid_users_by_group(gid)
    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 {message.text}")
            sent += 1
        except Exception:
            pass
    await message.answer(f"✅ Рассылка отправлена {sent} участницам Группы {gid}", reply_markup=main_menu_keyboard(True))
    await state.clear()

@dp.callback_query(F.data == "admin_addold")
async def admin_addold_prompt(callback: CallbackQuery, state: FSMContext):
    if (callback.from_user.username or "") not in ADMINS:
        return
    await state.set_state(Form.addold_collecting)
    await state.update_data(addold_list=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="addold_done")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
    ])
    await callback.message.edit_text(
        "➕ <b>Добавление старых учеников</b>\n\n"
        "Отправляй username'ы по одному или списком (каждый с новой строки).\n"
        "Можно с @ или без.\n\n"
        "Когда закончишь — нажми <b>✅ Готово</b>.",
        reply_markup=kb
    )

@dp.message(Form.addold_collecting)
async def addold_receive(message: Message, state: FSMContext):
    if (message.from_user.username or "") not in ADMINS:
        return
    data = await state.get_data()
    current = data.get("addold_list", [])
    new_names = [l.strip().lstrip("@").lower() for l in message.text.split("\n") if l.strip()]
    current.extend(new_names)
    await state.update_data(addold_list=current)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="addold_done")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
    ])
    await message.answer(
        f"✅ Добавлено в список: {', '.join('@' + u for u in new_names)}\n"
        f"Всего в очереди: {len(current)} чел.\n\n"
        f"Продолжай отправлять или нажми <b>✅ Готово</b>.",
        reply_markup=kb
    )

@dp.callback_query(F.data == "addold_done")
async def addold_done(callback: CallbackQuery, state: FSMContext):
    if (callback.from_user.username or "") not in ADMINS:
        return
    data = await state.get_data()
    usernames = data.get("addold_list", [])
    await state.clear()
    if not usernames:
        await callback.message.edit_text("Список пустой, никого не добавлено.", reply_markup=admin_keyboard())
        return
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    for u in usernames:
        c.execute("INSERT OR IGNORE INTO old_students (username) VALUES (?)", (u,))
        c.execute("UPDATE users SET discount=13, discount_type='old_student' WHERE LOWER(username)=?", (u,))
    conn.commit(); conn.close()
    await callback.message.edit_text(
        f"✅ Добавлено {len(usernames)} старых учеников:\n" + "\n".join(f"@{u}" for u in usernames),
        reply_markup=admin_keyboard()
    )

@dp.callback_query(F.data == "admin_resetdiscount")
async def admin_resetdiscount_list(callback: CallbackQuery, state: FSMContext):
    if (callback.from_user.username or "") not in ADMINS:
        return
    await state.clear()
    users = get_users_with_discount()
    if not users:
        await callback.message.edit_text("Нет пользователей со скидкой.", reply_markup=admin_keyboard())
        return
    buttons = []
    for uid, uname, fname, disc, dtype in users:
        label = f"{fname or uname or uid} — {disc}% ({dtype})"
        buttons.append([InlineKeyboardButton(text=f"❌ {label}", callback_data=f"do_reset_{uid}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    await callback.message.edit_text(
        "🗑 <b>Сбросить скидку</b>\n\nНажми ❌ рядом с нужным человеком:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@dp.callback_query(F.data.startswith("do_reset_"))
async def do_reset_discount_btn(callback: CallbackQuery):
    if (callback.from_user.username or "") not in ADMINS:
        return
    uid = int(callback.data.replace("do_reset_", ""))
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("UPDATE users SET discount=0, discount_type='' WHERE user_id=?", (uid,))
    conn.commit(); conn.close()
    # Обновляем список
    users = get_users_with_discount()
    if not users:
        await callback.message.edit_text("✅ Скидка сброшена. Больше нет пользователей со скидкой.", reply_markup=admin_keyboard())
        return
    buttons = []
    for u_id, uname, fname, disc, dtype in users:
        label = f"{fname or uname or u_id} — {disc}% ({dtype})"
        buttons.append([InlineKeyboardButton(text=f"❌ {label}", callback_data=f"do_reset_{u_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    await callback.answer("✅ Скидка сброшена")
    await callback.message.edit_text(
        "🗑 <b>Сбросить скидку</b>\n\nНажми ❌ рядом с нужным человеком:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# ── Напоминания ───────────────────────────────────────────────────────────────

async def send_reminders():
    tomorrow = date.today() + timedelta(days=1)
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("SELECT user_id, group_id, tariff FROM users WHERE paid=1")
    users = c.fetchall()
    conn.close()

    for user_id, group_id, tariff in users:
        events = SCHEDULE.get(group_id, [])
        for e in events:
            if e["date"] != tomorrow:
                continue
            if tariff != "full" and e["type"] != "dance":
                continue
            etype = e["type"]
            time  = e["time"]
            if etype == "dance":
                text = f"🔔 Завтра танцы! 💃\n🕗 {time}"
            elif etype == "meeting":
                text = f"🔔 Завтра встреча с Лё! 🤝\n🕗 {time}"
            else:
                text = (f"🔔 Завтра съёмка! 📸\n🕗 {time}\n\n"
                        f"⏰ Приходи за 30 минут до начала\n"
                        f"📄 Не забудь паспорт\n"
                        f"💡 Рекомендации: {SHOOT_RECOMMENDATIONS}")
            try:
                await bot.send_message(user_id, text)
            except Exception as ex:
                logging.error(ex)


# ── Напоминалка про незавершённую оплату ─────────────────────────────────────

async def send_payment_reminders():
    """Отправляет напоминание тем, кто дошёл до оплаты но не прислал скриншот (pending > 24ч)"""
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    # Берём юзеров у которых есть pending платёж (ещё не подтверждён и не отклонён)
    c.execute("""
        SELECT DISTINCT p.user_id FROM pending_payments p
        WHERE p.status = 'pending'
        AND p.user_id NOT IN (SELECT user_id FROM pending_payments WHERE status = 'confirmed')
    """)
    rows = c.fetchall()
    conn.close()
    for (user_id,) in rows:
        try:
            await bot.send_message(
                user_id,
                "💬 Привет! Возникли проблемы с оплатой?\n\n"
                "Если что-то пошло не так — напиши @maxtroid, разберёмся 🙏\n\n"
                "Или отправь скриншот перевода прямо сюда если уже оплатил 📸"
            )
        except Exception as ex:
            logging.error(ex)

# ── /deleteme — удаление из БД (только админы) ───────────────────────────────

@dp.message(Command("deleteme"))
async def delete_user_cmd(message: Message):
    if (message.from_user.username or "") not in ADMINS:
        return
    parts = message.text.split()
    # Если без аргумента — удаляем себя
    if len(parts) == 1:
        target_id = message.from_user.id
        target_name = "себя"
    else:
        # /deleteme @username или /deleteme user_id
        arg = parts[1].lstrip("@")
        if arg.isdigit():
            target_id = int(arg)
            target_name = f"ID {target_id}"
        else:
            # Ищем по username
            conn = sqlite3.connect("dance.db")
            c = conn.cursor()
            c.execute("SELECT user_id FROM users WHERE LOWER(username)=?", (arg.lower(),))
            row = c.fetchone()
            conn.close()
            if not row:
                await message.answer(f"❌ Пользователь @{arg} не найден в БД")
                return
            target_id = row[0]
            target_name = f"@{arg}"

    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE user_id = ?", (target_id,))
    c.execute("DELETE FROM pending_payments WHERE user_id = ?", (target_id,))
    c.execute("DELETE FROM ref_used WHERE user_id = ?", (target_id,))
    conn.commit(); conn.close()
    await message.answer(f"🗑 {target_name} удалён из базы данных.")

# ── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    init_db()
    scheduler.add_job(send_reminders, "cron", hour=12, minute=0)
    scheduler.add_job(send_payment_reminders, "cron", hour=10, minute=0)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
