import logging
import os
import asyncio
import math

# --- Imports for the Web Server ---
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

# --- Imports for the Bot ---
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ChatMemberHandler
from pymongo import MongoClient

# --- Load Environment Variables ---
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

# --- Constants ---
GUIDES_PER_PAGE = 7

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
# Core Logic Functions
# =========================================================================
def save_guide_from_message(message: Message) -> str | None:
    guide_text = message.text or message.caption
    if not guide_text or len(guide_text) < 50:
        return None

    if message.forward_origin:
        original_chat_id = message.forward_origin.chat.id
        original_message_id = message.forward_origin.message_id
    else:
        original_chat_id = message.chat_id
        original_message_id = message.message_id

    try:
        title = guide_text.strip().split('\n', 1)[0]
    except Exception:
        title = "Guide"
        
    guide_document = {"title": title, "original_message_id": original_message_id, "original_chat_id": original_chat_id}
    guides_collection.update_one(
        {"original_message_id": original_message_id, "original_chat_id": original_chat_id},
        {"$set": guide_document},
        upsert=True
    )
    logging.info(f"Guide '{title}' saved/updated.")
    return title

def build_guides_paginator(page: int = 0):
    """Builds the message text and keyboard for a specific page of guides."""
    guides_count = guides_collection.count_documents({})
    if guides_count == 0:
        return "לא נמצאו מדריכים במערכת.", None

    total_pages = math.ceil(guides_count / GUIDES_PER_PAGE)
    page = max(0, min(page, total_pages - 1)) # Clamp page number to valid range

    guides_to_skip = page * GUIDES_PER_PAGE
    guides = guides_collection.find().sort("original_message_id", 1).skip(guides_to_skip).limit(GUIDES_PER_PAGE)
    
    message_text = "📖 *רשימת המדריכים הזמינים:*\n\n"
    for guide in guides:
        title = guide.get("title", "ללא כותרת")
        chat_id = guide.get("original_chat_id")
        msg_id = guide.get("original_message_id")
        
        if chat_id and msg_id:
            link_chat_id = str(chat_id).replace("-100", "", 1)
            link = f"https://t.me/c/{link_chat_id}/{msg_id}"
            message_text += f"🔹 [{title}]({link})\n"
        else:
             message_text += f"🔹 {title} (אין קישור ישיר)\n"

    # --- Build Pagination Keyboard ---
    keyboard = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"page:{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop")) # No-op button

    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"page:{page+1}"))
    
    keyboard.append(nav_buttons)
    return message_text, InlineKeyboardMarkup(keyboard)

# =========================================================================
# Your Bot's Handlers
# =========================================================================
async def start_command(update: Update, context) -> None:
    user = update.effective_user
    users_collection.update_one({"user_id": user.id}, {"$set": {"first_name": user.first_name, "last_name": user.last_name}}, upsert=True)
    start_text = "..." # Your existing start text
    keyboard = [
        [InlineKeyboardButton("📚 כל המדריכים", callback_data="show_guides_start")]
        # ... your other buttons
    ]
    await update.message.reply_text(start_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def guides_command(update: Update, context) -> None:
    text, keyboard = build_guides_paginator(0)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)

async def button_callback(update: Update, context) -> None:
    query = update.callback_query
    await query.answer()

    if query.data.startswith("page:"):
        page = int(query.data.split(":")[1])
        text, keyboard = build_guides_paginator(page)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
    
    elif query.data == "show_guides_start":
        text, keyboard = build_guides_paginator(0)
        await query.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)

async def handle_new_guide_in_channel(update: Update, context) -> None:
    if update.channel_post:
        save_guide_from_message(update.channel_post)

async def handle_forwarded_guide(update: Update, context) -> None:
    saved_title = save_guide_from_message(update.message)
    if saved_title:
        await update.message.reply_text(f"✅ המדריך '{saved_title}' נשמר/עודכן בהצלחה!")
    else:
        await update.message.reply_text("לא ניתן היה לשמור את ההודעה.")

# =========================================================================
# Telegram Bot Application Setup
# =========================================================================
ptb_application = Application.builder().token(BOT_TOKEN).build()

ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))
ptb_application.add_handler(CallbackQueryHandler(button_callback))

if CHANNEL_ID:
    ptb_application.add_handler(MessageHandler(filters.Chat(chat_id=int(CHANNEL_ID)) & ~filters.COMMAND & ~filters.POLL, handle_new_guide_in_channel))

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
