import gspread
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
import asyncio
import os
import json
from aiohttp import web

# --- Настройка Telegram ---
TELEGRAM_TOKEN = "8395846968:AAGtrBhr5N9SGgEayzd5SxlJznrfcU_UQwk"

# --- Авторизация в Google Sheets ---
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds_dict = json.loads(os.getenv('GOOGLE_CREDENTIALS_JSON'))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

SHEET_NAME = 'BonusPointsBot'
spreadsheet = gc.open(SHEET_NAME)
users_sheet = spreadsheet.worksheet('Пользователи')
prizes_sheet = spreadsheet.worksheet('Призы')

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
router = Router()

# --- FSM состояния ---
class Registration(StatesGroup):
    choosing_language = State()
    waiting_for_name = State()

# --- Хранилище выбора языка ---
user_lang = {}

# --- Подгрузка текстов ---
def get_texts(user_id: str):
    lang = user_lang.get(user_id, "lv")
    if lang == "lv":
        import text_lv as texts
    else:
        import text_ru as texts
    return texts

# =================================
# 1. START — регистрация
# =================================

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🇷🇺 Русский"), KeyboardButton(text="🇱🇻 Latviešu")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    # перешли в выбор языка для старта
    await message.answer("Izvēlies valodu: 🇷🇺 Krievu vai 🇱🇻 Latviešu", reply_markup=kb)
    await state.set_state(Registration.choosing_language)

@router.message(Registration.choosing_language)
async def choose_language_start(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)

    if "Рус" in message.text:
        user_lang[user_id] = "ru"
    elif "Latv" in message.text:
        user_lang[user_id] = "lv"
    else:
        await message.answer("Please, choose language / Lūdzu, izvēlieties valodu")
        return

    users = users_sheet.get_all_records()
    user = next((u for u in users if str(u.get("TelegramID")) == user_id), None)
    texts = get_texts(user_id)

    if user:
        await message.answer(texts.messages["already_registered"] + "\n\n" + texts.messages["help"], reply_markup=None)
        await state.clear()
    else:
        await message.answer(texts.messages["ask_name"], reply_markup=None)
        await state.set_state(Registration.waiting_for_name)

@router.message(Registration.waiting_for_name)
async def save_user_name(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    texts = get_texts(user_id)

    full_name = message.text.strip()
    users = users_sheet.get_all_records()

    if any(str(u.get('TelegramID')) == user_id for u in users):
        await message.answer(texts.messages["already_registered"])
        await state.clear()
        return

    parts = full_name.split(maxsplit=1)
    name = parts[0]
    surname = parts[1] if len(parts) > 1 else ""

    lang = user_lang.get(user_id, "ru")

    await message.answer(texts.messages["ask_parent"])
    await state.update_data(name=name, surname=surname, lang=lang)
    await state.set_state("waiting_for_parent")

@router.message(StateFilter('waiting_for_parent'))
async def save_parent(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    texts = get_texts(user_id)

    parent_name = message.text.strip()
    data = await state.get_data()

    name = data.get("name")
    surname = data.get("surname")
    lang = data.get("lang")

    users = users_sheet.get_all_records()
    next_row = len(users) + 2

    users_sheet.update(f'A{next_row}:E{next_row}', [[user_id, name, surname, parent_name, lang]])

    await message.answer(texts.messages["registered"].format(name=name))
    await message.answer(texts.messages["help"])
    await state.clear()

# =================================
# 2. VALODA — смена языка без регистрации
# =================================

@router.message(Command("valoda"))
async def cmd_language(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🇷🇺 Русский"), KeyboardButton(text="🇱🇻 Latviešu")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await message.answer("Выберите язык: 🇷🇺 Русский или 🇱🇻 Latviešu", reply_markup=kb)
    await state.set_state(Registration.choosing_language)

# =================================
# 3. PUNKT — показать баланс
# =================================
@router.message(Command("punkti"))
async def command_punkti(message: Message):
    user_id = str(message.from_user.id)
    texts = get_texts(user_id)

    users = users_sheet.get_all_records()
    user = next((u for u in users if str(u.get('TelegramID')) == user_id), None)

    if user:
        months_translated = texts.months
        child_details = ""
        parent_details = ""
        child_total = 0
        parent_total = 0

        for month_en, month_local in months_translated.items():
            points_str = user.get(month_en, 0)
            points = int(points_str) if points_str and points_str != '' else 0
            if points > 0:
                child_details += f"{month_local}: +{points}\n"
                child_total += points

        for month_en, month_local in months_translated.items():
            parent_key = f"{month_en} Parent"
            points_str = user.get(parent_key, 0)
            points = int(points_str) if points_str and points_str != '' else 0
            if points > 0:
                parent_details += f"{month_local}: +{points}\n"
                parent_total += points

        response = ""

        if child_details:
            response += f"{texts.messages['child_points_title']} {user.get('Name')} {user.get('Surname')}: \n{child_details}" + texts.messages['total_points'].format(total=child_total) + "\n\n"
        else:
            response += texts.messages['no_child_points'] + "\n\n"

        if parent_details:
            response += f"{texts.messages['parent_points_title']} {user.get('Parent')}: \n{parent_details}" + texts.messages['total_points'].format(total=parent_total)
        else:
            response += texts.messages['no_parent_points']

        await message.answer(response)
    else:
        await message.answer(texts.messages["profile_not_found"])

# =================================
# 4. DAVANAS — призы
# =================================
@router.message(Command("davanas"))
async def command_davanas(message: Message):
    user_id = str(message.from_user.id)
    texts = get_texts(user_id)

    prizes = prizes_sheet.get_all_records()
    response = texts.messages["prizes_title"]
    for prize in prizes:
        response += texts.messages["prize_line"].format(
            prize=prize['Prize'], 
            points=prize['Points']
        )

    await message.answer(response)

# --- HELP ---
@router.message()
async def send_help(message: Message):
    texts = get_texts(str(message.from_user.id))
    await message.answer(texts.messages["help"])

# --- Регистрируем роутер ---
dp.include_router(router)

# --- HTTP-сервер для Render ---
async def http_handler(request):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get('/', http_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"HTTP-сервер запущен на порту {port}")

async def main():
    await start_web()
    await dp.start_polling(bot)

if __name__ == '__main__':
    import logging
    from aiohttp import web
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
