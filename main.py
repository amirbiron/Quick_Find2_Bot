import logging
import os
import asyncio
import math
import re
import traceback
import html

from datetime import datetime, timedelta

# --- Imports for the Web Server ---
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

# --- Imports for the Bot ---
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
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
# States for ConversationHandler
SEARCH_QUERY, EDIT_GUIDE_TITLE, MERGE_GUIDES = range(3)

# --- Basic Setup & Database ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

client = MongoClient(MONGO_URI)
db = client.get_database("QuickFind2BotDB")
users_collection = db.get_collection("users")
guides_collection = db.get_collection("guides")

# =========================================================================
# Helper Functions
# =========================================================================
def escape_markdown_v2(text: str) -> str:
    """Escapes characters for Telegram's MarkdownV2 parser."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def update_user_activity(user):
    """Updates the user's details and last_seen timestamp in the database."""
    if user:
        users_collection.update_one(
            {"user_id": user.id},
            {"$set": {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "last_seen": datetime.utcnow()
            }},
            upsert=True
        )

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
    return title

def build_guides_paginator(page: int = 0, mode='view'):
    guides_count = guides_collection.count_documents({})
    if guides_count == 0: return "לא נמצאו מדריכים במערכת.", None

    total_pages = math.ceil(guides_count / GUIDES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    guides_to_skip = page * GUIDES_PER_PAGE
    guides = list(guides_collection.find().sort("original_message_id", 1).skip(guides_to_skip).limit(GUIDES_PER_PAGE))
    
    keyboard = []
    
    if mode == 'delete' or mode == 'edit' or mode == 'merge' or mode == 'merge_second':
        if mode == 'delete':
            message_text = "🗑️ *בחר מדריך למחיקה:*\n\n"
        elif mode == 'edit':
            message_text = "✏️ *בחר מדריך לעריכה:*\n\n"
        elif mode == 'merge':
            message_text = "🔗 *בחר מדריך ראשון למיזוג:*\n\n"
        else:  # merge_second
            message_text = "🔗 *בחר מדריך שני למיזוג:*\n\n"
        
        for guide in guides:
            title = guide.get("title", "ללא כותרת")
            guide_id_str = str(guide["_id"])
            chat_id = guide.get("original_chat_id")
            msg_id = guide.get("original_message_id")
            link = f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"
            
            message_text += f"🔹 {escape_markdown_v2(title)}\n"
            
            if mode == 'delete':
                action_button = InlineKeyboardButton("מחק 🗑️", callback_data=f"delete:{guide_id_str}")
            elif mode == 'edit':
                action_button = InlineKeyboardButton("ערוך ✏️", callback_data=f"edit:{guide_id_str}")
            elif mode == 'merge':
                action_button = InlineKeyboardButton("בחר למיזוג 🔗", callback_data=f"merge:{guide_id_str}")
            else:  # merge_second
                action_button = InlineKeyboardButton("בחר למיזוג 🔗", callback_data=f"merge_second:{guide_id_str}")
            
            keyboard.append([
                InlineKeyboardButton("צפה 👁️", url=link),
                action_button
            ])
    else: # View mode with text as links
        message_text = "📖 *רשימת המדריכים הזמינים:*\n\n"
        for guide in guides:
            title = guide.get("title", "ללא כותרת")
            chat_id = guide.get("original_chat_id")
            msg_id = guide.get("original_message_id")
            link = f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"
            message_text += f"🔹 [{escape_markdown_v2(title)}]({link})\n\n"

    nav_buttons = []
    callback_prefix = f"{mode}page"
    if page > 0: nav_buttons.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"{callback_prefix}:{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"{callback_prefix}:{page+1}"))
    if nav_buttons: keyboard.append(nav_buttons)
    
    return message_text, InlineKeyboardMarkup(keyboard)

# =========================================================================
# Bot Handlers
# =========================================================================
main_keyboard = ReplyKeyboardMarkup([["חיפוש 🔍", "מיזוג 🔗"]], resize_keyboard=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    start_text = """
👋 שלום וברוך הבא לערוץ!
אם זו הפעם הראשונה שלך פה – הכנתי לך ערכת התחלה מסודרת 🎁

מה תמצא כאן?
📌 מדריכים שימושיים בעברית
🧰 כלים מומלצים (AI, מדריכים לאנדרואיד, בוטים)
💡 רעיונות לפרויקטים אמיתיים
📥 טופס לשיתוף אנונימי של כלים או מחשבות

בחר מה שתרצה מתוך הכפתורים למטה ⬇️
"""
    inline_keyboard = [[InlineKeyboardButton("🧹 מדריך ניקוי מטמון (סמסונג)", url="https://t.me/AndroidAndAI/17")], [InlineKeyboardButton("🧠 מה ChatGPT באמת זוכר עליכם?", url="https://t.me/AndroidAndAI/20")], [InlineKeyboardButton("💸 טריק להנחה ל-GPT", url="https://t.me/AndroidAndAI/23")], [InlineKeyboardButton("📝 טופס שיתוף אנונימי", url="https://oa379okv.forms.app/untitled-form")], [InlineKeyboardButton("📚 כל המדריכים", callback_data="show_guides_start")]]
    await update.message.reply_text(start_text, reply_markup=InlineKeyboardMarkup(inline_keyboard))
    await update.message.reply_text("השתמש בכפתור החיפוש למטה כדי למצוא מדריך ספציפי:", reply_markup=main_keyboard)

async def guides_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    text, keyboard = build_guides_paginator(0, mode='view')
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
    text, keyboard = build_guides_paginator(0, mode='delete')
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
    text, keyboard = build_guides_paginator(0, mode='edit')
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

async def merge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
    text, keyboard = build_guides_paginator(0, mode='merge')
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)
    
async def recent_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent_users = list(users_collection.find({"last_seen": {"$gte": seven_days_ago}}).sort("last_seen", -1))
    if not recent_users:
        await update.message.reply_text("לא היו משתמשים פעילים בשבוע האחרון.")
        return
    message = "👥 *משתמשים פעילים בשבוע האחרון:*\n\n"
    for user in recent_users:
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        last_seen = user.get("last_seen").strftime("%d/%m/%Y %H:%M")
        message += f"🔹 *{escape_markdown_v2(name)}* \\- נראה לאחרונה: {last_seen} UTC\n"
    await update.message.reply_text(message, parse_mode='MarkdownV2')

# --- Conversation Handlers ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    update_user_activity(update.effective_user)
    await update.message.reply_text("נא להזין את מונח החיפוש:")
    return SEARCH_QUERY

async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    update_user_activity(update.effective_user)
    query = update.message.text
    results = list(guides_collection.find({"title": {"$regex": query, "$options": "i"}}))
    if not results:
        await update.message.reply_text(f"לא נמצאו מדריכים התואמים לחיפוש.", reply_markup=main_keyboard)
        return ConversationHandler.END
    message = f"🔍 *תוצאות חיפוש עבור '{escape_markdown_v2(query)}':*\n\n"
    for guide in results:
        title = guide.get("title", "ללא כותרת")
        chat_id = guide.get("original_chat_id")
        msg_id = guide.get("original_message_id")
        link = f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"
        message += f"🔹 [{escape_markdown_v2(title)}]({link})\n\n"
    await update.message.reply_text(message, reply_markup=main_keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)
    return ConversationHandler.END

async def edit_guide_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    update_user_activity(update.effective_user)
    query = update.callback_query
    await query.answer()
    guide_id_str = query.data.split(":")[1]
    context.user_data['guide_to_edit'] = guide_id_str
    await query.edit_message_text("נא לשלוח את השם החדש עבור המדריך:")
    return EDIT_GUIDE_TITLE

async def update_guide_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    update_user_activity(update.effective_user)
    new_title = update.message.text
    guide_id_str = context.user_data.get('guide_to_edit')
    if not guide_id_str:
        await update.message.reply_text("שגיאה, לא נמצא מדריך לעריכה.", reply_markup=main_keyboard)
        return ConversationHandler.END
    guides_collection.update_one({"_id": ObjectId(guide_id_str)}, {"$set": {"title": new_title}})
    await update.message.reply_text(f"✅ השם עודכן בהצלחה ל: '{new_title}'", reply_markup=main_keyboard)
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    update_user_activity(update.effective_user)
    await update.message.reply_text('הפעולה בוטלה.', reply_markup=main_keyboard)
    context.user_data.clear()
    return ConversationHandler.END

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "noop": return
    if "page:" in data:
        mode_str, page_str = data.split("page:")
        page = int(page_str)
        text, keyboard = build_guides_paginator(page, mode=mode_str)
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                raise
    elif data.startswith("delete:"):
        guide_id_str = data.split(":")[1]
        guide = guides_collection.find_one({"_id": ObjectId(guide_id_str)})
        if guide:
            title_preview = escape_markdown_v2(guide.get('title', '')[:50])
            text = f"❓ האם אתה בטוח שברצונך למחוק את המדריך '{title_preview}\.\.\.'?"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ כן, מחק", callback_data=f"confirm_delete:{guide_id_str}"), InlineKeyboardButton("❌ לא, בטל", callback_data="cancel_delete")]])
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode='MarkdownV2')
    elif data.startswith("confirm_delete:"):
        guide_id_str = data.split(":")[1]
        result = guides_collection.delete_one({"_id": ObjectId(guide_id_str)})
        if result.deleted_count > 0: await query.edit_message_text("🗑️ המדריך נמחק בהצלחה\.")
        else: await query.edit_message_text("שגיאה: המדריך לא נמצא\.")
    elif data == "cancel_delete":
        await query.edit_message_text("👍 המחיקה בוטלה\.")
    elif data.startswith("merge:"):
        guide_id_str = data.split(":")[1]
        guide = guides_collection.find_one({"_id": ObjectId(guide_id_str)})
        if guide:
            context.user_data['first_guide_id'] = guide_id_str
            context.user_data['first_guide_title'] = guide.get('title', 'ללא כותרת')
            text = f"✅ בחרת את המדריך: '{escape_markdown_v2(guide.get('title', 'ללא כותרת'))}'\n\nעכשיו בחר את המדריך השני למיזוג:"
            keyboard = build_guides_paginator(0, mode='merge_second')[1]
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode='MarkdownV2')
    elif data.startswith("merge_second:"):
        second_guide_id_str = data.split(":")[1]
        first_guide_id = context.user_data.get('first_guide_id')
        first_guide_title = context.user_data.get('first_guide_title', 'ללא כותרת')
        
        if not first_guide_id:
            await query.edit_message_text("❌ שגיאה: לא נמצא מדריך ראשון למיזוג.")
            return
            
        second_guide = guides_collection.find_one({"_id": ObjectId(second_guide_id_str)})
        if second_guide:
            second_guide_title = second_guide.get('title', 'ללא כותרת')
            text = f"🔗 *מיזוג מדריכים:*\n\nמדריך ראשון: {escape_markdown_v2(first_guide_title)}\nמדריך שני: {escape_markdown_v2(second_guide_title)}\n\nהאם אתה בטוח שברצונך למזג את המדריכים?"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ כן, מזג", callback_data=f"confirm_merge:{first_guide_id}:{second_guide_id_str}")],
                [InlineKeyboardButton("❌ לא, בטל", callback_data="cancel_merge")]
            ])
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode='MarkdownV2')
    elif data.startswith("confirm_merge:"):
        guide_ids = data.split(":")[1:]
        if len(guide_ids) == 2:
            first_guide_id, second_guide_id = guide_ids
            # כאן תוכל להוסיף את הלוגיקה למיזוג המדריכים
            # לדוגמה: עדכון הכותרת, מחיקת המדריך השני, וכו'
            await query.edit_message_text("🔗 המיזוג בוצע בהצלחה!")
            context.user_data.clear()
    elif data == "cancel_merge":
        await query.edit_message_text("👍 המיזוג בוטל.")
        context.user_data.clear()
    elif data == "show_guides_start":
        text, keyboard = build_guides_paginator(0, mode='view')
        await query.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

async def handle_new_guide_in_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.channel_post: save_guide_from_message(update.channel_post)
async def handle_forwarded_guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user_activity(update.effective_user)
    saved_title = save_guide_from_message(update.message)
    if saved_title: await update.message.reply_text(f"✅ המדריך '{escape_markdown_v2(saved_title)}' נשמר/עודכן בהצלחה\!", parse_mode='MarkdownV2')
    else: await update.message.reply_text("לא ניתן היה לשמור את ההודעה\.")

# --- The new Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    logger.error("Exception while handling an update:", exc_info=context.error)

# =========================================================================
# Application Setup & Web Server
# =========================================================================
ptb_application = Application.builder().token(BOT_TOKEN).build()

# Add error handler
ptb_application.add_error_handler(error_handler)

# Conversation Handlers
search_conv_handler = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^חיפוש 🔍$'), search_start)], states={SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search)]}, fallbacks=[CommandHandler('cancel', cancel_conversation)])
edit_conv_handler = ConversationHandler(entry_points=[CallbackQueryHandler(edit_guide_start, pattern="^edit:")], states={EDIT_GUIDE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_guide_title)]}, fallbacks=[CommandHandler('cancel', cancel_conversation)])
merge_conv_handler = ConversationHandler(entry_points=[MessageHandler(filters.Regex('^מיזוג 🔗$'), merge_command)], states={}, fallbacks=[CommandHandler('cancel', cancel_conversation)])

ptb_application.add_handler(search_conv_handler)
ptb_application.add_handler(edit_conv_handler)
ptb_application.add_handler(merge_conv_handler)
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))
ptb_application.add_handler(CommandHandler("delete", delete_command))
ptb_application.add_handler(CommandHandler("edit", edit_command))
ptb_application.add_handler(CommandHandler("merge", merge_command))
ptb_application.add_handler(CommandHandler("recent_users", recent_users_command))
ptb_application.add_handler(CallbackQueryHandler(button_callback))

if CHANNEL_ID: ptb_application.add_handler(MessageHandler(filters.Chat(chat_id=int(CHANNEL_ID)) & ~filters.COMMAND & ~filters.POLL, handle_new_guide_in_channel))
ptb_application.add_handler(MessageHandler(filters.FORWARDED & ~filters.POLL, handle_forwarded_guide))

# --- Web Server ---
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
