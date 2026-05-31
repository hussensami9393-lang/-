import asyncio
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8583457334:AAG7nuJWeKotMS_y3p5NU2OKl9wwGfny8hw").strip()
TIMEZONE = os.getenv("TIMEZONE", "Asia/Baghdad").strip()
DB_PATH = os.getenv("DB_PATH", "reminders.db").strip()

ASK_TITLE, ASK_DETAILS, ASK_DATETIME = range(3)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def tz() -> ZoneInfo:
    return ZoneInfo(TIMEZONE)


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(connect_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def add_reminder(user_id: int, chat_id: int, title: str, details: str, remind_at: datetime) -> int:
    with closing(connect_db()) as conn:
        cur = conn.execute(
            """
            INSERT INTO reminders (user_id, chat_id, title, details, remind_at, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                user_id,
                chat_id,
                title,
                details,
                remind_at.isoformat(),
                datetime.now(tz()).isoformat(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_pending_reminders(user_id: int | None = None) -> list[sqlite3.Row]:
    query = "SELECT * FROM reminders WHERE status = 'pending'"
    params: tuple = ()
    if user_id is not None:
        query += " AND user_id = ?"
        params = (user_id,)
    query += " ORDER BY remind_at ASC"
    with closing(connect_db()) as conn:
        return list(conn.execute(query, params).fetchall())


def mark_done(reminder_id: int) -> None:
    with closing(connect_db()) as conn:
        conn.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,))
        conn.commit()


def delete_reminder(reminder_id: int, user_id: int) -> bool:
    with closing(connect_db()) as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ? AND status = 'pending'",
            (reminder_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def parse_user_datetime(text: str) -> datetime | None:
    cleaned = text.strip().replace("/", "-")
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.replace(tzinfo=tz())
        except ValueError:
            continue
    return None


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ إضافة تذكير جديد", callback_data="add")],
            [InlineKeyboardButton("📋 عرض التذكيرات", callback_data="list")],
            [InlineKeyboardButton("🗑 حذف تذكير", callback_data="delete_menu")],
            [InlineKeyboardButton("ℹ️ طريقة الاستخدام", callback_data="help")],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="home")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🌟 <b>بوت تذكير مريم بالأوراق الحكومية</b> 🌟\n\n"
        "أهلًا مريم، هذا البوت يساعدك على عدم نسيان مواعيد تقديم الأوراق للمدير.\n"
        "يمكنك تحديد اليوم والساعة والدقيقة والثانية، وسيصلك تذكير واضح في موعده.\n\n"
        "اختاري من الأزرار:"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def help_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "ℹ️ <b>طريقة الاستخدام</b>\n\n"
        "1) اضغطي: ➕ إضافة تذكير جديد\n"
        "2) اكتبي عنوان التذكير، مثل: تسليم ملف الترقية\n"
        "3) اكتبي تفاصيل الأوراق المطلوبة\n"
        "4) اكتبي التاريخ والوقت بهذه الصيغة:\n\n"
        "<code>2026-06-15 09:30:00</code>\n\n"
        "يعني: سنة-شهر-يوم ساعة:دقيقة:ثانية\n\n"
        f"⏰ التوقيت المستخدم حاليًا: <b>{TIMEZONE}</b>"
    )
    await update.callback_query.edit_message_text(text, reply_markup=back_keyboard(), parse_mode=ParseMode.HTML)


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "home":
        await start(update, context)
        return ConversationHandler.END

    if data == "help":
        await help_text(update, context)
        return ConversationHandler.END

    if data == "list":
        await show_reminders(update, context)
        return ConversationHandler.END

    if data == "delete_menu":
        await show_delete_menu(update, context)
        return ConversationHandler.END

    if data and data.startswith("delete:"):
        reminder_id = int(data.split(":", 1)[1])
        ok = delete_reminder(reminder_id, query.from_user.id)
        if ok:
            await query.edit_message_text("✅ تم حذف التذكير بنجاح.", reply_markup=main_keyboard())
        else:
            await query.edit_message_text("⚠️ لم أجد هذا التذكير أو ربما تم إرساله سابقًا.", reply_markup=main_keyboard())
        return ConversationHandler.END

    if data == "add":
        context.user_data.clear()
        await query.edit_message_text(
            "➕ <b>إضافة تذكير جديد</b>\n\nاكتبي عنوان التذكير، مثل:\n<code>تسليم أوراق الترقية</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="cancel")]]),
        )
        return ASK_TITLE

    if data == "cancel":
        await query.edit_message_text("تم الإلغاء ✅", reply_markup=main_keyboard())
        return ConversationHandler.END

    return ConversationHandler.END


async def ask_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = update.message.text.strip()
    if len(title) < 2:
        await update.message.reply_text("اكتبي عنوانًا أوضح للتذكير.")
        return ASK_TITLE
    context.user_data["title"] = title
    await update.message.reply_text(
        "📄 ممتاز. الآن اكتبي تفاصيل الأوراق المطلوبة، مثل:\n"
        "صورة الهوية، خطاب المدير، نموذج الترقية، توقيع الموارد البشرية."
    )
    return ASK_DETAILS


async def ask_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    details = update.message.text.strip()
    if len(details) < 2:
        await update.message.reply_text("اكتبي تفاصيل الأوراق حتى أذكّرك بها بوضوح.")
        return ASK_DETAILS
    context.user_data["details"] = details
    await update.message.reply_text(
        "⏰ الآن اكتبي التاريخ والوقت بالثانية.\n\n"
        "الصيغة المطلوبة:\n"
        "2026-06-15 09:30:00\n\n"
        "يمكن أيضًا استخدام:\n"
        "15-06-2026 09:30:00"
    )
    return ASK_DATETIME


async def save_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    dt = parse_user_datetime(update.message.text)
    if not dt:
        await update.message.reply_text(
            "⚠️ الصيغة غير صحيحة. اكتبي التاريخ هكذا بالضبط:\n"
            "2026-06-15 09:30:00"
        )
        return ASK_DATETIME

    now = datetime.now(tz())
    if dt <= now:
        await update.message.reply_text("⚠️ اختاري موعدًا في المستقبل وليس في الماضي.")
        return ASK_DATETIME

    reminder_id = add_reminder(
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        title=context.user_data["title"],
        details=context.user_data["details"],
        remind_at=dt,
    )

    await update.message.reply_text(
        "✅ <b>تم حفظ التذكير بنجاح</b>\n\n"
        f"🆔 رقم التذكير: <code>{reminder_id}</code>\n"
        f"📌 العنوان: <b>{context.user_data['title']}</b>\n"
        f"📅 الموعد: <code>{dt.strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
        "سأرسل لكِ تنبيهًا في الوقت المحدد بإذن الله.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def show_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rows = get_pending_reminders(query.from_user.id)
    if not rows:
        await query.edit_message_text("📭 لا توجد تذكيرات حالية.", reply_markup=main_keyboard())
        return

    lines = ["📋 <b>تذكيراتك الحالية</b>\n"]
    for row in rows[:20]:
        dt = datetime.fromisoformat(row["remind_at"]).astimezone(tz())
        lines.append(
            f"🆔 <code>{row['id']}</code>\n"
            f"📌 <b>{row['title']}</b>\n"
            f"📅 <code>{dt.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"📄 {row['details']}\n"
        )
    await query.edit_message_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode=ParseMode.HTML)


async def show_delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rows = get_pending_reminders(query.from_user.id)
    if not rows:
        await query.edit_message_text("📭 لا توجد تذكيرات لحذفها.", reply_markup=main_keyboard())
        return

    buttons = []
    for row in rows[:15]:
        dt = datetime.fromisoformat(row["remind_at"]).astimezone(tz())
        label = f"🗑 {row['id']} - {row['title']} - {dt.strftime('%m-%d %H:%M:%S')}"
        buttons.append([InlineKeyboardButton(label[:60], callback_data=f"delete:{row['id']}")])
    buttons.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="home")])
    await query.edit_message_text("اختاري التذكير الذي تريدين حذفه:", reply_markup=InlineKeyboardMarkup(buttons))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("تم الإلغاء ✅", reply_markup=main_keyboard())
    return ConversationHandler.END


async def reminder_loop(app: Application) -> None:
    while True:
        try:
            now = datetime.now(tz())
            rows = get_pending_reminders()
            for row in rows:
                remind_at = datetime.fromisoformat(row["remind_at"]).astimezone(tz())
                if remind_at <= now:
                    message = (
                        "🚨 <b>تذكير مهم لمريم</b> 🚨\n\n"
                        "📣 لا تنسي تقديم الأوراق في موعدها حتى تحفظي حقك في الترقية.\n\n"
                        f"📌 <b>{row['title']}</b>\n"
                        f"📄 {row['details']}\n"
                        f"📅 الموعد المحدد: <code>{remind_at.strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
                        "✅ جهزي الأوراق وقدميها للمدير في الوقت المحدد."
                    )
                    await app.bot.send_message(
                        chat_id=row["chat_id"],
                        text=message,
                        parse_mode=ParseMode.HTML,
                    )
                    mark_done(row["id"])
        except Exception:
            logger.exception("Error in reminder loop")
        await asyncio.sleep(1)


async def post_init(app: Application) -> None:
    app.create_task(reminder_loop(app))


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it as an environment variable.")

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_router)],
        states={
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_details), CallbackQueryHandler(button_router)],
            ASK_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_datetime), CallbackQueryHandler(button_router)],
            ASK_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_datetime), CallbackQueryHandler(button_router)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(button_router)],
        allow_reentry=True,
    )

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_router))
    return app


def main() -> None:
    init_db()
    app = build_application()
    logger.info("Maryam reminder bot started with timezone %s", TIMEZONE)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
