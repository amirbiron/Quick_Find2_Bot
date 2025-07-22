import logging
import os
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ChatMemberHandler,
)
from pymongo import MongoClient

# הגדרת משתני סביבה
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
PORT = os.environ.get("PORT", "8000")

# הגדרת לוגינג
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# אתחול מסד נתונים
client = MongoClient(MONGO_URI)
db = client.get_database("QuickFind2BotDB")
users_collection = db.get_collection("users")
guides_collection = db.get_collection("guides")
logging.info("מסד הנתונים אותחל בהצלחה")

# =========================================================================
# פונקציות הבוט (נשארות כמעט ללא שינוי)
# =========================================================================

async def start_command(update: Update, context) -> None:
    # ... תוכן הפונקציה נשאר זהה ...
    user = update.effective_user
    users_collection.update_one(
        {"user_id": user.id},
        {"$set": {"first_name": user.first_name, "last_name": user.last_name}},
        upsert=True,
    )
    await update.message.reply_text("ברוך הבא לבוט המדריכים!")

async def guides_command(update: Update, context) -> None:
    # ... תוכן הפונקציה נשאר זהה ...
    await update.message.reply_text("זוהי רשימת המדריכים:")

async def button_callback(update: Update, context) -> None:
    # ... תוכן הפונקציה נשאר זהה ...
    pass

async def handle_message(update: Update, context) -> None:
    # ... תוכן הפונקציה נשאר זהה ...
    pass

async def welcome(update: Update, context) -> None:
    # ... תוכן הפונקציה נשאר זהה ...
    pass

# =========================================================================
# בניית האפליקציה ברמה הגלובלית
# =========================================================================

# כאן השינוי המרכזי - בניית האפליקציה מחוץ לפונקציה
application = Application.builder().token(BOT_TOKEN).build()

# הוספת המטפלים (handlers) לאפליקציה
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("guides", guides_command))
application.add_handler(CallbackQueryHandler(button_callback))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(ChatMemberHandler(welcome, ChatMemberHandler.CHAT_MEMBER))

# אין יותר צורך בפונקציית main או ב-if __name__ == "__main__"
