import logging
import os
import asyncio

# --- Imports for the Web Server ---
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

# --- Imports for the Bot ---
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ChatMemberHandler
from pymongo import MongoClient

# --- Load Environment Variables ---
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL") # Render provides this automatically

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
# Your Bot's Functions (start_command, guides_command, etc.)
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
        all_guides = guides_collection.find()
        guides_list = list(all_guides) # Convert cursor to list to check its length

        if not guides_list:
            await update.message.reply_text("לא נמצאו מדריכים במערכת.")
            return

        # Format the message
        message = "📖 *רשימת המדריכים הזמינים:*\n\n"
        for guide in guides_list:
            # Assuming each guide has a 'title' and 'description' field
            title = guide.get("title", "ללא כותרת")
            description = guide.get("description", "אין תיאור")
            message += f"🔹 *{title}* - {description}\n"

        # Send the formatted message
        await update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error fetching guides: {e}")
        await update.message.reply_text("אירעה שגיאה בעת שליפת המדריכים.")

async def button_callback(update: Update, context) -> None:
    pass # Your logic here

async def handle_message(update: Update, context) -> None:
    pass # Your logic here

async def welcome(update: Update, context) -> None:
    pass # Your logic here

# =========================================================================
# Telegram Bot Application Setup
# =========================================================================
ptb_application = Application.builder().token(BOT_TOKEN).build()

# Register your command and message handlers
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))
ptb_application.add_handler(CallbackQueryHandler(button_callback))
ptb_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
ptb_application.add_handler(ChatMemberHandler(welcome, ChatMemberHandler.CHAT_MEMBER))

# =========================================================================
# Web Server (Starlette) Setup
# =========================================================================
async def on_startup():
    # Initialize the bot
    await ptb_application.initialize()
    
    # Set the webhook
    webhook_path = f"/{BOT_TOKEN.split(':')[-1]}"
    url = f"{WEBHOOK_URL}{webhook_path}"
    await ptb_application.bot.set_webhook(url=url)
    logging.info(f"Webhook set to {url}")

async def on_shutdown():
    # Shut down the bot
    await ptb_application.shutdown()
    logging.info("Application shut down")

# This is the main web application instance that Uvicorn will run
app = Starlette(on_startup=[on_startup], on_shutdown=[on_shutdown])

@app.route(f"/{BOT_TOKEN.split(':')[-1]}", methods=["POST"])
async def telegram_webhook(request: Request) -> Response:
    # This function receives the update from Telegram
    data = await request.json()
    update = Update.de_json(data, ptb_application.bot)
    
    # Process the update with your bot
    await ptb_application.process_update(update)
    
    # Return a 200 OK response to Telegram
    return Response(status_code=200)
