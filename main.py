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
GUIDES_PER_PAGE = 5 # Reduced for better layout with more buttons
MAX_BUTTON_TEXT_LENGTH = 40

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
    logging.info(f"Guide '{title}' saved/updated.")
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
        if len(title.encode('utf-8')) > MAX_BUTTON_TEXT_LENGTH:
            display_title = title[:25] + "..."
        else:
            display_title = title
            
        guide_id_str = str(guide["_id"])
        chat_id = guide.get("original_chat_id")
        msg_id = guide.get("original_message_id")
        link = f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"

        if for_delete:
            # Row 1: Title
            keyboard.append([InlineKeyboardButton(display_title, url=link)])
            # Row 2: Actions (View and Delete) - this will be full width
            keyboard.append([
                InlineKeyboardButton("צפה 👁️", url=link),
                InlineKeyboardButton("מחק 🗑️", callback_data=f"delete:{guide_id_str}")
            ])
        else:
            keyboard.append([InlineKeyboardButton(display_title, url=link)])

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
    # (The content of this function remains the same)
    user = update.effective_user
    users_collection.update_one({"user_id": user.id}, {"$set": {"first_name": user.first_name, "last_name": user.last_name}}, upsert=True)
    start_text = """
👋 שלום וברוך הבא לערוץ!
אם זו הפעם הראשונה שלך פה – הכנתי לך ערכת התחלה מסודרת 🎁
בחר מה שתרצה מתוך הכפתורים למטה ⬇️
"""
    keyboard = [
        [InlineKeyboardButton("🧹 מדריך ניקוי מטמון (סמסונג)", url="https://t.me/AndroidAndAI/17")],
        [InlineKeyboardButton("🧠 מה ChatGPT באמת זוכר עליכם?", url="https://t.me/AndroidAndAI/20")],
        [InlineKeyboardButton("📚 כל המדריכים", callback_data="show_guides_start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(start_text, reply_markup=reply_markup)

async def guides_command(update: Update, context) -> None:
    text, keyboard = build_guides_paginator(0, for_delete=False)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)

async def delete_command(update: Update, context) -> None:
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID:
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
        if keyboard: await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
    elif data.startswith("deletepage:"):
        page = int(data.split(":")[1])
        text, keyboard = build_guides_paginator(page, for_delete=True)
        if keyboard: await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)
    elif data.startswith("delete:"):
        guide_id_str = data.split(":")[1]
        guide = guides_collection.find_one({"_id": ObjectId(guide_id_str)})
        if guide:
            title_preview = guide.get('title', '')[:50]
            text = f"❓ האם אתה בטוח שברצונך למחוק את המדריך '{title_preview}...'?"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ כן, מחק", callback_data=f"confirm_delete:{guide_id_str}"), InlineKeyboardButton("❌ לא, בטל", callback_data="cancel_delete")]])
            await query.edit_message_text(text, reply_markup=keyboard)
    elif data.startswith("confirm_delete:"):
        guide_id_str = data.split(":")[1]
        result = guides_collection.delete_one({"_id": ObjectId(guide_id_str)})
        if result.deleted_count > 0: await query.edit_message_text("🗑️ המדריך נמחק בהצלחה.")
        else: await query.edit_message_text("שגיאה: המדריך לא נמצא.")
    elif data == "cancel_delete":
        await query.edit_message_text("👍 המחיקה בוטלה.")
    elif data == "show_guides_start":
        text, keyboard = build_guides_paginator(0, for_delete=False)
        await query.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown', disable_web_page_preview=True)

async def handle_new_guide_in_channel(update: Update, context) -> None:
    if update.channel_post: save_guide_from_message(update.channel_post)

async def handle_forwarded_guide(update: Update, context) -> None:
    saved_title = save_guide_from_message(update.message)
    if saved_title: await update.message.reply_text(f"✅ המדריך '{saved_title}' נשמר/עודכן בהצלחה!")
    else: await update.message.reply_text("לא ניתן היה לשמור את ההודעה.")

# =========================================================================
# Application Setup & Web Server
# =========================================================================
ptb_application = Application.builder().token(BOT_TOKEN).build()
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))
ptb_application.add_handler(CommandHandler("delete", delete_command))
ptb_application.add_handler(CallbackQueryHandler(button_callback))

if CHANNEL_ID: ptb_application.add_handler(MessageHandler(filters.Chat(chat_id=int(CHANNEL_ID)) & ~filters.COMMAND & ~filters.POLL, handle_new_guide_in_channel))
ptb_application.add_handler(MessageHandler(filters.FORWARDED & ~filters.POLL, handle_forwarded_guide))

# ... (on_startup, on_shutdown, app, telegram_webhook functions remain the same)
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
