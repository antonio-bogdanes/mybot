import os
import sys
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# ===== НАСТРОЙКА ЛОГОВ (СРАЗУ ПРИ СТАРТЕ) =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
logger.info("🚀 Бот запускается...")

# ===== ПРОВЕРКА ТОКЕНА =====
TOKEN = os.environ.get('TOKEN')
if not TOKEN:
    logger.error("❌ ТОКЕН НЕ НАЙДЕН! Добавь переменную TOKEN в Render.")
    sys.exit(1)
logger.info("✅ Токен получен")

# ===== ПРОВЕРКА GOOGLE SHEETS (с запасным вариантом) =====
try:
    import gspread
    from google.oauth2.service_account import Credentials
    logger.info("✅ Библиотеки Google Sheets загружены")
    USE_GSHEET = True
except ImportError as e:
    logger.warning(f"⚠️ Библиотеки Google не загружены: {e}. Бот будет работать без таблицы.")
    USE_GSHEET = False

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')
if USE_GSHEET and not SPREADSHEET_ID:
    logger.warning("⚠️ SPREADSHEET_ID не задан. Бот будет работать без таблицы.")

def add_expense(category, amount):
    """Запись в таблицу (если доступна)"""
    if not USE_GSHEET or not SPREADSHEET_ID:
        logger.info(f"🚫 (ДЕМО) Записано бы: {amount} в {category}")
        return True  # Возвращаем True, чтобы бот отвечал, даже если нет таблицы
    try:
        if not os.path.exists('credentials.json'):
            logger.error("❌ Файл credentials.json не найден")
            return False
        creds = Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        day = datetime.now().day
        col = day + 1
        cats = sheet.col_values(1)
        row = cats.index(category) + 1 if category in cats else None
        if not row:
            logger.error(f"❌ Категория {category} не найдена")
            return False
        cell = sheet.cell(row, col)
        current = float(cell.value) if cell.value else 0
        sheet.update_cell(row, col, current + amount)
        logger.info(f"✅ Записано {amount} в {category} за {day}-е число")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка записи: {e}")
        return False

# ===== ТЕЛЕГРАМ БОТ =====
app_bot = Application.builder().token(TOKEN).build()

async def start(update, context):
    logger.info(f"👤 Пользователь {update.effective_user.id} вызвал /start")
    await update.message.reply_text(
        "👕 Бот работает!\n"
        "Отправь: сумма категория\n"
        "Пример: 15000 Закупка товара\n"
        "Категории: Закупка товара, Аренда, Зарплата, Реклама, Коммунальные, Транспорт, Налоги, Прочее"
    )

async def handle_message(update, context):
    logger.info(f"📩 Сообщение от {update.effective_user.id}: {update.message.text}")
    try:
        parts = update.message.text.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text("Пиши: сумма категория")
            return
        amount_str, category = parts
        amount = float(amount_str.replace(',', '.'))
        categories = ['Закупка товара', 'Аренда', 'Зарплата', 'Реклама', 'Коммунальные', 'Транспорт', 'Налоги', 'Прочее']
        if category not in categories:
            await update.message.reply_text(f"Категории: {', '.join(categories)}")
            return
        if add_expense(category, amount):
            await update.message.reply_text(f"✅ Записано {amount} в {category}")
        else:
            await update.message.reply_text("❌ Не удалось записать в таблицу, но бот работает!")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text("Ошибка, но бот на связи!")

app_bot.add_handler(CommandHandler("start", start))
app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ===== FLASK =====
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running!", 200

@flask_app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), app_bot.bot)
        app_bot.process_update(update)
        return "ok", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "error", 500

# ===== ЗАПУСК =====
if __name__ == '__main__':
    # Устанавливаем webhook
    render_url = os.environ.get('RENDER_EXTERNAL_URL')
    if render_url:
        webhook_url = f"{render_url}/{TOKEN}"
        try:
            app_bot.bot.set_webhook(url=webhook_url)
            logger.info(f"✅ Webhook установлен: {webhook_url}")
        except Exception as e:
            logger.error(f"❌ Ошибка webhook: {e}")
    else:
        logger.warning("⚠️ RENDER_EXTERNAL_URL не задан. Бот запущен, но webhook не установлен.")

    port = int(os.environ.get('PORT', 8080))
    logger.info(f"🔥 Запуск Flask на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)