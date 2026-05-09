import asyncio
import logging
import time
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
import aiosqlite

# ================= НАСТРОЙКИ (КОНФИГ) =================
TOKEN = '8585031028:AAFUkaAvE6c7gKs15xs_DFS5HJHdXZCfQr0'
ADMIN_ID = 8743889402  # Твой ID
CHANNEL_ID = -1003936750917  # ID канала
COOLDOWN_SECONDS = 60  # КД в секундах
DB_NAME = 'school_bot.db'

BANNED_MESSAGE = "🚫 <b>ВЫ ПОПАЛИ В СПИСОК ДАУНОВ</b> (для дегенератов: вас заблокировал админ) причину у админа спроси https://t.me/anonaskbot?start=a6dhbvl"
# ======================================================

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=TOKEN, 
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()


# ================= РАБОТА С БАЗОЙ ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS blacklist (user_id INTEGER PRIMARY KEY)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, last_msg_time REAL)''')
        await db.commit()

async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def ban_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO blacklist (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def unban_user(user_id: int):
    """Удаляет пользователя из черного списка."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        await db.commit()

async def check_and_update_cooldown(user_id: int) -> bool:
    current_time = time.time()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_msg_time FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                if current_time - row[0] < COOLDOWN_SECONDS:
                    return False
        
        await db.execute("INSERT OR REPLACE INTO users (user_id, last_msg_time) VALUES (?, ?)", (user_id, current_time))
        await db.commit()
        return True


# ================= ХЭНДЛЕРЫ =================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    
    if await is_banned(user_id):
        await message.answer(BANNED_MESSAGE)
        return

    text = (
        "Это предложка школы 1 шумилино, пиши че хочешь. Всё анонимно. "
        "Правила: не спамить, желательно без матов, +18 контент запрещен. "
        "Наказание: бан в предложке 👇"
    )
    await message.answer(text)


@dp.message((F.text | F.photo) & (F.chat.type == "private") & ~F.reply_to_message)
async def handle_suggestion(message: types.Message):
    """Обработчик входящих предложенных новостей (текст и фото)"""
    user_id = message.from_user.id

    if await is_banned(user_id):
        await message.answer(BANNED_MESSAGE)
        return

    if user_id != ADMIN_ID:
        if not await check_and_update_cooldown(user_id):
            await message.answer("Подожди немного! Отправлять сообщения можно раз в минуту.")
            return

    raw_text = message.text or message.caption or ""
    text_safe = raw_text.replace('<', '&lt;').replace('>', '&gt;')

    if text_safe:
        channel_content = f"<blockquote><i>{text_safe}</i></blockquote>"
        admin_content = f"<blockquote>{text_safe}</blockquote>"
    else:
        channel_content = "<i>(Без текста)</i>"
        admin_content = "<i>(Без текста)</i>"

    channel_text = f"Новое сообщение:\n{channel_content}"
    
    # --- Отправка в канал ---
    try:
        if message.photo:
            file_id = message.photo[-1].file_id
            await bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=channel_text)
        else:
            await bot.send_message(chat_id=CHANNEL_ID, text=channel_text)
    except TelegramAPIError as e:
        logging.error(f"Ошибка отправки в канал: {e}")
        await message.answer("Произошла ошибка при публикации. Администратор уже уведомлен.")
        return

    # --- Отправка Админу ---
    username = f"@{message.from_user.username}" if message.from_user.username else "Нет"
    name = message.from_user.full_name
    
    admin_text = (
        f"Новое сообщение:\n{admin_content}\n\n"
        f"👤 Имя: <a href='tg://user?id={user_id}'>{name}</a>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🔗 Ссылка: {username}\n\n"
        f"<i>Свайпни что бы ответить (только у админа)</i>"
    )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 ЗАБЛОКИРОВАТЬ", callback_data=f"ban_{user_id}")]
    ])
    
    try:
        if message.photo:
            file_id = message.photo[-1].file_id
            await bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=admin_text, reply_markup=markup)
        else:
            await bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=markup)
    except TelegramAPIError as e:
        logging.error(f"Ошибка отправки админу: {e}")

    await message.answer("Сообщение отправлено. ✅ Всё опубликовано полностью анонимно. Чекай канал. Если хочешь отправить что-то еще — пиши прямо сюда.")


@dp.callback_query(F.data.startswith('ban_'))
async def ban_callback(callback: types.CallbackQuery):
    """Обработчик кнопки БАН у админа"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("У вас нет прав!", show_alert=True)
        return

    target_id = int(callback.data.split('_')[1])
    await ban_user(target_id)
    
    new_text = f"🚫 Пользователь <code>{target_id}</code> заблокирован."
    
    # Меняем кнопку на "РАЗБЛОКИРОВАТЬ"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ РАЗБЛОКИРОВАТЬ", callback_data=f"unban_{target_id}")]
    ])
    
    if callback.message.photo:
        await callback.message.edit_caption(caption=new_text, reply_markup=markup)
    else:
        await callback.message.edit_text(text=new_text, reply_markup=markup)
        
    await callback.answer("Пользователь добавлен в черный список!")


@dp.callback_query(F.data.startswith('unban_'))
async def unban_callback(callback: types.CallbackQuery):
    """Обработчик кнопки РАЗБАН у админа"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("У вас нет прав!", show_alert=True)
        return

    target_id = int(callback.data.split('_')[1])
    await unban_user(target_id)
    
    new_text = f"✅ Пользователь <code>{target_id}</code> разблокирован."
    
    # Убираем все кнопки после разбана
    if callback.message.photo:
        await callback.message.edit_caption(caption=new_text)
    else:
        await callback.message.edit_text(text=new_text)
        
    await callback.answer("Пользователь удален из черного списка!")


@dp.message(F.reply_to_message & (F.from_user.id == ADMIN_ID))
async def admin_reply_to_user(message: types.Message):
    """Свайп для ответа"""
    reply_text = message.reply_to_message.text or message.reply_to_message.caption
    if not reply_text:
        return

    match = re.search(r"🆔 ID:\s*(\d+)", reply_text)
    if match:
        target_id = int(match.group(1))
        admin_answer = message.text.replace('<', '&lt;').replace('>', '&gt;')
        
        # Обновленный текст ответа с припиской перед ссылкой
        response_to_user = (
            f"<b>Ответ от администратора:</b>\n"
            f"<blockquote>{admin_answer}</blockquote>\n\n"
            f"анонка адмна\n"
            f"https://t.me/anonaskbot?start=a6dhbvl"
        )
        
        try:
            await bot.send_message(chat_id=target_id, text=response_to_user, disable_web_page_preview=True)
            await message.reply("✅ Ваш ответ успешно доставлен пользователю.")
        except TelegramAPIError:
            await message.reply("❌ Не удалось доставить. Возможно, пользователь заблокировал бота.")
    else:
        await message.reply("Не смог найти ID пользователя в этом сообщении.")


# ================= ЗАПУСК БОТА =================
async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
