import logging
import os
import asyncio
import math
import re

# --- Imports for the Web Server ---
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

# --- Imports for the Bot ---
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, 
    ChatMemberHandler, ContextTypes, ConversationHandler
)
from pymongo import MongoClient
from bson.objectid import ObjectId

# --- Load Environment Variables ---
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
ADMIN_ID = os.environ.get("ADMIN_ID")

# --- Constants ---
GUIDES_PER_PAGE = 7
MAX_BUTTON_TEXT_LENGTH = 40
# States for ConversationHandler
SEARCH_QUERY = 1

# --- Basic Setup & Database ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
client = MongoClient(MONGO_URI)
db = client.get_database("QuickFind2BotDB")
users_collection = db.get_collection("users")
guides_collection = db.get_collection("guides")

# =========================================================================
# Helper Functions (save_guide, paginator, etc.)
# =========================================================================
def escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# ... (save_guide_from_message and build_guides_paginator functions are unchanged)
def save_guide_from_message(message: Message) -> str | None: pass
def build_guides_paginator(page: int = 0, for_delete=False): pass

# =========================================================================
# Bot Handlers
# =========================================================================
main_keyboard = ReplyKeyboardMarkup([["חיפוש 🔍"]], resize_keyboard=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    users_collection.update_one({"user_id": user.id}, {"$set": {"first_name": user.first_name, "last_name": user.last_name}}, upsert=True)
    await update.message.reply_text("👋 ברוך הבא!", reply_markup=main_keyboard)
    # You can follow up with the button-based welcome message if you like
    # ... (code for the start message with inline buttons)

async def guides_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = build_guides_paginator(0, for_delete=False)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("⛔ אין לך הרשאה.")
        return
    text, keyboard = build_guides_paginator(0, for_delete=True)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

# --- Search Conversation Functions ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user for their search query."""
    await update.message.reply_text("נא להזין את מונח החיפוש:")
    return SEARCH_QUERY

async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Performs the search and ends the conversation."""
    query = update.message.text
    results = list(guides_collection.find({"title": {"$regex": query, "$options": "i"}}))

    if not results:
        await update.message.reply_text(f"לא נמצאו מדריכים התואמים לחיפוש: '{escape_markdown_v2(query)}'", reply_markup=main_keyboard, parse_mode='MarkdownV2')
        return ConversationHandler.END

    message = f"🔍 *תוצאות חיפוש עבור '{escape_markdown_v2(query)}':*\n\n"
    for guide in results:
        title = guide.get("title", "ללא כותרת")
        chat_id = guide.get("original_chat_id")
        msg_id = guide.get("original_message_id")
        link = f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"
        message += f"🔹 [{escape_markdown_v2(title)}]({link})\n"

    await update.message.reply_text(message, reply_markup=main_keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)
    return ConversationHandler.END

async def cancel_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text('החיפוש בוטל.', reply_markup=main_keyboard)
    return ConversationHandler.END

# (Other handlers like button_callback, handle_new_guide_in_channel, etc. are unchanged)
def button_callback(): pass
def handle_new_guide_in_channel(): pass
def handle_forwarded_guide(): pass

# =========================================================================
# Application Setup & Web Server
# =========================================================================
ptb_application = Application.builder().token(BOT_TOKEN).build()

# Create the ConversationHandler for search
search_conv_handler = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^חיפוש 🔍$'), search_start)],
    states={
        SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search)],
    },
    fallbacks=[CommandHandler('cancel', cancel_search)],
)

ptb_application.add_handler(search_conv_handler) # Add the new conversation handler

# Add the rest of the handlers
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))
ptb_application.add_handler(CommandHandler("delete", delete_command))
ptb_application.add_handler(CallbackQueryHandler(button_callback))

if CHANNEL_ID: ptb_application.add_handler(MessageHandler(filters.Chat(chat_id=int(CHANNEL_ID)) & ~filters.COMMAND & ~filters.POLL, handle_new_guide_in_channel))
ptb_application.add_handler(MessageHandler(filters.FORWARDED & ~filters.POLL, handle_forwarded_guide))

# (Web server setup remains unchanged)
def on_startup(): pass
def on_shutdown(): pass
app = Starlette(on_startup=[on_startup], on_shutdown=[on_shutdown])
def telegram_webhook(): pass
