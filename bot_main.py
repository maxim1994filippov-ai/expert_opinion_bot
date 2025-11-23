# bot_main.py
import os
import asyncio
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
from web_automation_playwright import PlaywrightExpertBot
import users_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLIT_PREVIEW_URL = os.getenv("REPLIT_PREVIEW_URL", "https://<your-repl>.id.repl.co/")

# Conversation states for adding/editing account
EMAIL, PASSWORD = range(2)

_runner_task = {}
_runner_lock = asyncio.Lock()
_playbots = {}           # chat_id -> PlaywrightExpertBot instance
_captcha_waiters = {}    # chat_id -> asyncio.Event

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    kb = [
        [InlineKeyboardButton("🔍 Найти опросы", callback_data="find")],
        [InlineKeyboardButton("▶ Начать опросы (авто)", callback_data="start_all")],
        [InlineKeyboardButton("📊 Отчёт", callback_data="report")],
        [InlineKeyboardButton("⚙ Аккаунт", callback_data="account_menu")]
    ]
    await update.message.reply_text("Меню бота:", reply_markup=InlineKeyboardMarkup(kb))

# ----- Account management flows -----
async def account_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    kb = [
        [InlineKeyboardButton("➕ Добавить / Изменить аккаунт", callback_data="add_account")],
        [InlineKeyboardButton("❌ Удалить аккаунт", callback_data="delete_account")],
        [InlineKeyboardButton("⬅ Назад", callback_data="back_main")]
    ]
    await query.edit_message_text("Меню аккаунта:", reply_markup=InlineKeyboardMarkup(kb))

async def add_account_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введите email для аккаунта (сообщением):")
    return EMAIL

async def recv_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.user_data["new_email"] = update.message.text.strip()
    await update.message.reply_text("Теперь введите пароль (сообщением):", reply_markup=ReplyKeyboardRemove())
    return PASSWORD

async def recv_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    password = update.message.text.strip()
    email = context.user_data.get("new_email")
    users_manager.add_or_update_user(chat_id, email, password)
    await update.message.reply_text("Аккаунт сохранён ✅\nТеперь можно в Меню нажать ▶ Начать опросы (авто).")
    return ConversationHandler.END

async def cancel_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def delete_account_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    users_manager.remove_user(chat_id)
    await query.edit_message_text("Аккаунт удалён (если он был).")

# ----- Core bot callbacks -----
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    app = context.application

    if data == "back_main":
        await query.edit_message_text("Возврат в главное меню. Нажми /start", reply_markup=None)
        return

    if data == "report":
        if not users_manager.has_credentials(chat_id):
            await app.bot.send_message(chat_id=chat_id, text="У тебя ещё нет аккаунта. Добавь через Меню → Аккаунт.")
            return
        s = users_manager.summary(chat_id)
        text = f"📊 Статистика\n\nВсего опросов: {s['total_surveys']}\nЗаработано баллов: {s['total_points']}\n\nПоследние {len(s['last5'])}:\n"
        for r in s['last5']:
            text += f"• {r['points']} баллов — \"{r['title']}\" ({r['date']})\n"
        await app.bot.send_message(chat_id=chat_id, text=text)
        return

    if data == "find":
        if not users_manager.has_credentials(chat_id):
            await app.bot.send_message(chat_id=chat_id, text="Сначала добавь аккаунт в Меню → Аккаунт.")
            return
        await query.edit_message_text("Ищу опросы... Подождите.")
        u = users_manager.get_user(chat_id)
        pb = PlaywrightExpertBot(u["email"], u["password"], headless=True)
        await pb.start()
        ok = await pb.login()
        if not ok:
            await app.bot.send_message(chat_id=chat_id, text="Ошибка входа — проверь email/password в разделе Аккаунт.")
            await pb.stop()
            return
        surveys = await pb.get_available_surveys()
        await pb.stop()
        if not surveys:
            await app.bot.send_message(chat_id=chat_id, text="Не найдено доступных опросов.")
            return
        text = "Найденные опросы:\n"
        for i, s in enumerate(surveys, start=1):
            text += f"{i}. {s['title']} — {s['points']} баллов\n"
        await app.bot.send_message(chat_id=chat_id, text=text)
        return

    if data == "start_all":
        if not users_manager.has_credentials(chat_id):
            await app.bot.send_message(chat_id=chat_id, text="Сначала добавь аккаунт в Меню → Аккаунт.")
            return
        async with _runner_lock:
            if chat_id in _runner_task and not _runner_task[chat_id].done():
                await app.bot.send_message(chat_id=chat_id, text="Задача уже выполняется.")
                return
            u = users_manager.get_user(chat_id)
            pb = PlaywrightExpertBot(u["email"], u["password"], headless=True)
            task = asyncio.create_task(_runner_auto(chat_id, app, pb))
            _runner_task[chat_id] = task
            await app.bot.send_message(chat_id=chat_id, text="Запуск автоматического прохождения опросов...")
        return

    if data == "open_preview":
        await app.bot.send_message(chat_id=chat_id, text=f"Открой превью Replit и нажми капчу:\n{REPLIT_PREVIEW_URL}")
        return

    if data == "captcha_done":
        ev = _captcha_waiters.get(chat_id)
        if ev:
            ev.set()
            await app.bot.send_message(chat_id=chat_id, text="Принял — продолжаю выполнение.")
        else:
            await app.bot.send_message(chat_id=chat_id, text="Нет ожидающей операции.")
        return

    if data == "cancel":
        async with _runner_lock:
            t = _runner_task.get(chat_id)
            if t and not t.done():
                t.cancel()
                await app.bot.send_message(chat_id=chat_id, text="Остановил текущую задачу.")
            else:
                await app.bot.send_message(chat_id=chat_id, text="Нечего останавливать.")
        return

# ----- Runner that processes all surveys for a given user -----
async def _runner_auto(chat_id: int, app, pb: PlaywrightExpertBot):
    try:
        await pb.start()
        ok = await pb.login()
        if not ok:
            await app.bot.send_message(chat_id=chat_id, text="Не удалось войти (проверь учётные данные).")
            await pb.stop()
            return

        surveys = await pb.get_available_surveys()
        if not surveys:
            await app.bot.send_message(chat_id=chat_id, text="Опросы не найдены.")
            await pb.stop()
            return

        await app.bot.send_message(chat_id=chat_id, text=f"Найдено {len(surveys)} опросов. Начинаю проходить...")

        for s in surveys:
            title = s.get("title", "Опрос")
            points = s.get("points", 0)
            await app.bot.send_message(chat_id=chat_id, text=f"→ Открываю: {title} ({points} баллов)")
            ok_click = await pb.open_survey_by_xpath(s["button_xpath"])
            if not ok_click:
                await app.bot.send_message(chat_id=chat_id, text=f"Ошибка при открытии опроса: {title}. Пропускаю.")
                continue

            await asyncio.sleep(2)
            has_captcha = await pb.check_captcha()
            if has_captcha:
                screenshot_path = f"captcha_{chat_id}.png"
                await pb.screenshot_captcha(screenshot_path)
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Открыть окно браузера", callback_data="open_preview")],
                    [InlineKeyboardButton("👍 Я нажал капчу", callback_data="captcha_done"),
                     InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
                ])
                with open(screenshot_path, "rb") as fh:
                    await app.bot.send_photo(chat_id=chat_id, photo=fh, caption="Появилась капча. Нажми её в Replit Preview, затем жми «Я нажал капчу».", reply_markup=kb)

                ev = asyncio.Event()
                _captcha_waiters[chat_id] = ev
                try:
                    await asyncio.wait_for(ev.wait(), timeout=600)
                except asyncio.TimeoutError:
                    await app.bot.send_message(chat_id=chat_id, text="Таймаут ожидания капчи — пропускаю опрос.")
                    _captcha_waiters.pop(chat_id, None)
                    continue
                finally:
                    _captcha_waiters.pop(chat_id, None)

                await pb.continue_after_captcha()
                users_manager.add_record(chat_id, title, points)
                await app.bot.send_message(chat_id=chat_id, text=f"Опрос \"{title}\" помечен как пройден ({points} баллов).")
            else:
                # TODO: integrate ai_survey_solver to auto-fill
                users_manager.add_record(chat_id, title, points)
                await app.bot.send_message(chat_id=chat_id, text=f"Опрос \"{title}\" пройден автоматически ({points} баллов).")

        await app.bot.send_message(chat_id=chat_id, text="Обработка опросов завершена.")
    except asyncio.CancelledError:
        await app.bot.send_message(chat_id=chat_id, text="Задача была отменена.")
    except Exception as e:
        logging.exception("Runner error: %s", e)
        await app.bot.send_message(chat_id=chat_id, text=f"Ошибка: {e}")
    finally:
        try:
            await pb.stop()
        except:
            pass

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Basic handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Conversation for add account
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_account_start_cb, pattern="^add_account$")],
        states={
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_email)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_password)]
        },
        fallbacks=[CommandHandler("cancel", cancel_account)],
        allow_reentry=True
    )
    app.add_handler(conv)

    # Quick account menu callbacks
    app.add_handler(CallbackQueryHandler(account_menu_cb, pattern="^account_menu$"))
    app.add_handler(CallbackQueryHandler(delete_account_cb, pattern="^delete_account$"))

    logging.info("Starting Telegram bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
