import logging
import os
import asyncio

# --- Imports for the Web Server ---
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

# --- Imports for the Bot ---
from telegram import Update, Message
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ChatMemberHandler
from pymongo import MongoClient

# --- Load Environment Variables ---
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
CHANNEL_ID = os.environ.get("CHANNEL_ID") # The ID of the channel you want to monitor

# --- Basic Setup ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Database Setup ---
try:
    client = MongoClient(MONGO_URI)
    db = client.get_database("QuickFind2BotDB")
    users_collection = db.get_collection("users")
    guides_collection = db.get_collection("guides")
    logging.info("Database initialized successfully")
except Exception as e:
    logging.error(f"Failed to initialize database: {e}")

# =========================================================================
# Core Logic Function to Save a Guide
# =========================================================================
def save_guide_from_message(message: Message) -> str | None:
    """
    Analyzes a message, and if it meets the criteria, saves it to the database.
    Returns the title of the saved guide, or None if not saved.
    """
    guide_text = message.text or message.caption
    
    # --- Filter out messages that are too short or empty ---
    if not guide_text or len(guide_text) < 50:
        logging.info(f"Message {message.message_id} skipped (too short or no text).")
        return None

    # --- Simple parsing: first line is title, the rest is description ---
    try:
        parts = guide_text.strip().split('\n', 1)
        title = parts[0].strip()
        description = parts[1].strip() if len(parts) > 1 else "No description"
    except Exception:
        title = "Guide"
        description = guide_text.strip()
        
    guide_document = {
        "title": title,
        "description": description,
        "original_message_id": message.message_id,
        "original_chat_id": message.chat_id,
        "full_text": guide_text
    }
    
    # Save to DB, prevent duplicates
    guides_collection.update_one(
        {"original_message_id": message.message_id, "original_chat_id": message.chat_id},
        {"$set": guide_document},
        upsert=True
    )
    logging.info(f"Guide '{title}' saved/updated successfully.")
    return title

# =========================================================================
# Your Bot's Handlers
# =========================================================================
async def start_command(update: Update, context) -> None:
    user = update.effective_user
    users_collection.update_one(
        {"user_id": user.id},
        {"$set": {"first_name": user.first_name, "last_name": user.last_name}},
        upsert=True,
    )
    await update.message.reply_text("ברוך הבא לבוט המדריכים!")

async def guides_command(update: Update, context) -> None:
    """Sends a list of all guides from the database."""
    try:
        all_guides = guides_collection.find().sort("title", 1) # Sort alphabetically
        guides_list = list(all_guides)

        if not guides_list:
            await update.message.reply_text("לא נמצאו מדריכים במערכת.")
            return

        message = "📖 *רשימת המדריכים הזמינים:*\n\n"
        for guide in guides_list:
            title = guide.get("title", "ללא כותרת")
            message += f"🔹 {title}\n"

        await update.message.reply_text(message, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error fetching guides: {e}")
        await update.message.reply_text("אירעה שגיאה בעת שליפת המדריכים.")

async def handle_new_guide_in_channel(update: Update, context) -> None:
    """Handles new posts in the channel."""
    save_guide_from_message(update.channel_post) # Note: use update.channel_post here

async def handle_forwarded_guide(update: Update, context) -> None:
    """Handles messages forwarded by an admin to fill the database."""
    saved_title = save_guide_from_message(update.message)
    if saved_title:
        await update.message.reply_text(f"✅ המדריך '{saved_title}' נשמר/עודכן בהצלחה!")
    else:
        await update.message.reply_text("לא ניתן היה לשמור את ההודעה (כנראה שהיא קצרה מדי).")

# =========================================================================
# Telegram Bot Application Setup
# =========================================================================
ptb_application = Application.builder().token(BOT_TOKEN).build()

# --- Register Handlers ---
# 1. Standard commands
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))

# 2. Handler for new posts in your channel
if CHANNEL_ID:
    ptb_application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=int(CHANNEL_ID)) & ~filters.COMMAND & ~filters.POLL,
            handle_new_guide_in_channel
        )
    )

# 3. Handler for forwarded messages (to manually add old guides)
ptb_application.add_handler(MessageHandler(filters.FORWARDED & ~filters.POLL, handle_forwarded_guide))

# =========================================================================
# Web Server (Starlette) Setup
# =========================================================================
async def on_startup():
    await ptb_application.initialize()
    webhook_path = f"/{BOT_TOKEN.split(':')[-1]}"
    url = f"{WEBHOOK_URL}{webhook_path}"
    await ptb_application.bot.set_webhook(url=url)
    logging.info(f"Webhook set to {url}")

async def on_shutdown():
    await ptb_application.shutdown()
    logging.info("Application shut down")

app = Starlette(on_startup=[on_startup], on_shutdown=[on_shutdown])

@app.route(f"/{BOT_TOKEN.split(':')[-1]}", methods=["POST"])
async def telegram_webhook(request: Request) -> Response:
    data = await request.json()
    update = Update.de_json(data, ptb_application.bot)
    await ptb_application.process_update(update)
    return Response(status_code=200)
