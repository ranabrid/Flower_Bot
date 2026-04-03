# handlers.py

from datetime import datetime, timedelta
import pytz
import logging
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    CommandHandler,
    filters,
)
from database import (
    get_interval,
    save_interval,
    save_user_date,
    get_user_date,
    delete_user_data,
    get_all_users_for_restore,
)
from scheduler import (
    send_first_reminder,
    send_cycle_reminder,
    restore_reminders,
    get_aware_datetime,
)
from constants import (
    STATUS_CHECK,
    AWAITING_ANSWER,
    CHOOSING_DATE,
    CALLBACK_PLAN,
    CALLBACK_LATER,
    CALLBACK_BOUGHT,
    CALLBACK_HOUR_LATER,
    CALLBACK_DAY_LATER,
    CALLBACK_SCHEDULE_YES,
    CALLBACK_SCHEDULE_NO,
    MOSCOW_TIMEZONE,
    MSG_WELCOME_START,
    MSG_PLAN_DATE,
    MSG_POSTPONED_MESSAGE,
    MSG_BOUGHT_SUCCESS,
    MSG_REMINDER_TEXT,
    MSG_DELIVERY_URL,
    MSG_INVALID_DATE_INPUT,
    MSG_REMINDER_SCHEDULED_SUCCESS,
    MSG_CANCEL_SUCCESS,
    MSG_REMINDER_ALREADY_ACTIVE,
    MSG_NO_ACTIVE_REMINDER,
    MSG_KEEP_REMINDER,
    MSG_DAY_REMINDER,
    MSG_HOUR_REMINDER,
    MSG_OLD_DATE,
    MSG_ASK_INTERVAL,
)

import dateparser
import constants

moscow_tz = pytz.timezone(MOSCOW_TIMEZONE)
logger = logging.getLogger(__name__)
DAYS = ("день", "дня", "дней")


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """
    forms = (день, дня, дней)
    """
    if 11 <= n % 100 <= 14:
        return forms[2]
    last = n % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def format_days(n: int) -> str:
    return f"{n} {plural(n, DAYS)}"


# !!! НОВАЯ функция с интервалами !!!


def interval_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Раз в неделю", callback_data="int_7")],
            [InlineKeyboardButton("Раз в 2 недели", callback_data="int_14")],
            [InlineKeyboardButton("Раз в 3 недели", callback_data="int_21")],
            [InlineKeyboardButton("Раз в месяц", callback_data="int_30")],
            [InlineKeyboardButton("Свой вариант", callback_data="int_custom")],
        ]
    )


# Обработчик выбора интервала


async def choose_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    data = query.data  # int_7, int_14...

    if data == "int_custom":
        await query.edit_message_text(MSG_ASK_INTERVAL)
        return constants.CUSTOM_INTERVAL

    days = int(data.split("_")[1])

    save_interval(chat_id, days)

    await query.edit_message_text(f"Ок, буду напоминать раз в {format_days(days)} 👌")

    return ConversationHandler.END


# ОБРАБОТЧИК ВВОДА ЧИСЛА ДЛЯ ПЕРИОДИЧНОСТИ
async def handle_custom_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    try:
        days = int(text)

        if days <= 0:
            raise ValueError
        if days > 365:
            await update.message.reply_text("Давай что-то поменьше 🙂 (до 365 дней)")
            return constants.CUSTOM_INTERVAL
        save_interval(chat_id, days)

        await update.message.reply_text(
            f"Ок, буду напоминать раз в {format_days(days)} 👌"
        )

        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("Введите число дней, например: 10")
        return constants.CUSTOM_INTERVAL


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start. Планирует отправку приветственного сообщения."""
    chat_id = update.effective_chat.id

    context.application.job_queue.run_once(
        send_first_reminder,  # в scheduler.py
        when=datetime.now(moscow_tz),
        chat_id=chat_id,
        name=f"first_{chat_id}",
    )

    return ConversationHandler.END


async def schedule_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    try:
        await query.edit_message_reply_markup(reply_markup=None)  # Убираем клавиатуру
        await query.edit_message_text(text=MSG_PLAN_DATE)
    except Exception as e:
        logger.warning(
            f"Could not edit message in schedule_yes for {query.message.chat_id}: {e}"
        )
        await query.message.reply_text(MSG_PLAN_DATE)

    return CHOOSING_DATE


async def postpone_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для кнопки 'В другой раз'. Предлагает воспользоваться /start позже."""
    query = update.callback_query
    await query.answer()

    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text(text=MSG_POSTPONED_MESSAGE)
    except Exception as e:
        logger.warning(f"Could not edit message in postpone_reminder: {e}")

    return ConversationHandler.END


async def schedule_no(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text(text=MSG_KEEP_REMINDER)
    except Exception as e:
        logger.warning(
            f"Could not edit message in schedule_no for {query.message.chat_id}: {e}"
        )

    return ConversationHandler.END


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на инлайн-кнопки."""
    query = update.callback_query

    try:
        await query.answer()  # ОТВЕЧАЕМ Боту: "Да, я получил нажатие"
    except Exception as e:
        # Если ответ не ушел, логируем ошибку, чтобы бот не завис
        logger.error(f"Не удалось ответить на нажатие кнопки: {e}")
        return ConversationHandler.END  # Завершаем диалог, чтобы не было зависаний
        # --- НОВАЯ ПРОВЕРКА ---
    # Если query или query.message равно None, мы не сможем ничего сделать.
    # Это может быть причиной "загрузки" и отсутствия ответа.
    if not query or not query.message:
        logger.error("Ошибка в button_click: query или query.message равен None.")
        return ConversationHandler.END
    # --- КОНЕЦ ПРОВЕРКИ ---
    await query.answer()
    chat_id = query.message.chat_id
    job_queue = context.job_queue

    if query.data == CALLBACK_PLAN:
        await query.edit_message_text(text=MSG_PLAN_DATE)
        return constants.CHOOSING_DATE  # Переходим в состояние ожидания ввода даты

    elif query.data == CALLBACK_LATER:
        await query.edit_message_text(MSG_POSTPONED_MESSAGE)
        return ConversationHandler.END

    elif query.data == CALLBACK_BOUGHT:
        # Пользователь купил цветы. Планируем следующее напоминание через 21 день.

        interval = get_interval(chat_id)

        if interval is None:
            logger.error(f"Interval is missing for {chat_id}, using fallback 7 days")
            interval = 7

        interval = int(interval)

        next_remind_date = datetime.now(moscow_tz) + timedelta(days=interval)
        save_user_date(chat_id, next_remind_date.isoformat())

        new_job_name = f"cycle_{chat_id}"

        existing_jobs = job_queue.get_jobs_by_name(new_job_name)
        if existing_jobs:
            for existing_job in existing_jobs:
                existing_job.schedule_removal()
                logger.info(f"Removed existing job {new_job_name} before rescheduling.")
        job_queue.run_once(
            send_cycle_reminder,
            when=next_remind_date,
            chat_id=chat_id,
            name=new_job_name,
        )
        await query.edit_message_text(MSG_BOUGHT_SUCCESS)
        logger.info(
            f"Scheduled next reminder for {chat_id} after purchase, in {interval} days."
        )
        return ConversationHandler.END

    elif query.data == CALLBACK_HOUR_LATER:
        # Напомнить через час
        next_remind_date = datetime.now(moscow_tz) + timedelta(hours=1)
        save_user_date(chat_id, next_remind_date.isoformat())

        new_job_name = f"cycle_{chat_id}"

        for job in job_queue.get_jobs_by_name(new_job_name):
            job.schedule_removal()

        job_queue.run_once(
            send_cycle_reminder,
            when=next_remind_date,
            chat_id=chat_id,
            name=new_job_name,
        )
        await query.edit_message_text(MSG_HOUR_REMINDER)
        logger.info(f"Rescheduled reminder for {chat_id} in 1 hour.")
        return ConversationHandler.END

    elif query.data == CALLBACK_DAY_LATER:
        # Напомнить завтра
        next_remind_date = datetime.now(moscow_tz) + timedelta(days=1)
        save_user_date(chat_id, next_remind_date.isoformat())

        new_job_name = f"cycle_{chat_id}"

        for job in job_queue.get_jobs_by_name(new_job_name):
            job.schedule_removal()

        job_queue.run_once(
            send_cycle_reminder,
            when=next_remind_date,
            chat_id=chat_id,
            name=new_job_name,
        )
        await query.edit_message_text(MSG_DAY_REMINDER)
        logger.info(f"Rescheduled reminder for {chat_id} for tomorrow.")
        return ConversationHandler.END


async def handle_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод даты от пользователя для настройки напоминания."""
    logger.info("handle_date_input triggered")
    user_input = update.message.text
    chat_id = update.effective_chat.id

    is_reschedule = get_user_date(chat_id) is not None
    logger.info(f"[DEBUG] is_reschedule={is_reschedule}")
    # --- УСИЛЕННАЯ ДИАГНОСТИКА ---
    if not hasattr(context, "job_queue") or context.job_queue is None:
        # Если очереди задач нет, покажем ошибку в логах и пользователю
        logger.error(
            "[CRITICAL ERROR] handle_date_input: context.job_queue is None! Cannot schedule task."
        )
        logger.error(f"[CRITICAL] Full context dump: {context}")

        await update.message.reply_text(
            "Произошла внутренняя ошибка планирования (1). Попробуйте еще раз."
        )
        return ConversationHandler.END

    # Если очередь есть, проверим, можем ли мы ее использовать
    try:
        # Попробуем получить список задач. Если упадет ошибка, значит очередь "мертвая"
        jobs = context.job_queue.jobs()
        logger.info("[DEBUG] handle_date_input: JobQueue is active and accessible.")
    except Exception as e:
        logger.error(f"[CRITICAL ERROR] handle_date_input: Cannot access JobQueue: {e}")
        await update.message.reply_text(
            "Произошла внутренняя ошибка планирования (2). Попробуйте еще раз."
        )
        return ConversationHandler.END
    # --- КОНЕЦ ДИАГНОСТИКИ ---
    # --- НОВАЯ ДИАГНОСТИКА ---
    if context.job_queue is None:
        logger.error(
            "ERROR: JobQueue is None in handle_date_input! Cannot schedule task."
        )
        await update.message.reply_text(
            "Произошла внутренняя ошибка планирования. Попробуйте еще раз."
        )
        return ConversationHandler.END
    # --- КОНЕЦ ДИАГНОСТИКИ ---
    job_queue = context.application.job_queue  # Используем очередь приложения

    cycle_job_name = f"cycle_{chat_id}"
    for job in job_queue.get_jobs_by_name(cycle_job_name):
        job.schedule_removal()
        logger.info(
            f"handle_date_input: Removed existing cycle job '{cycle_job_name}' to prevent duplicates."
        )

    parsed_date = dateparser.parse(
        user_input,
        settings={"TIMEZONE": MOSCOW_TIMEZONE, "PREFER_DATES_FROM": "future"},
    )
    now_aware = datetime.now(moscow_tz)

    if not parsed_date:
        await update.message.reply_text(MSG_INVALID_DATE_INPUT)
        return CHOOSING_DATE

    parsed_date_aware = get_aware_datetime(parsed_date)

    # Проверка на время в прошлом
    if parsed_date_aware < now_aware:
        await update.message.reply_text(MSG_OLD_DATE)
        return CHOOSING_DATE

    logger.info(
        f"[DEBUG] Scheduling reminder for chat {chat_id} at {parsed_date_aware}. Job name will be cycle_{chat_id}."
    )
    context.job_queue.run_once(
        send_cycle_reminder,
        when=parsed_date_aware,
        chat_id=chat_id,
        name=f"cycle_{chat_id}",
    )
    logger.info(f"[DEBUG] Reminder scheduled successfully for chat {chat_id}.")

    save_user_date(chat_id, parsed_date_aware.isoformat())

    formatted_date = parsed_date_aware.strftime("%d.%m.%Y в %H:%M")
    message_text = MSG_REMINDER_SCHEDULED_SUCCESS.format(date=formatted_date)
    await update.message.reply_text(message_text)

    # 🔍 Проверяем интервал
    interval = get_interval(chat_id)
    logger.info(f"[DEBUG] loaded interval={interval} for chat_id={chat_id}")
    if is_reschedule:
        return ConversationHandler.END

    # ❗ если интервала нет → спрашиваем
    await update.message.reply_text(MSG_ASK_INTERVAL, reply_markup=interval_keyboard())
    return constants.CHOOSING_INTERVAL


async def cancel_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет все запланированные напоминания для пользователя и очищает данные."""
    chat_id = update.effective_chat.id
    job_queue = context.job_queue

    # --- ИЗМЕНЕНИЕ: Ищем задачи по префиксу имени ---
    # Создаем список префиксов, которые используются для напоминаний
    prefixes = ["cycle_", "first_"]

    removed_count = 0
    for prefix in prefixes:
        # Ищем все задачи, имя которых начинается с нашего префикса и ID пользователя
        jobs_to_remove = job_queue.get_jobs_by_name(f"{prefix}{chat_id}")

        for job in jobs_to_remove:
            job.schedule_removal()  # Ставим задачу на удаление из очереди
            removed_count += 1
            logger.info(f"Removed job {job.name} by cancel_reminders command.")

    # Если ничего не нашли, логируем это
    if removed_count == 0:
        logger.info(f"No active reminder jobs found for user {chat_id} to cancel.")

    # Очищаем данные в БД (эта строка у вас уже была и она верная)
    delete_user_data(chat_id)

    await update.message.reply_text(MSG_CANCEL_SUCCESS)
    logger.info(f"All reminders cancelled for {chat_id}. Removed {removed_count} jobs.")


async def status_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет статус и запускает диалог ожидания ответа."""
    chat_id = update.effective_chat.id
    date_str = get_user_date(chat_id)
    now_aware = datetime.now(moscow_tz)

    # Проверяем, есть ли активное напоминание в будущем
    has_active_reminder = False
    if date_str:
        try:
            remind_date_aware = get_aware_datetime(datetime.fromisoformat(date_str))
            if remind_date_aware > now_aware:
                has_active_reminder = True
        except Exception as e:
            logger.error(f"Error parsing date in status_check: {e}")

    # Формируем клавиатуру и сообщение в зависимости от статуса
    if has_active_reminder:
        # 1. Берём дату
        saved_date = datetime.fromisoformat(date_str)
        saved_date = get_aware_datetime(saved_date)

        # 2. Форматируем
        formatted_date = saved_date.strftime("%d.%m в %H:%M")

        # 3. Текст
        message_text = MSG_REMINDER_ALREADY_ACTIVE.format(date=formatted_date)

        # 4. Клавиатура
        keyboard = [
            [
                InlineKeyboardButton(
                    "✏️Да, изменить", callback_data=constants.CALLBACK_SCHEDULE_YES
                )
            ],
            [
                InlineKeyboardButton(
                    "🔴Нет", callback_data=constants.CALLBACK_SCHEDULE_NO
                )
            ],
        ]

        await update.message.reply_text(
            message_text, reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return constants.AWAITING_ANSWER

    else:
        keyboard = [
            [
                InlineKeyboardButton(
                    "🌻Да, пожалуйста", callback_data=constants.CALLBACK_PLAN
                )
            ],
            [
                InlineKeyboardButton(
                    "❌В другой раз", callback_data=constants.CALLBACK_LATER
                )
            ],
        ]

        await update.message.reply_text(
            MSG_NO_ACTIVE_REMINDER, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return constants.STATUS_CHECK


def setup_bot(application):

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("status", status_check),
            CallbackQueryHandler(
                button_click, pattern="^" + constants.CALLBACK_PLAN + "$"
            ),
        ],
        states={
            constants.CHOOSING_DATE: [
                CommandHandler("status", status_check),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date_input),
                CommandHandler("cancel", cancel_reminders),
            ],
            constants.STATUS_CHECK: [
                CommandHandler("status", status_check),
                CallbackQueryHandler(
                    button_click, pattern="^" + constants.CALLBACK_PLAN + "$"
                ),
                CallbackQueryHandler(
                    postpone_reminder, pattern="^" + constants.CALLBACK_LATER + "$"
                ),
            ],
            constants.AWAITING_ANSWER: [
                CommandHandler("status", status_check),
                CallbackQueryHandler(
                    schedule_yes, pattern="^" + constants.CALLBACK_SCHEDULE_YES + "$"
                ),
                CallbackQueryHandler(
                    schedule_no, pattern="^" + constants.CALLBACK_SCHEDULE_NO + "$"
                ),
                CommandHandler("cancel", cancel_reminders),
            ],
            # !!! NEW !!!
            constants.CHOOSING_INTERVAL: [
                CommandHandler("status", status_check),
                CallbackQueryHandler(choose_interval, pattern="^int_"),
            ],
            constants.CUSTOM_INTERVAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_interval)
            ],
        },
        fallbacks=[],
        persistent=None,
    )
    application.add_handler(conv_handler)

    application.add_handler(
        CallbackQueryHandler(
            button_click,
            pattern="^"
            + constants.CALLBACK_BOUGHT
            + "$|^"
            + constants.CALLBACK_HOUR_LATER
            + "$|^"
            + constants.CALLBACK_DAY_LATER
            + "$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(button_click, pattern="^" + constants.CALLBACK_LATER + "$")
    )
    application.add_handler(CommandHandler("cancel", cancel_reminders))

    # --- 3. Логирование ошибок ---
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger = logging.getLogger(__name__)
        logger.error("*** ПРОИЗОШЛА НЕОЖИДАННАЯ ОШИБКА ***", exc_info=context.error)

    application.add_error_handler(on_error)


async def handle_date_input_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
   
   
   
    logger.info(f"[DEBUG] Received message: {update.message.text}")
    logger.info(f"[DEBUG] Chat ID: {update.effective_chat.id}")

    # Чтобы не ломать диалог, вызовем настоящую функцию
    await handle_date_input(update, context)
