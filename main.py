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
from activity_reporter import create_reporter

# --- Load Environment Variables ---
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
ADMIN_ID = os.environ.get("ADMIN_ID")

reporter = create_reporter(
    mongodb_uri="mongodb+srv://mumin:M43M2TFgLfGvhBwY@muminai.tm6x81b.mongodb.net/?retryWrites=true&w=majority&appName=muminAI",
    service_id="srv-d1vm4m7diees73bq7eh0",
    service_name="Quick_Find2_Bot"
)

# --- Constants ---
GUIDES_PER_PAGE = 7
# States for ConversationHandler
SEARCH_QUERY, EDIT_GUIDE_TITLE = range(2)

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
    if not guide_text: return None
    
    # Apply minimum length only for original posts (not forwarded)
    if not message.forward_origin and len(guide_text) < 100: return None
    
    # Skip saving if tagged with #skip
    if "#skip" in guide_text.lower():
        return None
    
    # Filter out weekly summaries that start with "אז מה היה לנו השבוע?"
    if guide_text.strip().startswith("אז מה היה לנו השבוע?"):
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
    guides_collection.update_one({"original_message_id": original_message_id, "original_chat_id": original_chat_id}, {"$set": guide_document}, upsert=True)
    return title

def find_guide_link_by_title(title_query: str) -> str | None:
    """Return a Telegram post link for the first guide whose title matches the given regex (case-insensitive)."""
    guide = guides_collection.find_one({"title": {"$regex": title_query, "$options": "i"}})
    if not guide:
        return None
    chat_id = guide.get("original_chat_id")
    msg_id = guide.get("original_message_id")
    if not chat_id or not msg_id:
        return None
    return f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"

def build_guides_paginator(page: int = 0, mode='view'):
    guides_count = guides_collection.count_documents({})
    if guides_count == 0: return "לא נמצאו מדריכים במערכת.", None

    total_pages = math.ceil(guides_count / GUIDES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    guides_to_skip = page * GUIDES_PER_PAGE
    guides = list(guides_collection.find().sort("original_message_id", 1).skip(guides_to_skip).limit(GUIDES_PER_PAGE))
    
    keyboard = []
    
    if mode == 'delete' or mode == 'edit':
        message_text = "🗑️ *בחר מדריך:*\n\n" if mode == 'delete' else "✏️ *בחר מדריך:*\n\n"
        for guide in guides:
            title = guide.get("title", "ללא כותרת")
            guide_id_str = str(guide["_id"])
            chat_id = guide.get("original_chat_id")
            msg_id = guide.get("original_message_id")
            link = f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"
            
            message_text += f"🔹 {escape_markdown_v2(title)}\n"
            
            action_button = InlineKeyboardButton("מחק 🗑️", callback_data=f"delete:{guide_id_str}") if mode == 'delete' else InlineKeyboardButton("ערוך ✏️", callback_data=f"edit:{guide_id_str}")
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
    if page < total_pages - 1: nav_buttons.append(InlineKeyboardButton("הבא ▶️", callback_data=f"{callback_prefix}:{page+1}:loading"))
    if nav_buttons: keyboard.append(nav_buttons)
    
    return message_text, InlineKeyboardMarkup(keyboard)

# =========================================================================
# Bot Handlers
# =========================================================================
main_keyboard = ReplyKeyboardMarkup([["חיפוש 🔍"]], resize_keyboard=True)
admin_keyboard = ReplyKeyboardMarkup([["חיפוש 🔍"], ["מנהל 👤"]], resize_keyboard=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    start_text = """
👋 שלום וברוך הבא לערוץ!
אם זו הפעם הראשונה שלך פה – הכנתי לך ערכת התחלה מסודרת 🎁

מה תמצא כאן?
📌 מדריכים שימושיים בעברית
🧰 כלים מומלצים (AI, מדריכים לאנדרואיד, בוטים)
💡 רעיונות לפרויקטים אמיתיים
📚 מדריכים מעודכנים לשנת 2025

בחר מה שתרצה מתוך הכפתורים למטה ⬇️

📧 לכל תקלה או ביקורת ניתן לפנות ל-amirbiron@gmail.com או לחילופין ל-@moominAmir בטלגרם
"""
    inline_keyboard = [
        [InlineKeyboardButton("🧹 מדריך ניקוי מטמון (סמסונג)", url="https://t.me/AndroidAndAI/17")],
        [InlineKeyboardButton("🧠 מה ChatGPT באמת זוכר עליכם?", url="https://t.me/AndroidAndAI/20")],
        [InlineKeyboardButton("💸 טריק להנחה ל-GPT", url="https://t.me/AndroidAndAI/23")]
    ]

    # Try to include requested guides dynamically by title if they exist in DB
    ai_tools_link = find_guide_link_by_title("מדריך.*כלי.*2025|כלים.*2025|2025.*מדריך")
    if ai_tools_link:
        inline_keyboard.append([InlineKeyboardButton("🧠 מדריך כלי הבינה המלאכותית המקיף לשנת 2025", url=ai_tools_link)])

    midjourney_link = find_guide_link_by_title("מידג.?רני|Midjourney")
    if midjourney_link:
        inline_keyboard.append([InlineKeyboardButton("🎨 מדריך בסיסי למידג'רני", url=midjourney_link)])

    inline_keyboard.append([InlineKeyboardButton("📚 כל המדריכים", callback_data="show_guides_start")])
    await update.message.reply_text(start_text, reply_markup=InlineKeyboardMarkup(inline_keyboard))
    # Use admin keyboard for admin, regular keyboard for others
    keyboard = admin_keyboard if ADMIN_ID and str(update.effective_user.id) == ADMIN_ID else main_keyboard
    await update.message.reply_text("השתמש בכפתור החיפוש למטה כדי למצוא מדריך ספציפי:", reply_markup=keyboard)

async def guides_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    text, keyboard = build_guides_paginator(0, mode='view')
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
    text, keyboard = build_guides_paginator(0, mode='delete')
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
    text, keyboard = build_guides_paginator(0, mode='edit')
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)
    
async def recent_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reporter.report_activity(update.effective_user.id)
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

async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    contact_text = """
📧 *פרטי יצירת קשר*

לכל תקלה או ביקורת ניתן לפנות ל:
• אימייל: amirbiron@gmail.com
• טלגרם: @moominAmir

נשמח לשמוע ממך! 😊
"""
    await update.message.reply_text(contact_text, parse_mode='MarkdownV2')

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin menu button press"""
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID:
        await update.message.reply_text("אין לך הרשאות מנהל.")
        return
    
    admin_text = "👤 *תפריט מנהל*\n\nבחר פעולה:"
    inline_keyboard = [
        [InlineKeyboardButton("👥 משתמשים אחרונים", callback_data="admin_recent_users")],
        [InlineKeyboardButton("✏️ עריכת מדריכים", callback_data="admin_edit_guides")],
        [InlineKeyboardButton("🗑️ מחיקת מדריכים", callback_data="admin_delete_guides")]
    ]
    await update.message.reply_text(admin_text, reply_markup=InlineKeyboardMarkup(inline_keyboard), parse_mode='MarkdownV2')

# --- Conversation Handlers ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    await update.message.reply_text("נא להזין את מונח החיפוש:")
    return SEARCH_QUERY

async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    query = update.message.text
    results = list(guides_collection.find({"title": {"$regex": query, "$options": "i"}}))
    if not results:
        keyboard = admin_keyboard if ADMIN_ID and str(update.effective_user.id) == ADMIN_ID else main_keyboard
        await update.message.reply_text(f"לא נמצאו מדריכים התואמים לחיפוש.", reply_markup=keyboard)
        return ConversationHandler.END
    message = f"🔍 *תוצאות חיפוש עבור '{escape_markdown_v2(query)}':*\n\n"
    for guide in results:
        title = guide.get("title", "ללא כותרת")
        chat_id = guide.get("original_chat_id")
        msg_id = guide.get("original_message_id")
        link = f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{msg_id}"
        message += f"🔹 [{escape_markdown_v2(title)}]({link})\n\n"
    keyboard = admin_keyboard if ADMIN_ID and str(update.effective_user.id) == ADMIN_ID else main_keyboard
    await update.message.reply_text(message, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)
    return ConversationHandler.END

async def edit_guide_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    query = update.callback_query
    await query.answer()
    guide_id_str = query.data.split(":")[1]
    context.user_data['guide_to_edit'] = guide_id_str
    await query.edit_message_text("נא לשלוח את השם החדש עבור המדריך:")
    return EDIT_GUIDE_TITLE

async def update_guide_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    new_title = update.message.text
    guide_id_str = context.user_data.get('guide_to_edit')
    if not guide_id_str:
        keyboard = admin_keyboard if ADMIN_ID and str(update.effective_user.id) == ADMIN_ID else main_keyboard
        await update.message.reply_text("שגיאה, לא נמצא מדריך לעריכה.", reply_markup=keyboard)
        return ConversationHandler.END
    guides_collection.update_one({"_id": ObjectId(guide_id_str)}, {"$set": {"title": new_title}})
    keyboard = admin_keyboard if ADMIN_ID and str(update.effective_user.id) == ADMIN_ID else main_keyboard
    await update.message.reply_text(f"✅ השם עודכן בהצלחה ל: '{new_title}'", reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    keyboard = admin_keyboard if ADMIN_ID and str(update.effective_user.id) == ADMIN_ID else main_keyboard
    await update.message.reply_text('הפעולה בוטלה.', reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "noop": return
    
    # Handle admin menu callbacks
    if data == "admin_recent_users":
        if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_users = list(users_collection.find({"last_seen": {"$gte": seven_days_ago}}).sort("last_seen", -1))
        if not recent_users:
            await query.edit_message_text("לא היו משתמשים פעילים בשבוע האחרון.")
            return
        message = "👥 *משתמשים פעילים בשבוע האחרון:*\n\n"
        for user in recent_users:
            name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            last_seen = user.get("last_seen").strftime("%d/%m/%Y %H:%M")
            message += f"🔹 *{escape_markdown_v2(name)}* \\- נראה לאחרונה: {last_seen} UTC\n"
        await query.edit_message_text(message, parse_mode='MarkdownV2')
        return
    elif data == "admin_edit_guides":
        if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
        text, keyboard = build_guides_paginator(0, mode='edit')
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)
        return
    elif data == "admin_delete_guides":
        if not ADMIN_ID or str(update.effective_user.id) != ADMIN_ID: return
        text, keyboard = build_guides_paginator(0, mode='delete')
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)
        return
    
    if "page:" in data:
        # Handle loading animation for "הבא" button
        if ":loading" in data:
            # Show loading message
            await query.edit_message_text("⏳ טוען מדריכים...")
            await asyncio.sleep(0.2)  # 0.2 seconds delay
            data = data.replace(":loading", "")  # Remove loading flag
        
        mode_str, page_str = data.split("page:")
        page = int(page_str)
        await query.edit_message_text("⏳ טוען מדריכים...")
        await asyncio.sleep(0.5)
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
    elif data == "show_guides_start":
        text, keyboard = build_guides_paginator(0, mode='view')
        await query.message.reply_text(text, reply_markup=keyboard, parse_mode='MarkdownV2', disable_web_page_preview=True)

async def handle_new_guide_in_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.channel_post: save_guide_from_message(update.channel_post)
async def handle_forwarded_guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reporter.report_activity(update.effective_user.id)
    update_user_activity(update.effective_user)
    guide_text = update.message.text or update.message.caption
    if guide_text and "#skip" in guide_text.lower():
        await update.message.reply_text("ההודעה סומנה כ־#skip ולא נשמרה.")
        return
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

ptb_application.add_handler(search_conv_handler)
ptb_application.add_handler(edit_conv_handler)
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))
ptb_application.add_handler(CommandHandler("delete", delete_command))
ptb_application.add_handler(CommandHandler("edit", edit_command))
ptb_application.add_handler(CommandHandler("recent_users", recent_users_command))
ptb_application.add_handler(CommandHandler("contact", contact_command))
ptb_application.add_handler(MessageHandler(filters.Regex('^מנהל 👤$'), admin_menu))
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
