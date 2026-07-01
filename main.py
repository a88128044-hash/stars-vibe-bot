import os
import asyncio
import sqlite3
import random
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

class BuyOrder(StatesGroup):
    waiting_for_channel = State()

class PromoState(StatesGroup):
    waiting_for_code = State()

TOKEN = os.environ.get("BOT_TOKEN")
try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
except:
    ADMIN_ID = 0

bot = Bot(token=TOKEN)
dp = Dispatcher()

DB_NAME = "stars-tg-bot/database.db"
REF_BONUS = 0.5


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0.0,
            referred_by INTEGER DEFAULT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS completed_tasks (
            user_id INTEGER,
            channel_username TEXT,
            PRIMARY KEY (user_id, channel_username)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            username TEXT PRIMARY KEY,
            reward REAL DEFAULT 0.25
        )
    ''')
    cursor.execute('INSERT OR IGNORE INTO channels (username, reward) VALUES (?, ?)', ("durov", 0.25))
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS achievements (
            user_id INTEGER,
            milestone INTEGER,
            PRIMARY KEY (user_id, milestone)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            reward REAL,
            uses_left INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS used_promos (
            user_id INTEGER,
            code TEXT,
            PRIMARY KEY (user_id, code)
        )
    ''')
    conn.commit()
    conn.close()

def add_user(user_id, username, referred_by=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    exists = cursor.fetchone()
    if not exists:
        cursor.execute(
            'INSERT INTO users (user_id, username, referred_by) VALUES (?, ?, ?)',
            (user_id, username, referred_by)
        )
        conn.commit()
        conn.close()
        return True  # новый пользователь
    conn.close()
    return False  # уже был

def get_balance(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0.0

def change_balance(user_id, amount):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def check_task_done(user_id, channel_username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM completed_tasks WHERE user_id = ? AND channel_username = ?', (user_id, channel_username))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def reward_user(user_id, channel_username, amount):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    cursor.execute('INSERT INTO completed_tasks (user_id, channel_username) VALUES (?, ?)', (user_id, channel_username))
    conn.commit()
    conn.close()

def get_random_task(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT username, reward FROM channels
        WHERE username NOT IN (SELECT channel_username FROM completed_tasks WHERE user_id = ?)
        LIMIT 1
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def get_referral_count(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def db_add_channel(username, reward):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO channels (username, reward) VALUES (?, ?)', (username, reward))
    conn.commit()
    conn.close()

def db_delete_channel(username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM channels WHERE username = ?', (username,))
    conn.commit()
    conn.close()

def db_add_promo(code, reward, uses):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO promo_codes (code, reward, uses_left) VALUES (?, ?, ?)', (code, reward, uses))
    conn.commit()
    conn.close()

def db_use_promo(user_id, code):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT reward, uses_left FROM promo_codes WHERE code = ?', (code,))
    promo = cursor.fetchone()
    if not promo:
        conn.close()
        return None, "not_found"
    reward, uses_left = promo
    cursor.execute('SELECT 1 FROM used_promos WHERE user_id = ? AND code = ?', (user_id, code))
    if cursor.fetchone():
        conn.close()
        return None, "already_used"
    if uses_left <= 0:
        conn.close()
        return None, "expired"
    cursor.execute('UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code = ?', (code,))
    cursor.execute('INSERT INTO used_promos (user_id, code) VALUES (?, ?)', (user_id, code))
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (reward, user_id))
    conn.commit()
    conn.close()
    return reward, "ok"

def get_profile(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0.0
    cursor.execute('SELECT COUNT(*) FROM completed_tasks WHERE user_id = ?', (user_id,))
    tasks_done = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (user_id,))
    ref_count = cursor.fetchone()[0]
    conn.close()
    return balance, tasks_done, ref_count

def get_task_count(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM completed_tasks WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()[0]
    conn.close()
    return result

def grant_achievement(user_id, milestone):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO achievements (user_id, milestone) VALUES (?, ?)', (user_id, milestone))
    inserted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return inserted  # True = новое достижение

def get_all_user_ids():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    result = [row[0] for row in cursor.fetchall()]
    conn.close()
    return result

# Достижения: milestone -> (эмодзи, название, бонус Stars)
ACHIEVEMENTS = {
    5:  ("🥉", "Новичок",      0.5),
    10: ("🥈", "Активист",     1.0),
    25: ("🥇", "Профи",        2.0),
    50: ("💎", "Легенда",      5.0),
}

init_db()


async def check_and_send_achievements(user_id: int):
    count = get_task_count(user_id)
    for milestone, (emoji, name, bonus) in ACHIEVEMENTS.items():
        if count >= milestone:
            is_new = grant_achievement(user_id, milestone)
            if is_new:
                change_balance(user_id, bonus)
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"🏆 *Новое достижение разблокировано!*\n\n"
                            f"{emoji} *{name}* — выполнено {milestone} заданий\n"
                            f"Бонус: *+{bonus}⭐* зачислено на баланс!"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass


async def notify_all_users(channel_username: str, reward: float):
    user_ids = get_all_user_ids()
    text = (
        f"📢 *Новое задание в NanoStars!*\n\n"
        f"Подпишись на канал @{channel_username} и получи *+{reward}⭐*\n\n"
        f"Нажми «⭐ Заработать Звёзды» прямо сейчас!"
    )
    sent = 0
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)  # защита от flood-лимита Telegram
        except Exception:
            pass
    return sent


@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: types.BotCommand = None):
    user_id = message.from_user.id
    print(f"Юзер {message.from_user.first_name} нажал старт. Его Telegram ID: {user_id}")

    # Парсим реферальный аргумент: /start ref_123456
    referred_by = None
    if message.text and len(message.text.split()) > 1:
        arg = message.text.split()[1]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
                if referrer_id != user_id:
                    referred_by = referrer_id
            except ValueError:
                pass

    is_new = add_user(user_id, message.from_user.username, referred_by)

    # Начисляем бонус рефереру только если юзер реально новый
    if is_new and referred_by:
        change_balance(referred_by, REF_BONUS)
        try:
            await bot.send_message(
                chat_id=referred_by,
                text=f"🎉 По вашей ссылке зарегистрировался новый пользователь!\n"
                     f"Вам начислено +{REF_BONUS}⭐"
            )
        except Exception:
            pass

    builder = ReplyKeyboardBuilder()
    builder.row(
        types.KeyboardButton(text="⭐ Заработать Звёзды"),
        types.KeyboardButton(text="💎 Задания")
    )
    builder.row(
        types.KeyboardButton(text="🎁 Вывести Звёзды"),
        types.KeyboardButton(text="👥 Пригласить друга")
    )
    builder.row(
        types.KeyboardButton(text="🎰 Казино"),
        types.KeyboardButton(text="👥 Купить Подписчиков")
    )
    builder.row(
        types.KeyboardButton(text="📊 Мой профиль"),
        types.KeyboardButton(text="🎟 Промокод")
    )

    welcome_text = (
        f"🚀 Привет, {message.from_user.first_name}! Добро пожаловать в *NanoStars*!\n\n"
        f"Здесь ты можешь:\n"
        f"⭐ Зарабатывать Telegram Stars за подписки\n"
        f"👥 Продвигать свой канал за Stars\n"
        f"🎰 Удваивать Stars в казино\n"
        f"👫 Приглашать друзей и получать бонусы\n\n"
        f"Выбери действие в меню ниже 👇"
    )
    await message.answer(text=welcome_text, reply_markup=builder.as_markup(resize_keyboard=True), parse_mode="Markdown")


@dp.message(F.text == "⭐ Заработать Звёзды")
async def menu_earn(message: types.Message):
    user_id = message.from_user.id
    task = get_random_task(user_id)

    if not task:
        await message.answer("🎉 Пока что новых заданий нет! Все партнеры выполнены. Загляни позже.")
        return

    channel_username, reward = task

    task_text = (
        f"💡 Получай Звёзды за простые задания! 👇\n\n"
        f"🟢 Подпишись на канал и нажми «Подтвердить»\n\n"
        f"Вознаграждение: +{reward}⭐"
    )

    inline_builder = InlineKeyboardBuilder()
    inline_builder.row(
        types.InlineKeyboardButton(text="🔎 Перейти ↗️", url=f"https://t.me/{channel_username}"),
        types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"sub_check:{channel_username}:{reward}")
    )
    inline_builder.row(types.InlineKeyboardButton(text="⏩ Пропустить", callback_data="sub_skip"))

    await message.answer(text=task_text, reply_markup=inline_builder.as_markup())


@dp.callback_query(F.data.startswith("sub_check:"))
async def callback_check_subscription(callback: types.CallbackQuery):
    data = callback.data.split(":")
    channel = data[1]
    reward = float(data[2])
    user_id = callback.from_user.id

    if check_task_done(user_id, channel):
        await callback.answer("Вы уже получили награду!", show_alert=True)
        await callback.message.delete()
        return

    try:
        member = await bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            reward_user(user_id, channel, reward)
            await callback.answer(f"✅ Успешно! +{reward}⭐ начислено.", show_alert=True)
            await callback.message.delete()
            await check_and_send_achievements(user_id)
        else:
            await callback.answer("❌ Вы не подписались на канал!", show_alert=True)
    except TelegramBadRequest:
        await callback.answer(f"⚠️ Тест-режим: зачислено +{reward}⭐", show_alert=True)
        reward_user(user_id, channel, reward)
        await callback.message.delete()
        await check_and_send_achievements(user_id)


@dp.callback_query(F.data == "sub_skip")
async def callback_skip(callback: types.CallbackQuery):
    await callback.answer("Задание пропущено.")
    await callback.message.edit_text("⏳ Задание пропущено. Нажми «Заработать Звёзды» снова, чтобы получить другое.")


@dp.message(F.text == "💎 Задания")
async def menu_tasks_info(message: types.Message):
    await message.answer("ℹ️ Доступные задания обновляются каждый час автоматической системой проверки партнеров.")


@dp.message(F.text == "🎁 Вывести Звёзды")
async def menu_withdraw(message: types.Message):
    user_balance = get_balance(message.from_user.id)
    text = (
        f"Заработано: {user_balance} ⭐\n\n"
        f"Выбери сумму для вывода\n"
        f"Канал с выводами: @ТвойКаналВыводов"
    )
    inline_builder = InlineKeyboardBuilder()
    inline_builder.row(
        types.InlineKeyboardButton(text="15⭐", callback_data="w:15"),
        types.InlineKeyboardButton(text="25⭐", callback_data="w:25")
    )
    inline_builder.row(
        types.InlineKeyboardButton(text="50⭐", callback_data="w:50"),
        types.InlineKeyboardButton(text="100⭐", callback_data="w:100")
    )
    await message.answer(text=text, reply_markup=inline_builder.as_markup())


@dp.message(F.text == "👥 Пригласить друга")
async def menu_referral(message: types.Message):
    user_id = message.from_user.id
    ref_count = get_referral_count(user_id)
    earned = round(ref_count * REF_BONUS, 2)

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    text = (
        f"👥 *Реферальная программа*\n\n"
        f"Приглашай друзей и получай *+{REF_BONUS}⭐* за каждого!\n\n"
        f"🔗 Твоя ссылка:\n`{ref_link}`\n\n"
        f"📊 Приглашено друзей: *{ref_count}*\n"
        f"💰 Заработано на рефералах: *{earned}⭐*"
    )

    inline_builder = InlineKeyboardBuilder()
    inline_builder.row(
        types.InlineKeyboardButton(
            text="📤 Поделиться ссылкой",
            url=f"https://t.me/share/url?url={ref_link}&text=Зарабатывай%20бесплатные%20Telegram%20Stars%20прямо%20сейчас!"
        )
    )

    await message.answer(text=text, reply_markup=inline_builder.as_markup(), parse_mode="Markdown")


@dp.message(F.text == "📊 Мой профиль")
async def menu_profile(message: types.Message):
    user_id = message.from_user.id
    balance, tasks_done, ref_count = get_profile(user_id)
    ref_earned = round(ref_count * REF_BONUS, 2)
    username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name

    # Собираем полученные достижения
    earned = []
    for milestone, (emoji, name, _) in ACHIEVEMENTS.items():
        if tasks_done >= milestone:
            earned.append(f"{emoji} {name}")
    achievements_text = " · ".join(earned) if earned else "Пока нет — выполняй задания!"

    text = (
        f"📊 *Мой профиль*\n\n"
        f"👤 {username}\n"
        f"🆔 ID: `{user_id}`\n\n"
        f"💰 Баланс: *{round(balance, 2)}⭐*\n"
        f"✅ Заданий выполнено: *{tasks_done}*\n"
        f"👥 Приглашено друзей: *{ref_count}*\n"
        f"🎁 Заработано на рефералах: *{ref_earned}⭐*\n\n"
        f"🏆 *Достижения:*\n{achievements_text}"
    )
    await message.answer(text=text, parse_mode="Markdown")


@dp.message(F.text == "🎟 Промокод")
async def menu_promo(message: types.Message, state: FSMContext):
    await message.answer(
        "🎟 *Введи промокод*\n\n"
        "Напиши промокод — и Stars упадут на баланс мгновенно!\n\n"
        "_Промокоды раздаются в нашем канале._",
        parse_mode="Markdown"
    )
    await state.set_state(PromoState.waiting_for_code)


@dp.message(PromoState.waiting_for_code)
async def process_promo_code(message: types.Message, state: FSMContext):
    await state.clear()
    code = message.text.strip().upper()
    user_id = message.from_user.id

    reward, status = db_use_promo(user_id, code)

    if status == "ok":
        new_balance = get_balance(user_id)
        await message.answer(
            f"🎉 *Промокод активирован!*\n\n"
            f"Начислено: *+{reward}⭐*\n"
            f"Текущий баланс: *{round(new_balance, 2)}⭐*",
            parse_mode="Markdown"
        )
    elif status == "not_found":
        await message.answer("❌ Промокод не найден. Проверь правильность ввода.")
    elif status == "already_used":
        await message.answer("⚠️ Ты уже использовал этот промокод.")
    elif status == "expired":
        await message.answer("⏳ Этот промокод больше не активен — все использования исчерпаны.")


# Тарифы: (название, количество подписчиков, цена в Stars)
# Математика: 100 подп. = 200⭐ → 160⭐ блогеру (80%), 40⭐ нам
# Из 40⭐ мы платим ~25⭐ юзерам за подписки → чистый профит 15⭐
SUBSCRIBER_PLANS = [
    ("50 подписчиков", 50, 100),
    ("100 подписчиков", 100, 200),
    ("500 подписчиков", 500, 900),
]


@dp.message(F.text == "👥 Купить Подписчиков")
async def menu_buy_subscribers(message: types.Message, state: FSMContext):
    await message.answer(
        "🚀 *NanoStars — Продвижение канала*\n\n"
        "Введи юзернейм канала, который хочешь продвинуть (БЕЗ знака @).\n\n"
        "После оплаты реальные пользователи NanoStars начнут подписываться автоматически!\n\n"
        "*Пример:* my\\_crypto\\_channel",
        parse_mode="Markdown"
    )
    await state.set_state(BuyOrder.waiting_for_channel)


@dp.message(BuyOrder.waiting_for_channel)
async def process_channel_name(message: types.Message, state: FSMContext):
    channel_username = message.text.replace("@", "").strip()
    await state.clear()

    text = (
        f"📦 *Тарифы для @{channel_username}*\n\n"
        f"Выбери пакет подписчиков:"
    )
    inline_builder = InlineKeyboardBuilder()
    for name, count, price in SUBSCRIBER_PLANS:
        inline_builder.row(
            types.InlineKeyboardButton(
                text=f"{name} — {price}⭐",
                callback_data=f"buy_subs:{count}:{price}:{channel_username}"
            )
        )
    await message.answer(text=text, reply_markup=inline_builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("buy_subs:"))
async def callback_buy_subs(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    count = int(parts[1])
    price = int(parts[2])
    channel_username = parts[3]

    await callback.message.delete()

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"👥 {count} подписчиков для @{channel_username}",
        description=f"Живые подписчики для канала @{channel_username}. Начало — в течение 24 часов после оплаты.",
        payload=f"subs_{count}:{channel_username}",
        currency="XTR",
        prices=[types.LabeledPrice(label=f"{count} подписчиков", amount=price)],
        provider_token="",
    )
    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payment = message.successful_payment
    payload = payment.invoice_payload  # "subs_100:channel_username"
    parts = payload.split(":")
    count = parts[0].split("_")[1]
    channel_username = parts[1] if len(parts) > 1 else "unknown"
    stars_paid = payment.total_amount

    # Автоматически добавляем канал рекламодателя в задания для юзеров!
    reward_per_sub = round(25 / int(count), 2)
    db_add_channel(channel_username, reward_per_sub)
    asyncio.create_task(notify_all_users(channel_username, reward_per_sub))

    await message.answer(
        f"✅ *Оплата принята!*\n\n"
        f"Канал: *@{channel_username}*\n"
        f"Заказ: *{count} подписчиков*\n"
        f"Оплачено: *{stars_paid}⭐*\n\n"
        f"Ваш канал добавлен в задания NanoStars — подписчики начнут приходить автоматически!\n"
        f"По вопросам: @ТвойАккаунт",
        parse_mode="Markdown"
    )

    if ADMIN_ID != 0:
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"💰 *НОВЫЙ ЗАКАЗ!*\n\n"
                    f"👤 @{message.from_user.username} (ID: `{message.from_user.id}`)\n"
                    f"📢 Канал: *@{channel_username}*\n"
                    f"📦 Заказ: *{count} подписчиков*\n"
                    f"⭐ Оплачено: *{stars_paid} Stars*\n\n"
                    f"Канал автоматически добавлен в задания!"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Ошибка уведомления админа: {e}")


WIN_CHANCE = 40  # % шанс выигрыша (40% — профит у владельца)


@dp.message(F.text == "🎰 Казино")
async def menu_casino(message: types.Message):
    balance = get_balance(message.from_user.id)
    text = (
        f"🎰 *Казино*\n\n"
        f"Испытай удачу! Выбери ставку и крути барабан.\n"
        f"Шанс удвоить: *40%* | Шанс потерять: *60%*\n\n"
        f"Твой баланс: *{round(balance, 2)}⭐*\n\n"
        f"Выбери сумму ставки:"
    )
    inline_builder = InlineKeyboardBuilder()
    inline_builder.row(
        types.InlineKeyboardButton(text="1⭐", callback_data="casino:1"),
        types.InlineKeyboardButton(text="2⭐", callback_data="casino:2"),
        types.InlineKeyboardButton(text="5⭐", callback_data="casino:5"),
    )
    inline_builder.row(
        types.InlineKeyboardButton(text="10⭐", callback_data="casino:10"),
        types.InlineKeyboardButton(text="25⭐", callback_data="casino:25"),
    )
    await message.answer(text=text, reply_markup=inline_builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("casino:"))
async def callback_casino_play(callback: types.CallbackQuery):
    bet = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    balance = get_balance(user_id)

    if balance < bet:
        await callback.answer(
            f"❌ Недостаточно звёзд! У тебя {round(balance, 2)}⭐, нужно {bet}⭐",
            show_alert=True
        )
        return

    # Списываем ставку сразу
    change_balance(user_id, -bet)

    # Крутим барабан
    roll = random.randint(1, 100)
    won = roll <= WIN_CHANCE

    if won:
        winnings = bet * 2
        change_balance(user_id, winnings)
        new_balance = get_balance(user_id)
        result_text = (
            f"🎰 Барабан крутится...\n\n"
            f"🍒 🍒 🍒\n\n"
            f"🎉 *ПОБЕДА!* Ты удвоил ставку!\n"
            f"Выиграно: *+{bet}⭐* (ставка возвращена + приз)\n"
            f"Баланс: *{round(new_balance, 2)}⭐*"
        )
    else:
        new_balance = get_balance(user_id)
        symbols = random.choice([
            "🍋 🍒 🍊",
            "🍊 🍋 🍒",
            "🍒 🍊 🍋",
            "🍋 🍊 🍒",
        ])
        result_text = (
            f"🎰 Барабан крутится...\n\n"
            f"{symbols}\n\n"
            f"😞 *Не повезло...* Ставка сгорела.\n"
            f"Потеряно: *{bet}⭐*\n"
            f"Баланс: *{round(new_balance, 2)}⭐*"
        )

    inline_builder = InlineKeyboardBuilder()
    inline_builder.row(
        types.InlineKeyboardButton(text="🔄 Играть снова", callback_data=f"casino:{bet}"),
    )

    await callback.message.edit_text(
        text=result_text,
        reply_markup=inline_builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("w:"))
async def callback_withdraw_request(callback: types.CallbackQuery):
    amount = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    balance = get_balance(user_id)

    if balance < amount:
        await callback.answer(f"❌ Недостаточно звёзд! У вас всего {balance}⭐, а нужно {amount}⭐", show_alert=True)
    else:
        change_balance(user_id, -amount)
        new_balance = get_balance(user_id)

        await callback.answer("🎉 Заявка на вывод успешно создана! Ожидайте выплаты.", show_alert=True)
        await callback.message.edit_text(f"📉 Списано {amount}⭐. Ваш текущий баланс: {new_balance}⭐")

        if ADMIN_ID != 0:
            try:
                admin_text = (
                    f"🚨 **НОВАЯ ЗАЯВКА НА ВЫВОД!** 🚨\n\n"
                    f"👤 Юзер: @{callback.from_user.username} (ID: `{user_id}`)\n"
                    f"💰 Сумма вывода: **{amount} ⭐**\n"
                    f"Переведи ему звёзды на баланс вручную!"
                )
                await bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="Markdown")
            except Exception as e:
                print(f"Не удалось отправить уведомление админу: {e}")


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]

    cursor.execute('SELECT SUM(balance) FROM users')
    total_balance = cursor.fetchone()[0] or 0.0

    cursor.execute('SELECT COUNT(*) FROM completed_tasks')
    total_tasks = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM channels')
    total_channels = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL')
    total_refs = cursor.fetchone()[0]

    cursor.execute('''
        SELECT username, balance FROM users
        ORDER BY balance DESC LIMIT 5
    ''')
    top_users = cursor.fetchall()
    conn.close()

    top_text = ""
    for i, (username, balance) in enumerate(top_users, 1):
        name = f"@{username}" if username else "Без ника"
        top_text += f"  {i}. {name} — {round(balance, 2)}⭐\n"

    stats_text = (
        f"📊 **Статистика бота**\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"👥 Пришли по рефералке: {total_refs}\n"
        f"✅ Заданий выполнено: {total_tasks}\n"
        f"📢 Каналов-партнеров: {total_channels}\n"
        f"💰 Суммарный баланс: {round(total_balance, 2)}⭐\n\n"
        f"🏆 **Топ-5 пользователей:**\n{top_text if top_text else '  Пока пусто'}"
    )
    await message.answer(text=stats_text, parse_mode="Markdown")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    admin_help = (
        "👑 **Админ-панель NanoStars**\n\n"
        "📊 Статистика:\n"
        "`/stats` — пользователи, задания, топ-5\n\n"
        "📢 Управление каналами:\n"
        "`/add_channel юзернейм награда`\n"
        "Пример: `/add_channel durov 0.5`\n"
        "`/del_channel юзернейм`\n\n"
        "🎟 Промокоды:\n"
        "`/add_promo КОД награда количество`\n"
        "Пример: `/add_promo NANO2025 2.0 100`\n"
        "_(создаст промокод NANO2025 на 2⭐, 100 использований)_"
    )
    await message.answer(text=admin_help, parse_mode="Markdown")


@dp.message(Command("add_channel"))
async def admin_add(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Ошибка! Пиши так: `/add_channel юзернейм награда`")
        return

    username = args[1].replace("@", "").strip()
    try:
        reward = float(args[2])
        db_add_channel(username, reward)
        await message.answer(f"✅ Канал @{username} добавлен! Рассылаю уведомления пользователям...")
        sent = await notify_all_users(username, reward)
        await message.answer(f"📢 Уведомление отправлено {sent} пользователям.")
    except ValueError:
        await message.answer("Ошибка! Награда должна быть числом (например, 0.25 или 1).")


@dp.message(Command("del_channel"))
async def admin_del(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Ошибка! Пиши так: `/del_channel юзернейм`")
        return

    username = args[1].replace("@", "").strip()
    db_delete_channel(username)
    await message.answer(f"❌ Канал @{username} удален из списка заданий.")


@dp.message(Command("add_promo"))
async def admin_add_promo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) < 4:
        await message.answer("Ошибка! Пиши так: `/add_promo КОД награда количество`\nПример: `/add_promo NANO2025 2.0 100`")
        return

    code = args[1].upper()
    try:
        reward = float(args[2])
        uses = int(args[3])
        db_add_promo(code, reward, uses)
        await message.answer(
            f"✅ Промокод создан!\n\n"
            f"🎟 Код: `{code}`\n"
            f"💰 Награда: {reward}⭐\n"
            f"🔢 Использований: {uses}"
        )
    except ValueError:
        await message.answer("Ошибка! Пример: `/add_promo NANO2025 2.0 100`")


class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"NanoStars Bot is running 24/7!")

    def log_message(self, format, *args):
        pass  # отключаем лишние логи в консоль

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
        print(f"Веб-сервер запущен на порту {port}")
        server.serve_forever()
    except OSError:
        print(f"Порт {port} занят — веб-сервер не запущен (ок для Replit)")


async def main():
    Thread(target=run_web_server, daemon=True).start()
    print("Бот полностью готов к монетизации! 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
