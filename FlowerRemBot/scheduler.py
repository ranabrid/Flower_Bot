# scheduler.py

from datetime import datetime, timedelta
import pytz
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import (
    save_user_date,
    get_all_users_for_restore,
)
import constants as const

moscow_tz = pytz.timezone(const.MOSCOW_TIMEZONE)
logger = logging.getLogger(__name__)


def get_aware_datetime(dt_object: datetime) -> datetime:
    """Приводит naive datetime к aware datetime с учетом часового пояса."""
    if dt_object.tzinfo is None or dt_object.tzinfo.utcoffset(dt_object) is None:
        return moscow_tz.localize(dt_object)
    return dt_object


async def restore_reminders(application):
    """Восстанавливает запланированные напоминания при запуске бота."""
    users_data = get_all_users_for_restore()
    now_aware = datetime.now(moscow_tz)
    job_queue = application.job_queue

    for chat_id, date_str in users_data:
        if not date_str:
            continue
        try:
            # Парсим строку даты (она должна быть в ISO 8601 формате)
            remind_date_naive = datetime.fromisoformat(date_str)
            remind_date_aware = get_aware_datetime(remind_date_naive)

            if remind_date_aware > now_aware:
                # Планируем только если время еще не прошло
                job_name = f"cycle_{chat_id}"
                # Удаляем старую задачу, если она существует, чтобы избежать дублирования
                existing_job = job_queue.get_job_by_name(job_name)
                if existing_job:
                    existing_job.schedule_removal()
                    logger.info(f"Removed existing job {job_name} during restore.")

                job_queue.run_once(
                    send_cycle_reminder,
                    when=remind_date_aware,
                    chat_id=chat_id,
                    name=job_name,
                )
                logger.info(
                    f"Restored reminder for {chat_id} at {remind_date_aware.isoformat()}"
                )
            else:
                logger.info(
                    f"Reminder for {chat_id} at {date_str} is in the past, not restoring."
                )
        except ValueError:
            logger.error(
                f"Failed to parse date string '{date_str}' for chat_id {chat_id} during restore."
            )
        except Exception as e:
            logger.error(f"Error restoring reminder for {chat_id}: {e}", exc_info=True)


async def send_first_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет первое приветственное сообщение с кнопками."""
    chat_id = context.job.chat_id if context.job else None
    if not chat_id:
        logger.error("Cannot send first reminder: chat_id is missing from job.")
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🗓️Запланировать напоминание", callback_data=const.CALLBACK_PLAN
            )
        ],
        [InlineKeyboardButton("❌Позже", callback_data=const.CALLBACK_LATER)],
    ]

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=const.MSG_WELCOME_START,  # Оставляем текст приветствия
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        logger.info(f"Sent first reminder to {chat_id}.")
    except Exception as e:
        logger.error(f"Failed to send first reminder to {chat_id}: {e}", exc_info=True)


async def send_cycle_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет основное напоминание и планирует следующее."""
    job = context.job
    chat_id = job.chat_id if job else None
    if not chat_id:
        logger.error("Cannot send cycle reminder: chat_id is missing from job.")
        return
    job_name = job.name if job else ""

    if job_name.startswith("first_"):
        try:

            logger.info(f"Sent confirmation to {chat_id} for scheduled reminder.")
        except Exception as e:
            logger.error(
                f"Failed to send confirmation to {chat_id}: {e}", exc_info=True
            )

    keyboard = [
        [InlineKeyboardButton("🌸 Купил", callback_data=const.CALLBACK_BOUGHT)],
        [InlineKeyboardButton("🚚 Заказать доставку", url=const.MSG_DELIVERY_URL)],
        [
            InlineKeyboardButton(
                "⏳ Напомнить через час", callback_data=const.CALLBACK_HOUR_LATER
            )
        ],
        [
            InlineKeyboardButton(
                "📅 Напомнить завтра", callback_data=const.CALLBACK_DAY_LATER
            )
        ],
    ]

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=const.MSG_REMINDER_TEXT,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        logger.info(f"Sent cycle reminder to {chat_id}.")

        # Планируем следующее напоминание (через 1 день), если это не задача "напомнить завтра/через час"
        # Это предотвратит бесконечное планирование, если пользователь нажимает "напомнить завтра"

        # Проверяем, какое имя у текущей задачи, чтобы понять, нужно ли планировать следующее
        job_name = job.name if job else ""

        # Если это обычное цикличное напоминание, планируем следующее
        if job_name.startswith("cycle_"):
            next_remind_date = datetime.now(moscow_tz) + timedelta(days=1)
            new_job_name = f"cycle_{chat_id}"

            # Удаляем старую задачу, чтобы избежать дублирования

            existing_jobs = context.job_queue.get_jobs_by_name(new_job_name)
            for existing_job in existing_jobs:
                existing_job.schedule_removal()
                logger.info(f"Removed existing job {new_job_name} before rescheduling.")

            context.job_queue.run_once(
                send_cycle_reminder,
                when=next_remind_date,
                chat_id=chat_id,
                name=new_job_name,
            )
            save_user_date(chat_id, next_remind_date.isoformat())
            logger.info(
                f"Scheduled next cycle reminder for {chat_id} at {next_remind_date.isoformat()}."
            )
        else:
            logger.info(
                f"Not rescheduling next reminder for {chat_id} from job '{job_name}' (it's a button action)."
            )

    except Exception as e:
        logger.error(f"Failed to send cycle reminder to {chat_id}: {e}", exc_info=True)
