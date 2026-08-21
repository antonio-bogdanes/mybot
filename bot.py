import os
import sys
import json
import logging
import requests
import threading
import time
from flask import Flask, request
from telegram import Update
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from datetime import datetime, date

# ===== НАСТРОЙКИ =====
REMINDER_START_HOUR = 20      # Час начала (0-23)
REMINDER_START_MINUTE = 0     # Минута
REMIND_INTERVAL_MINUTES = 30  # Интервал между напоминаниями (минуты)
REMINDER_ENABLED = True

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("🚀 Бот с вечерним заёбыванием запускается...")

TOKEN = os.environ.get('TOKEN')
if not TOKEN:
    logger.error("❌ TOKEN не задан")
    sys.exit(1)

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

# ===== ПЕРЕМЕННЫЕ СОСТОЯНИЯ =====
reminder_active = False
reminder_thread = None
last_remind_date = None        # дата, когда в последний раз было отправлено напоминание (используем для сброса на следующий день)
reminding_in_progress = False   # идёт ли сейчас процесс заёбывания (после 20:00)

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

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=5)
        if resp.status_code != 200:
            logger.error(f"Ошибка отправки: {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def has_today_expenses(sheet):
    """Проверяет, есть ли расходы за сегодня (любая категория > 0)."""
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

# ===== ФУНКЦИЯ ФОНОВОГО ПОТОКА (ЗАЁБЫВАНИЕ) =====
def reminder_worker(chat_id):
    global reminding_in_progress, last_remind_date
    logger.info("🧠 Поток напоминаний запущен")
    while reminder_active:
        now = datetime.now()
        today = now.date()

        # Если сегодня уже была запись расхода, то reminding_in_progress = False, и мы не спамим
        # Но мы будем проверять это каждый цикл

        # Если время ещё не наступило (до REMINDER_START_HOUR) – спим
        if now.hour < REMINDER_START_HOUR or (now.hour == REMINDER_START_HOUR and now.minute < REMINDER_START_MINUTE):
            # До начала напоминаний ждём 10 минут
            time.sleep(600)
            continue

        # Если время наступило, проверяем, были ли расходы сегодня
        creds = get_creds()
        if not creds:
            logger.warning("Нет учётных данных, ждём...")
            time.sleep(600)
            continue
        try:
            creds.refresh(Request())
            client = gspread.authorize(creds)
            sheet = client.open_by_key(SPREADSHEET_ID).sheet1
            if has_today_expenses(sheet):
                # Расходы есть – отключаем заёбывание до следующего дня
                if reminding_in_progress:
                    logger.info("✅ Расходы за сегодня уже есть, заёбывание отключено до завтра.")
                    reminding_in_progress = False
                # Ждём до полуночи (или до следующей проверки, но лучше спать дольше)
                # будем проверять раз в час, не наступил ли новый день
                time.sleep(3600)
                continue
            else:
                # Расходов нет – начинаем заёбывать, если ещё не начали
                if not reminding_in_progress:
                    logger.info("⏰ Начало заёбывания (расходов нет)")
                    reminding_in_progress = True
                # Отправляем напоминание
                send_message(chat_id, f"⚠️ **НАПОМИНАНИЕ!** Уже {now.strftime('%H:%M')}, а ты ещё не записал расходы.\nОтправь: сумма категория\nПример: 15000 Закупка товара")
                logger.info("📩 Отправлено напоминание")
                # Ждём интервал перед следующим напоминанием
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
            text = update.message.text
            logger.info(f"Сообщение от {chat_id}: {text}")

            if text.startswith('/start'):
                send_message(chat_id, "👕 Бот учёта расходов с вечерним заёбыванием.\n"
                                      f"Отправь: сумма категория\nПример: 15000 Закупка товара\n"
                                      f"Каждый день с {REMINDER_START_HOUR:02d}:{REMINDER_START_MINUTE:02d} я буду напоминать каждые {REMIND_INTERVAL_MINUTES} минут, пока не запишешь расходы.")
                if not reminder_active:
                    reminder_active = True
                    reminding_in_progress = False
                    reminder_thread = threading.Thread(target=reminder_worker, args=(chat_id,), daemon=True)
                    reminder_thread.start()
                    logger.info("✅ Напоминания активированы")
                return "ok", 200

            if text.startswith('/categories'):
                creds = get_creds()
                if not creds:
                    send_message(chat_id, "Не могу подключиться к таблице.")
                    return "ok", 200
                try:
                    creds.refresh(Request())
                    client = gspread.authorize(creds)
                    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                    cats = get_categories_from_sheet(sheet)
                    if cats:
                        send_message(chat_id, f"📋 Категории в таблице:\n{', '.join(cats)}")
                    else:
                        send_message(chat_id, "В таблице нет категорий.")
                except Exception as e:
                    send_message(chat_id, f"Ошибка: {e}")
                return "ok", 200

            # Обработка расхода
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "Пиши: сумма категория")
                return "ok", 200
            amount_str, category = parts
            try:
                amount = float(amount_str.replace(',', '.'))
            except:
                send_message(chat_id, "Сумма должна быть числом")
                return "ok", 200

            success, msg = add_expense(category, amount)
            send_message(chat_id, f"{'✅' if success else '❌'} {msg}")
            if success:
                # Расход записан — если мы сейчас в режиме заёбывания, отключаем его до завтра
                if reminding_in_progress:
                    reminding_in_progress = False
                    logger.info("⏹️ Заёбывание остановлено (расход записан)")
        return "ok", 200
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Запуск на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)
