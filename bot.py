import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
import mimetypes
import os
import mimetypes
import json
import pickle
from io import BytesIO
from flask import Flask, request, redirect, session
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters
from dotenv import load_dotenv


# ...existing code...
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "supersecretkey123!@#"

# Homepage route for Flask
@app.route("/")
def home():
    return "Flask server is running. Use /login for Google authentication."

@app.route("/login")
def login():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uris": [GOOGLE_REDIRECT_URI + "/callback"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=["https://www.googleapis.com/auth/drive.file"],
        redirect_uri=GOOGLE_REDIRECT_URI + "/callback"
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true"
    )
    session["state"] = state
    return redirect(authorization_url)
load_dotenv()


GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI") or 'http://localhost:8080'
ASK_ID, ASK_MONTH, ASK_FILE = range(3)

# Load user data from JSON
DATA_FILE = 'user_data.json'
def load_user_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    else:
        return {}
user_data = load_user_data()

# Get Google Drive folder ID from environment
DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID')

# Validate GOOGLE_REDIRECT_URI
if not GOOGLE_REDIRECT_URI:
    # Fallback to localhost for development if not set
    GOOGLE_REDIRECT_URI = 'http://localhost:8080'

# ...existing code...

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        msg = (
            "✨ የሐይመተ አብርሃም የዜማ ተማሪዎች ደረሰኝ መቀበያ ✨\n\n"
            "በዚህ ቦት የዜማ ተማሪዎች ደረሰኞችን ማስቀመጥ ይችላሉ።\n\n"
            "ትእዛዞች:\n"
            "• /start - አዲስ ስብስ መጀመሪያ ይጀምሩ\n"
            "• /stop ወይም /cancel - የአሁኑን ስብስ ያቁሙ\n\n"
            "እንጀምር!\n\n"
            "ሰላም! 👋 እባክዎን የተማሪውን መለያ ቁጥር ያስገቡ (ለምሳሌ፡ 001)።"
        )
        await update.message.reply_text(msg)
        return ASK_ID
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        msg = (
            "🛑 ስብስ ቆሟል።\n\n"
            "አዲስ ስብስ ለመጀመር /start ይጠቀሙ።"
        )
        await update.message.reply_text(msg)
    return ConversationHandler.END

async def ask_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message and update.message.text:
        user_id = update.message.text.strip()
        name = user_data.get(user_id, {}).get('name')
        if not name:
            await update.message.reply_text("❌ User ID not found. Please try again.")
            return ASK_ID
        if context.user_data is None:
            context.user_data = {}
        context.user_data['user_id'] = user_id
        context.user_data['name'] = name
        await update.message.reply_text(f"✅ Got it. The user is {name}.\nPlease enter the month for the document (e.g., January).")

        return ASK_MONTH
    return ConversationHandler.END

async def ask_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message and update.message.text:
        month = update.message.text.strip()
        if context.user_data is None:
            context.user_data = {}
        context.user_data['month'] = month
        await update.message.reply_text("📁 Great! Please upload the PDF file you want to rename and upload.")
        return ASK_FILE
    return ConversationHandler.END

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    print("handle_file called")
    if update.message:
        print("update.message received")
        document = getattr(update.message, 'document', None)
        photo = getattr(update.message, 'photo', None)
        file_name = None
        is_document = document is not None
        is_photo = photo is not None and isinstance(photo, list) and len(photo) > 0

        if not is_document and not is_photo:
            await update.message.reply_text("❌ Please upload a file or image.")
            print("No valid file or photo found in message")
            return ASK_FILE

        if context.user_data is None:
            print("context.user_data is None, initializing")
            context.user_data = {}
        print(f"context.user_data: {context.user_data}")
        user_id = context.user_data.get('user_id')
        name = context.user_data.get('name')
        month = context.user_data.get('month')
        print(f"user_id: {user_id}, name: {name}, month: {month}")
        if not user_id or not name or not month:
            await update.message.reply_text("❌ Missing user info. Please start again with /start.")
            return ASK_ID

        if is_document:
            original_filename = getattr(document, 'file_name', 'uploaded.file')
            ext = original_filename.split('.')[-1].lower() if '.' in original_filename else ''
            # Supported compressed extensions
            compressed_exts = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz']
            # Try to get mime type from Telegram document
            mime_type = getattr(document, 'mime_type', None)
            is_image_doc = mime_type and mime_type.startswith('image/')
            if is_image_doc:
                new_filename = f"{name}_{month}.{ext}" if ext else f"{name}_{month}.jpg"
                await update.message.reply_text(f"🔄 Renaming {original_filename} to {new_filename}...\n📤 Uploading image to Google Drive...")
            elif ext in compressed_exts:
                new_filename = f"{name}_{month}.{ext}"
                await update.message.reply_text(f"🔄 Renaming {original_filename} to {new_filename}...\n📤 Uploading compressed archive to Google Drive...")
            else:
                new_filename = f"{name}_{month}.{ext}" if ext else f"{name}_{month}"
                await update.message.reply_text(f"🔄 Renaming {original_filename} to {new_filename}...\n📤 Uploading to Google Drive...")
            creds = None
            if os.path.exists('token.pickle'):
                with open('token.pickle', 'rb') as token:
                    creds = pickle.load(token)
            print(f"creds: {creds}")
            if not creds or not creds.valid:
                login_url = f"{GOOGLE_REDIRECT_URI}/login"
                print(f"Sending auth link: {login_url}")
                await update.message.reply_text(f"🔒 Please authenticate with Google Drive: {login_url}")
                return ASK_FILE
            file_id = getattr(document, 'file_id', None)
            if file_id:
                new_file = await context.bot.get_file(file_id)
                file_bytes = BytesIO()
                await new_file.download_to_memory(out=file_bytes)
                file_bytes.seek(0)
                print("Uploading file to Google Drive...")
                drive_service = build('drive', 'v3', credentials=creds)
                file_metadata = {'name': new_filename, 'parents': [DRIVE_FOLDER_ID]}
                # Set mime type for compressed files or images
                if is_image_doc:
                    mime_type = mime_type or 'image/jpeg'
                elif ext in compressed_exts:
                    mime_type = mimetypes.guess_type(new_filename)[0] or 'application/zip'
                else:
                    mime_type = mimetypes.guess_type(new_filename)[0] or 'application/octet-stream'
                media = MediaIoBaseUpload(file_bytes, mimetype=mime_type)
                drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                await update.message.reply_text(f"✅ Upload successful!\nFile {new_filename} has been uploaded to your Google Drive. 🎉")
                print("File upload complete, restarting conversation")
                await start(update, context)
                return ASK_ID
            else:
                await update.message.reply_text("❌ Could not retrieve file ID.")
                print("No file_id for document")
                return ASK_FILE
        elif is_photo:
            new_filename = f"{name}_{month}.jpg"
            await update.message.reply_text(f"🔄 Renaming image to {new_filename}...\n📤 Uploading to Google Drive...")
            creds = None
            if os.path.exists('token.pickle'):
                with open('token.pickle', 'rb') as token:
                    creds = pickle.load(token)
            print(f"creds: {creds}")
            if not creds or not creds.valid:
                login_url = f"{GOOGLE_REDIRECT_URI}/login"
                print(f"Sending auth link: {login_url}")
                await update.message.reply_text(f"🔒 Please authenticate with Google Drive: {login_url}")
                return ASK_FILE
            photo_file_id = getattr(photo[-1], 'file_id', None) if photo and isinstance(photo, list) and len(photo) > 0 else None
            if photo_file_id:
                photo_file = await context.bot.get_file(photo_file_id)
                file_bytes = BytesIO()
                await photo_file.download_to_memory(out=file_bytes)
                file_bytes.seek(0)
                print("Uploading image to Google Drive...")
                drive_service = build('drive', 'v3', credentials=creds)
                file_metadata = {'name': new_filename, 'parents': [DRIVE_FOLDER_ID]}
                media = MediaIoBaseUpload(file_bytes, mimetype='image/jpeg')
                drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                await update.message.reply_text(f"✅ Upload successful!\nImage {new_filename} has been uploaded to your Google Drive. 🎉")
                print("Image upload complete, restarting conversation")
                await start(update, context)
                return ASK_ID
            else:
                await update.message.reply_text("❌ Could not retrieve photo file ID.")
                print("No file_id for photo")
                return ASK_FILE
    print("No update.message, ending conversation")
    return ConversationHandler.END
@app.route("/callback")
def callback():
    try:
        state = session["state"]
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uris": [GOOGLE_REDIRECT_URI + "/callback"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token"
                }
            },
            scopes=["https://www.googleapis.com/auth/drive.file"],
            state=state,
            redirect_uri=GOOGLE_REDIRECT_URI + "/callback"
        )
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials
        with open('token.pickle', 'wb') as token:
            pickle.dump(credentials, token)
        return "Google Drive authentication successful! You can return to Telegram and upload your file."
    except Exception as e:
        import traceback
        error_message = f"Error during callback: {str(e)}\n{traceback.format_exc()}"
        print(error_message)
        return error_message, 500

# ...existing code for main, ConversationHandler, etc...
def main():
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    if not telegram_token:
        raise ValueError("TELEGRAM_TOKEN environment variable is not set.")

    application = Application.builder().token(telegram_token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_month)],
            ASK_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_file)],
            ASK_FILE: [MessageHandler(filters.ALL, handle_file)],
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("cancel", cancel))
    # Use polling for local, webhook for production
    if os.environ.get("RUN_LOCAL", "1") == "1":
        application.run_polling()
    else:
        webhook_url = os.environ.get("WEBHOOK_URL")
        listen_port = int(os.environ.get("PORT", 8080))
        telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
        application.run_webhook(
            listen="0.0.0.0",
            port=listen_port,
            url_path=telegram_token,
            webhook_url=f"{webhook_url}/{telegram_token}"
        )

if __name__ == "__main__":
    # Load .env variables before checking RUN_LOCAL
    from dotenv import load_dotenv
    load_dotenv()
    # Always start Flask in a thread so both OAuth and webhook endpoints are available
    import threading

    def run_flask():
        port = int(os.environ.get("PORT", 8080))
        app.run(port=port)

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    main()