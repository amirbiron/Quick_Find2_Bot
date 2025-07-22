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
from bson.objectid import ObjectId # Important for deleting by ID

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

# --- Basic Setup & Database ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
client = MongoClient(MONGO_URI)
db = client.get_database("QuickFind2BotDB")
users_collection = db.get_collection("users")
guides_collection = db.get_collection("guides")

# =========================================================================
# Core Logic & Paginators
# =========================================================================
def save_guide_from_message(message: Message) -> str | None:
    # (This function remains unchanged from the previous version)
    guide_text = message.text or message.caption
    if not guide_text or len(guide_text) < 50: return None
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
    guides_collection.update_one({"original_message_id": original_message_id, "original_chat_id": original_chat_id}, {"$set": guide_document}, upsert=True)
    return title

def build_guides_paginator(page: int = 0, for_delete=False):
    guides_count = guides_collection.count_documents({})
    if guides_count == 0:
        return "לא נמצאו מדריכים במערכת.", None

    total_pages = math.ceil(guides_count / GUIDES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    guides_to_skip = page * GUIDES_PER_PAGE
    guides = list(guides_collection.find().sort("original_message_id", 1).skip(guides_to_skip).limit(GUIDES_PER_PAGE))
    
    message_text = "📖 *רשימת המדריכים הזמינים:*\n"
    if for_delete:
        message_text = "🗑️ *בחר מדריך למחיקה:*\n"
    
    keyboard = []
    for guide in guides:
        title = guide.get("title", "ללא כותרת")
        guide_id_str = str(guide["_id"])
        
        row = []
        if for_delete:
            row.append(InlineKeyboardButton(f"מחק 🗑️", callback_data=f"delete:{guide_id_str}"))
            row.append(InlineKeyboardButton(title, callback_data="noop")) # Just for text
        else:
            chat_id = guide.get("original_chat_id")
            msg_id = guide.get("original_message_id")
            link = f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"
            row.append(InlineKeyboardButton(title, url=link))
        keyboard.append(row)

    nav_buttons = []
    callback_prefix = "deletepage" if for_delete else "page"
    if page > 0: nav_buttons.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"{callback_prefix}:{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"{callback_prefix}:{page+1}"))
    if nav_buttons: keyboard.append(nav_buttons)
    
    return message_text, InlineKeyboardMarkup(keyboard)

# =========================================================================
# Bot Handlers
# =========================================================================
async def start_command(update: Update, context) -> None:
    # ... (code is unchanged)
    pass 

async def guides_command(update: Update, context) -> None:
    text, keyboard = build_guides_paginator(0, for_delete=False)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)

async def delete_command(update: Update, context) -> None:
    """Admin command to start the deletion process."""
    if str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("⛔ אין לך הרשאה לבצע פעולה זו.")
        return
    text, keyboard = build_guides_paginator(0, for_delete=True)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)

async def button_callback(update: Update, context) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("page:"):
        page = int(data.split(":")[1])
        text, keyboard = build_guides_paginator(page, for_delete=False)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
    
    elif data.startswith("deletepage:"):
        page = int(data.split(":")[1])
        text, keyboard = build_guides_paginator(page, for_delete=True)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)

    elif data.startswith("delete:"):
        guide_id_str = data.split(":")[1]
        guide = guides_collection.find_one({"_id": ObjectId(guide_id_str)})
        if guide:
            text = f"❓ האם אתה בטוח שברצונך למחוק את המדריך '{guide['title']}'?"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ כן, מחק", callback_data=f"confirm_delete:{guide_id_str}"),
                    InlineKeyboardButton("❌ לא, בטל", callback_data="cancel_delete")
                ]
            ])
            await query.edit_message_text(text, reply_markup=keyboard)

    elif data.startswith("confirm_delete:"):
        guide_id_str = data.split(":")[1]
        result = guides_collection.delete_one({"_id": ObjectId(guide_id_str)})
        if result.deleted_count > 0:
            await query.edit_message_text("🗑️ המדריך נמחק בהצלחה.")
        else:
            await query.edit_message_text("שגיאה: המדריך לא נמצא (אולי כבר נמחק).")

    elif data == "cancel_delete":
        await query.edit_message_text("👍 המחיקה בוטלה.")
    
    elif data == "show_guides_start":
        text, keyboard = build_guides_paginator(0, for_delete=False)
        await query.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)


async def handle_new_guide_in_channel(update: Update, context) -> None:
    # ... (code is unchanged)
    pass
async def handle_forwarded_guide(update: Update, context) -> None:
    # ... (code is unchanged)
    pass

# =========================================================================
# Application Setup & Web Server
# =========================================================================
ptb_application = Application.builder().token(BOT_TOKEN).build()
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))
ptb_application.add_handler(CommandHandler("delete", delete_command)) # Add delete command
ptb_application.add_handler(CallbackQueryHandler(button_callback))

if CHANNEL_ID: ptb_application.add_handler(MessageHandler(filters.Chat(chat_id=int(CHANNEL_ID)) & ~filters.COMMAND & ~filters.POLL, handle_new_guide_in_channel))
ptb_application.add_handler(MessageHandler(filters.FORWARDED & ~filters.POLL, handle_forwarded_guide))

# Web Server setup (on_startup, on_shutdown, app, telegram_webhook) remains unchanged
async def on_startup():
    # ... (code is unchanged)
    pass
async def on_shutdown():
    # ... (code is unchanged)
    pass
app = Starlette(on_startup=[on_startup], on_shutdown=[on_shutdown])
@app.route(f"/{BOT_TOKEN.split(':')[-1]}", methods=["POST"])
async def telegram_webhook(request: Request) -> Response:
    # ... (code is unchanged)
    pass
