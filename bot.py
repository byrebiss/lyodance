import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = "serotonin_high"
ADMINS = {"serotonin_high", "maxtroid"}
PAYMENT_PHONE = "89999998266"
PAYMENT_NAME = "Елена (Т-Банк)"

PRICES = {
    "single": {"name": "Разовое занятие", "price": 4500},
    "four":   {"name": "4 занятия",        "price": 12000},
    "full":   {"name": "Полный пакет",     "price": 20000},
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

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
            paid          INTEGER DEFAULT 0
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
            amount       INTEGER,
            discount     INTEGER,
            final_amount INTEGER,
            status       TEXT DEFAULT 'pending'
        )
    """)
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
    c.execute("""
        INSERT INTO users (user_id, username, full_name, discount, discount_type, ref_code, referred_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name
    """, (user_id, username, full_name, discount, discount_type, ref_code, referred_by))
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

def add_pending_payment(user_id, tariff, amount, discount, final_amount):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("INSERT INTO pending_payments (user_id, tariff, amount, discount, final_amount) VALUES (?,?,?,?,?)",
              (user_id, tariff, amount, discount, final_amount))
    pid = c.lastrowid
    conn.commit()
    conn.close()
    return pid

def confirm_payment(payment_id):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("UPDATE pending_payments SET status='confirmed' WHERE id=?", (payment_id,))
    c.execute("UPDATE users SET paid=1 WHERE user_id=(SELECT user_id FROM pending_payments WHERE id=?)", (payment_id,))
    c.execute("SELECT user_id FROM pending_payments WHERE id=?", (payment_id,))
    row = c.fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else None

def reject_payment(payment_id):
    conn = sqlite3.connect("dance.db")
    c = conn.cursor()
    c.execute("UPDATE pending_payments SET status='rejected' WHERE id=?", (payment_id,))
    c.execute("SELECT user_id FROM pending_payments WHERE id=?", (payment_id,))
    row = c.fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else None

# ── FSM ───────────────────────────────────────────────────────────────────────

class Form(StatesGroup):
    waiting_screenshot        = State()
    waiting_repost_screenshot = State()

# ── Клавиатуры ────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Записаться на курс",        callback_data="enroll")],
        [InlineKeyboardButton(text="🎁 Моя реферальная ссылка",    callback_data="my_ref")],
        [InlineKeyboardButton(text="📸 Скидка за репост",          callback_data="repost")],
        [InlineKeyboardButton(text="❓ Вопросы",                    callback_data="faq")],
    ])

def tariff_keyboard(discount):
    buttons = []
    for key, val in PRICES.items():
        final = int(val["price"] * (1 - discount / 100))
        label = f"{val['name']} — {final:,} ₽" + (f" (скидка {discount}%)" if discount else "")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"tariff_{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
    ])

# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user      = message.from_user
    username  = user.username or ""
    full_name = user.full_name or ""
    user_id   = user.id

    discount = 0; discount_type = ""; referred_by = None
    args  = message.text.split()
    param = args[1] if len(args) > 1 else ""

    if param == "vip" or is_old_student(username):
        discount = 13; discount_type = "old_student"
    elif param.startswith("ref_"):
        ref_owner = get_user_by_ref(param)
        if ref_owner and ref_owner[0] != user_id:
            discount = 5; discount_type = "referral"; referred_by = ref_owner[0]

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
        reply_markup=main_menu_keyboard()
    )

# ── Записаться ────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "enroll")
async def enroll(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    discount = user[3] if user else 0
    await callback.message.edit_text("Выбери тариф 👇", reply_markup=tariff_keyboard(discount))

@dp.callback_query(F.data.startswith("tariff_"))
async def choose_tariff(callback: CallbackQuery, state: FSMContext):
    key    = callback.data.replace("tariff_", "")
    tariff = PRICES[key]
    user   = get_user(callback.from_user.id)
    discount = user[3] if user else 0
    final  = int(tariff["price"] * (1 - discount / 100))

    await state.update_data(tariff_key=key, tariff_name=tariff["name"],
                             original=tariff["price"], discount=discount, final=final)
    await callback.message.edit_text(
        f"Ты выбрала <b>{tariff['name']}</b>\n\n"
        f"💰 К оплате: <b>{final:,} ₽</b>" + (f" (скидка {discount}%)" if discount else "") +
        f"\n\n📱 Переведи на номер:\n<code>{PAYMENT_PHONE}</code>\n({PAYMENT_NAME})\n\n"
        f"После оплаты отправь сюда <b>скриншот перевода</b> 📸"
    )
    await state.set_state(Form.waiting_screenshot)

@dp.message(Form.waiting_screenshot, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user
    pid  = add_pending_payment(user.id, data["tariff_key"], data["original"], data["discount"], data["final"])

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{pid}"),
        InlineKeyboardButton(text="❌ Отклонить",   callback_data=f"reject_{pid}"),
    ]])
    try:
        await bot.send_photo(
            chat_id=f"@{ADMIN_USERNAME}",
            photo=message.photo[-1].file_id,
            caption=(
                f"💳 <b>Новая оплата #{pid}</b>\n\n"
                f"👤 {user.full_name} (@{user.username or 'нет'})\n"
                f"📦 {data['tariff_name']}\n"
                f"💰 {data['final']:,} ₽" + (f" (скидка {data['discount']}%)" if data['discount'] else "")
            ),
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Ошибка отправки Полине: {e}")

    await message.answer("✅ Скриншот получен! Ожидай подтверждения от Полины 🙏")
    await state.clear()

@dp.message(Form.waiting_screenshot)
async def screenshot_wrong(message: Message):
    await message.answer("Пожалуйста, отправь <b>скриншот</b> (фото) перевода 📸")

# ── Подтверждение / отклонение ────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("confirm_"))
async def admin_confirm(callback: CallbackQuery):
    pid     = int(callback.data.replace("confirm_", ""))
    user_id = confirm_payment(pid)
    if user_id:
        try:
            await bot.send_message(user_id,
                "🎉 <b>Оплата подтверждена!</b>\n\nТы в курсе! Полина скоро напишет тебе с деталями 💃")
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
                "😔 Оплата не подтверждена. Напиши @serotonin_high чтобы разобраться 🙏")
        except Exception as e:
            logging.error(e)
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ <b>Отклонено</b>")

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
        "2. Сделай скриншот и отправь сюда\n\nПолина проверит и скидка активируется ✅"
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
            chat_id=f"@{ADMIN_USERNAME}",
            photo=message.photo[-1].file_id,
            caption=f"📸 <b>Репост на проверку</b>\n\n👤 {user.full_name} (@{user.username or 'нет'})\nID: {user.id}",
            reply_markup=kb
        )
    except Exception as e:
        logging.error(e)
    await message.answer("Скриншот отправлен на проверку! Полина подтвердит в ближайшее время 🙏")
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
        "Адрес пришлёт Полина после подтверждения оплаты\n\n"
        "<b>Можно перенести занятие?</b>\n"
        "Да, напиши @serotonin_high заранее\n\n"
        "<b>Для кого курс?</b>\n"
        "Для всех, уровень не важен 💃\n\n"
        "<b>Остались вопросы?</b>\n"
        "Напиши @serotonin_high",
        reply_markup=back_keyboard()
    )

@dp.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню 👇", reply_markup=main_menu_keyboard())

# ── Админ команды ─────────────────────────────────────────────────────────────

@dp.message(Command("addold"))
async def add_old_students(message: Message):
    if message.from_user.username not in ADMINS:
        return
    lines     = message.text.replace("/addold", "").strip().split("\n")
    usernames = [l.strip().lstrip("@").lower() for l in lines if l.strip()]
    conn = sqlite3.connect("dance.db")
    c    = conn.cursor()
    for u in usernames:
        c.execute("INSERT OR IGNORE INTO old_students (username) VALUES (?)", (u,))
        c.execute("UPDATE users SET discount=13, discount_type='old_student' WHERE LOWER(username)=?", (u,))
    conn.commit(); conn.close()
    await message.answer(f"✅ Добавлено {len(usernames)} учеников из старого потока")

# ── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
