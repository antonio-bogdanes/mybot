import os
import sys
import json
import logging
import requests
import threading
import time
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from datetime import datetime

# ===== НАСТРОЙКИ =====
REMINDER_START_HOUR = 20
REMINDER_START_MINUTE = 0
REMIND_INTERVAL_MINUTES = 30

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("🚀 Бот с кнопками-префиксами запускается...")

TOKEN = os.environ.get('TOKEN')
if not TOKEN:
    logger.error("❌ TOKEN не задан")
    sys.exit(1)

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

reminder_active = False
reminder_thread = None
reminding_in_progress = False

# ===== ФУНКЦИИ GOOGLE =====
def get_creds():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if creds_json:
        try:
            creds_dict = json.loads(creds_json)
            return Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        except Exception as e:
            logger.error(f"Ошибка парсинга GOOGLE_CREDENTIALS: {e}")
    if os.path.exists('credentials.json'):
        try:
            with open('credentials.json', 'r') as f:
                creds_dict = json.load(f)
            return Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        except Exception as e:
            logger.error(f"Ошибка файла: {e}")
    logger.error("❌ Не найдены учётные данные")
    return None

def get_categories_from_sheet(sheet):
    try:
        all_vals = sheet.col_values(1)
        if len(all_vals) > 1:
            return [cat.strip() for cat in all_vals[1:] if cat.strip()]
        else:
            return []
    except Exception as e:
        logger.error(f"Ошибка чтения категорий: {e}")
        return []

def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=5)
        if resp.status_code != 200:
            logger.error(f"Ошибка отправки: {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def has_today_expenses(sheet):
    try:
        day = datetime.now().day
        col = day + 1
        all_values = sheet.col_values(col)
        if len(all_values) < 2:
            return False
        for val in all_values[1:]:
            if val and str(val).replace('.', '').isdigit():
                if float(val) > 0:
                    return True
        return False
    except Exception as e:
        logger.error(f"Ошибка проверки расходов: {e}")
        return False

def add_expense(category, amount):
    creds = get_creds()
    if not creds:
        return False, "Нет учётных данных"
    try:
        creds.refresh(Request())
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        logger.info(f"✅ Таблица открыта: {sheet.title}")

        categories = get_categories_from_sheet(sheet)
        if not categories:
            return False, "В таблице нет категорий."

        if category not in categories:
            cats_str = ", ".join(categories)
            return False, f"Категория '{category}' не найдена. Доступные: {cats_str}"

        day = datetime.now().day
        col = day + 1
        row = categories.index(category) + 2

        cell = sheet.cell(row, col)
        current = float(cell.value) if cell.value and str(cell.value).replace('.', '').isdigit() else 0
        new_value = current + amount
        sheet.update_cell(row, col, new_value)
        logger.info(f"✅ Ячейка {chr(64+col)}{row} обновлена на {new_value}")

        return True, f"Записано {amount} в {category} (ячейка {chr(64+col)}{row})"
    except Exception as e:
        logger.error(f"Ошибка записи: {e}")
        return False, f"Ошибка: {str(e)}"

def get_category_keyboard():
    """Создаёт клавиатуру с категориями (каждая кнопка — название категории)."""
    creds = get_creds()
    if not creds:
        return None
    try:
        creds.refresh(Request())
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        categories = get_categories_from_sheet(sheet)
        if not categories:
            return None
        keyboard = []
        row = []
        for i, cat in enumerate(categories):
            row.append(KeyboardButton(cat))
            if (i + 1) % 2 == 0:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    except Exception as e:
        logger.error(f"Ошибка создания клавиатуры: {e}")
        return None

# ===== НАПОМИНАНИЯ =====
def reminder_worker(chat_id):
    global reminding_in_progress
    logger.info("🧠 Поток напоминаний запущен")
    while reminder_active:
        now = datetime.now()
        if now.hour < REMINDER_START_HOUR or (now.hour == REMINDER_START_HOUR and now.minute < REMINDER_START_MINUTE):
            time.sleep(600)
            continue
        creds = get_creds()
        if not creds:
            time.sleep(600)
            continue
        try:
            creds.refresh(Request())
            client = gspread.authorize(creds)
            sheet = client.open_by_key(SPREADSHEET_ID).sheet1
            if has_today_expenses(sheet):
                if reminding_in_progress:
                    reminding_in_progress = False
                time.sleep(3600)
                continue
            else:
                if not reminding_in_progress:
                    reminding_in_progress = True
                keyboard = get_category_keyboard()
                send_message(chat_id, 
                             f"⚠️ **НАПОМИНАНИЕ!** Уже {now.strftime('%H:%M')}, а ты ещё не записал расходы.\n"
                             f"Нажми на категорию, затем введи сумму.\nПример: Транспорт 600",
                             reply_markup=keyboard.to_dict() if keyboard else None)
                time.sleep(REMIND_INTERVAL_MINUTES * 60)
        except Exception as e:
            logger.error(f"Ошибка в цикле напоминаний: {e}")
            time.sleep(600)

# ===== FLASK =====
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running!", 200

@flask_app.route('/', methods=['POST'])
def webhook():
    global reminder_active, reminder_thread, reminding_in_progress
    try:
        data = request.get_json(force=True)
        if not data:
            return "ok", 200
        update = Update.de_json(data, None)
        if update.message and update.message.text:
            chat_id = update.message.chat.id
            text = update.message.text.strip()
            logger.info(f"Сообщение от {chat_id}: {text}")

            # --- КОМАНДЫ ---
            if text.startswith('/start'):
                keyboard = get_category_keyboard()
                if keyboard:
                    send_message(chat_id, 
                                 "👕 Бот с кнопками-префиксами.\n"
                                 "📌 **Как использовать:**\n"
                                 "1️⃣ Нажми на кнопку с категорией (она появится в поле ввода)\n"
                                 "2️⃣ Допиши сумму (например, `600`)\n"
                                 "3️⃣ Отправь сообщение — расход записан!\n\n"
                                 f"⏰ Каждый день в {REMINDER_START_HOUR:02d}:{REMINDER_START_MINUTE:02d} я буду напоминать о расходах.",
                                 reply_markup=keyboard.to_dict())
                else:
                    send_message(chat_id, "Не удалось загрузить категории. Проверь таблицу.")
                if not reminder_active:
                    reminder_active = True
                    reminding_in_progress = False
                    reminder_thread = threading.Thread(target=reminder_worker, args=(chat_id,), daemon=True)
                    reminder_thread.start()
                return "ok", 200

            if text.startswith('/categories'):
                keyboard = get_category_keyboard()
                if keyboard:
                    send_message(chat_id, "📋 Кнопки с категориями обновлены.", reply_markup=keyboard.to_dict())
                else:
                    send_message(chat_id, "Не удалось загрузить категории.")
                return "ok", 200

            # --- ОБРАБОТКА РАСХОДА (формат: "Категория Сумма" или "Сумма Категория") ---
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                first, second = parts[0], parts[1]
                
                # Пробуем первый вариант: "Категория Сумма"
                try:
                    amount = float(second.replace(',', '.'))
                    category = first
                    success, msg = add_expense(category, amount)
                    if success:
                        if reminding_in_progress:
                            reminding_in_progress = False
                        send_message(chat_id, f"✅ {msg}")
                    else:
                        send_message(chat_id, f"❌ {msg}")
                    return "ok", 200
                except ValueError:
                    pass
                
                # Пробуем второй вариант: "Сумма Категория"
                try:
                    amount = float(first.replace(',', '.'))
                    category = second
                    success, msg = add_expense(category, amount)
                    if success:
                        if reminding_in_progress:
                            reminding_in_progress = False
                        send_message(chat_id, f"✅ {msg}")
                    else:
                        send_message(chat_id, f"❌ {msg}")
                    return "ok", 200
                except ValueError:
                    pass
            
            # Если не распарсилось — ошибка
            send_message(chat_id, 
                         "❗ Не понял формат.\n"
                         "Используй: **Категория Сумма** или **Сумма Категория**\n"
                         "Например: `Транспорт 600` или `600 Транспорт`")
        return "ok", 200
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Запуск на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)
