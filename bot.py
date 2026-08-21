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
logger.info("🚀 Бот с отчётами запускается...")

TOKEN = os.environ.get('TOKEN')
if not TOKEN:
    logger.error("❌ TOKEN не задан")
    sys.exit(1)

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

user_data = {}
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

def get_today_summary(sheet):
    """Возвращает словарь {категория: сумма} для сегодняшнего дня и общий итог."""
    try:
        day = datetime.now().day
        col = day + 1
        # Берём весь столбец
        col_values = sheet.col_values(col)
        # Берём категории из первого столбца
        categories = get_categories_from_sheet(sheet)
        summary = {}
        total = 0
        # Предполагаем, что строки начинаются с 2 (первая — заголовок)
        # и соответствуют порядку категорий
        for i, cat in enumerate(categories):
            # строка = i + 2 (т.к. категории начинаются со 2-й строки)
            row_idx = i + 2
            if row_idx < len(col_values):
                val = col_values[row_idx - 1]  # т.к. col_values индексируется с 0
            else:
                val = 0
            # Парсим число
            try:
                num = float(val) if val and str(val).replace('.', '').isdigit() else 0
            except:
                num = 0
            if num != 0:
                summary[cat] = num
                total += num
        return summary, total
    except Exception as e:
        logger.error(f"Ошибка получения итога: {e}")
        return None, None

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
    summary, total = get_today_summary(sheet)
    if total is None:
        return False
    return total > 0

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
                             f"Нажми на категорию, затем введи сумму.",
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
                                 "👕 Бот с кнопками и отчётами.\n"
                                 "📌 **Как использовать:**\n"
                                 "1️⃣ Нажми на кнопку с категорией\n"
                                 "2️⃣ Введи сумму (только число)\n"
                                 "Команды:\n"
                                 "/today — отчёт за сегодня\n"
                                 f"⏰ Напоминания с {REMINDER_START_HOUR:02d}:{REMINDER_START_MINUTE:02d}",
                                 reply_markup=keyboard.to_dict())
                else:
                    send_message(chat_id, "Не удалось загрузить категории.")
                if not reminder_active:
                    reminder_active = True
                    reminding_in_progress = False
                    reminder_thread = threading.Thread(target=reminder_worker, args=(chat_id,), daemon=True)
                    reminder_thread.start()
                return "ok", 200

            if text.startswith('/today'):
                # Отчёт за сегодня
                creds = get_creds()
                if not creds:
                    send_message(chat_id, "Не могу подключиться к таблице.")
                    return "ok", 200
                try:
                    creds.refresh(Request())
                    client = gspread.authorize(creds)
                    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                    summary, total = get_today_summary(sheet)
                    if summary is None or total is None:
                        send_message(chat_id, "Ошибка при чтении данных.")
                        return "ok", 200
                    if total == 0:
                        send_message(chat_id, "📊 За сегодня расходов нет.")
                    else:
                        # Формируем красивое сообщение
                        lines = ["📊 **Итоги за сегодня:**"]
                        for cat, amount in summary.items():
                            lines.append(f"• {cat}: {amount:.2f}")
                        lines.append(f"---\n**Общий итог:** {total:.2f}")
                        send_message(chat_id, "\n".join(lines))
                except Exception as e:
                    send_message(chat_id, f"Ошибка: {e}")
                return "ok", 200

            if text.startswith('/categories'):
                keyboard = get_category_keyboard()
                if keyboard:
                    send_message(chat_id, "📋 Кнопки с категориями обновлены.", reply_markup=keyboard.to_dict())
                else:
                    send_message(chat_id, "Не удалось загрузить категории.")
                return "ok", 200

            # --- ОБРАБОТКА КАТЕГОРИИ ---
            creds = get_creds()
            if creds:
                try:
                    creds.refresh(Request())
                    client = gspread.authorize(creds)
                    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                    categories = get_categories_from_sheet(sheet)
                    if text in categories:
                        user_data[chat_id] = {'category': text, 'waiting_for_amount': True}
                        send_message(chat_id, f"💰 Категория «{text}» выбрана. Введи сумму (только число):")
                        return "ok", 200
                except:
                    pass

            # --- ОБРАБОТКА СУММЫ (ожидание) ---
            if chat_id in user_data and user_data[chat_id].get('waiting_for_amount'):
                try:
                    amount = float(text.replace(',', '.'))
                    category = user_data[chat_id]['category']
                    success, msg = add_expense(category, amount)
                    if success:
                        if reminding_in_progress:
                            reminding_in_progress = False
                        send_message(chat_id, f"✅ {msg}")
                    else:
                        send_message(chat_id, f"❌ {msg}")
                    del user_data[chat_id]
                    return "ok", 200
                except ValueError:
                    send_message(chat_id, "❌ Нужно ввести число. Попробуй ещё раз:")
                    return "ok", 200

            # --- РУЧНОЙ ВВОД (Категория Сумма или Сумма Категория) ---
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                first, second = parts
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

            send_message(chat_id, 
                         "❗ Не понял.\n"
                         "Используй кнопки для выбора категории, затем введи сумму.\n"
                         "Или напиши в формате: `Категория Сумма` (например, `Транспорт 600`).\n"
                         "Команды: /today — отчёт за сегодня")
        return "ok", 200
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Запуск на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)
