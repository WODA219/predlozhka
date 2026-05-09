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
COOLDOWN_SECONDS = 150  # КД в секундах
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
        "это предложка школы 1 шумилино. всё анонимно."
        "Пиши "
        "👇"
    )
    await message.answer(text)


@dp.message((F.text | F.photo) & (F.chat.type == "private") & ~F.reply_to_message)
async def handle_suggestion(message: types.Message):
    """Обработчик входящих предложенных новостей (текст и фото)"""
    user_id = message.from_user.id

    # 1. Проверка на бан
    if await is_banned(user_id):
        await message.answer(BANNED_MESSAGE)
        return

    # 2. Проверка КД
    if user_id != ADMIN_ID:
        if not await check_and_update_cooldown(user_id):
            await message.answer("жди 150 секунд")
            return

    # Получаем текст или подпись к фото
    raw_text = message.text or message.caption or ""
    text_safe = raw_text.replace('<', '&lt;').replace('>', '&gt;')

    # Формируем блоки текста
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
        await message.answer("произошла ошибка при публикации")
        return

    # --- Отправка Админу ---
    username = f"@{message.from_user.username}" if message.from_user.username else "Нет"
    name = message.from_user.full_name
    
    admin_text = (
        f"Новое сообщение:\n{admin_content}\n\n"
        f"👤 Имя: <a href='tg://user?id={user_id}'>{name}</a>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🔗 Ссылка: {username}\n\n"
        f"<i>Свайпни что бы ответить</i>"
    )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"ban_{user_id}")]
    ])
    
    try:
        if message.photo:
            file_id = message.photo[-1].file_id
            await bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=admin_text, reply_markup=markup)
        else:
            await bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=markup)
    except TelegramAPIError as e:
        logging.error(f"Ошибка отправки админу: {e}")

    # --- Ответ пользователю об успехе ---
    await message.answer("Сообщение отправлено в подслушано")


@dp.callback_query(F.data.startswith('ban_'))
async def ban_callback(callback: types.CallbackQuery):
    """Обработчик кнопки БАН у админа"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("У вас нет прав", show_alert=True)
        return

    target_id = int(callback.data.split('_')[1])
    await ban_user(target_id)
    
    new_text = f"🚫 Пользователь <code>{target_id}</code> заблокирован."
    
    # Меняем подпись (если фото) или текст (если обычное сообщение)
    if callback.message.photo:
        await callback.message.edit_caption(caption=new_text)
    else:
        await callback.message.edit_text(text=new_text)
        
    await callback.answer("Пользователь заблокирован")


@dp.message(F.reply_to_message & (F.from_user.id == ADMIN_ID))
async def admin_reply_to_user(message: types.Message):
    """Свайп для ответа (работает и с фото, и с текстом)"""
    # Достаем текст или подпись (если админ свайпнул фото)
    reply_text = message.reply_to_message.text or message.reply_to_message.caption
    if not reply_text:
        return

    # Ищем ID пользователя
    match = re.search(r"🆔 ID:\s*(\d+)", reply_text)
    if match:
        target_id = int(match.group(1))
        admin_answer = message.text.replace('<', '&lt;').replace('>', '&gt;')
        
        response_to_user = (
            f"<b>Ответ от администратора:</b>\n"
            f"<blockquote>{admin_answer}</blockquote>\n\n"
            f"https://t.me/anonaskbot?start=a6dhbvl"
        )
        
        try:
            await bot.send_message(chat_id=target_id, text=response_to_user, disable_web_page_preview=True)
            await message.reply("✅ ответ отправлен ")
        except TelegramAPIError:
            await message.reply("❌ Не удалось доставить. Возможно, пользователь заблокировал бота.")
    else:
        await message.reply("Не смог найти ID пользователя в этом сообщении.")


# ================= ЗАПУСК БОТА =================
async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот успешно запущен и готов принимать фото/текст!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())