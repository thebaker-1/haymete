from dotenv import load_dotenv

load_dotenv()

import os
import json
import logging
from io import BytesIO
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle

# Handle missing dependencies gracefully

# Handle missing dependencies and symbol errors gracefully
try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters
except ImportError as e:
    print("Error: python-telegram-bot is not installed or not compatible. Please run 'pip install python-telegram-bot'.")
    raise e

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
except ImportError as e:
    print("Error: google-api-python-client and google-auth-oauthlib are not installed. Please run 'pip install google-api-python-client google-auth-oauthlib'.")
    raise e

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)


# Constants
DATA_FILE = 'user_data.json'
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE', 'service_account.json')
DRIVE_FOLDER_ID = os.getenv('DRIVE_FOLDER_ID')

# States for ConversationHandler
ASKING_ID, WAITING_FOR_FILE = range(2)

# Load user data
def load_user_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    else:
        return {}

def save_user_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

user_data = load_user_data()

SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Google Drive authentication using OAuth2
def get_drive_service():
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first time.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Out-of-band OAuth: send link to Telegram, get code from user
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            auth_url, _ = flow.authorization_url(prompt='consent')
            # Store flow for later use
            return flow, auth_url
    service = build('drive', 'v3', credentials=creds)
    return service


# Store flow for OOB OAuth
drive_service = None
pending_oauth_flow = None

# Start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    # Reset user_data to ensure fresh start
    if context.user_data is not None:
        context.user_data.clear()
    await update.message.reply_text("Welcome! Please enter your ID.")
    return ASKING_ID

# Handle user ID input
async def handle_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return ConversationHandler.END
    user_id = update.message.text.strip()
    if user_id in user_data:
        if context.user_data is None:
            context.user_data = {}
        context.user_data['user_id'] = user_id
        name = user_data[user_id]['name']
        await update.message.reply_text(f"Hello, {name}! Please upload your file.")
        return WAITING_FOR_FILE
    else:
        await update.message.reply_text("ID not found. Please try again.")
        return ASKING_ID

# Handle file upload
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if context.user_data is None:
        context.user_data = {}
    user_id = context.user_data.get('user_id')
    if not user_id:
        await update.message.reply_text("Please enter your ID first by sending /start.")
        return ASKING_ID

    document = getattr(update.message, 'document', None)
    photo = getattr(update.message, 'photo', None)
    file = document or (photo[-1] if photo else None)
    if not file:
        await update.message.reply_text("Please upload a valid file (document or photo).")
        return WAITING_FOR_FILE

    # Get file info
    if document:
        file_id = document.file_id
        original_filename = document.file_name or 'file'
        mime_type = document.mime_type or 'application/octet-stream'
    elif photo:
        file_id = photo[-1].file_id
        original_filename = 'photo.jpg'
        mime_type = 'image/jpeg'
    else:
        await update.message.reply_text("Could not process the file.")
        return WAITING_FOR_FILE

    # Download file from Telegram
    new_file = await context.bot.get_file(file_id)
    file_bytes = BytesIO()
    await new_file.download_to_memory(out=file_bytes)
    file_bytes.seek(0)

    # Get user info and increment count
    name = user_data[user_id]['name']
    count = user_data[user_id].get('count', 0) + 1
    user_data[user_id]['count'] = count
    save_user_data(user_data)

    # Rename file
    ext = os.path.splitext(original_filename)[1] if original_filename else ''
    new_filename = f"{name}_{count}{ext}"

    # Upload to Google Drive
    global drive_service, pending_oauth_flow
    # If drive_service is not ready, start OAuth flow
    if drive_service is None:
        result = get_drive_service()
        if isinstance(result, tuple):
            flow, auth_url = result
            pending_oauth_flow = flow
            await update.message.reply_text(
                f"🔒 Please authorize access to Google Drive by visiting this link:\n{auth_url}\n\nAfter authorizing, please reply with the code you receive.")
            # Wait for code from user
            context.user_data['awaiting_oauth_code'] = True
            return WAITING_FOR_FILE
        else:
            drive_service = result

    file_metadata = {
        'name': new_filename,
        'parents': [DRIVE_FOLDER_ID]
    }
    media = MediaIoBaseUpload(file_bytes, mimetype=mime_type)

    drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    # Send confirmation
    await update.message.reply_text(f"✅ File \"{new_filename}\" uploaded successfully to Google Drive.\nThank you!")

    return WAITING_FOR_FILE
    # Handle OAuth code reply
    if context.user_data.get('awaiting_oauth_code') and update.message and update.message.text:
        code = update.message.text.strip()
        global pending_oauth_flow, drive_service
        try:
            pending_oauth_flow.fetch_token(code=code)
            creds = pending_oauth_flow.credentials
            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)
            drive_service = build('drive', 'v3', credentials=creds)
            context.user_data['awaiting_oauth_code'] = False
            await update.message.reply_text("✅ Google Drive authentication successful! Please resend your file.")
        except Exception as e:
            await update.message.reply_text(f"❌ Authentication failed: {e}\nPlease try again or check the code.")
        return WAITING_FOR_FILE

# Cancel handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text('Operation cancelled. Send /start to begin again.')
    return ConversationHandler.END

def main():
    # Telegram bot token from environment variable
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    if not TOKEN:
        logger.error("Please set the TELEGRAM_BOT_TOKEN environment variable")
        return

    # Check if filters is available before using it
    if filters is None:
        print("Error: 'filters' is not available in your python-telegram-bot version. Please check the documentation or update the package.")
        return

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            ASKING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_id)],
            WAITING_FOR_FILE: [MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CommandHandler('start', start)],
    )

    application.add_handler(conv_handler)

    # Use webhook mode for cloud hosting (Render)
    WEBHOOK_URL = os.getenv('WEBHOOK_URL')
    if not WEBHOOK_URL:
        logger.error("Please set the WEBHOOK_URL environment variable to your public HTTPS URL.")
        return
    # Listen on all interfaces, port 10000 (Render default), path '/'
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        url_path="/",
        webhook_url=WEBHOOK_URL
    )


if __name__ == '__main__':
    main()
