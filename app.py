import os
import asyncio
from flask import Flask, send_file, jsonify
from loguru import logger
import sys
import threading
import queue
from bot import main as bot_main

# Configure logger
logger.remove()
logger.add(sys.stdout, level="DEBUG")

# Initialize Flask with import name
app = Flask(__name__)

url_queue = queue.Queue()

def run_bot():
    """Run the bot and capture room URL from logs"""
    def log_interceptor(message):
        if "Join the video call at:" in message:
            url = message.split("Join the video call at:")[-1].strip()
            url_queue.put(url)
        logger.info(message)

    logger.add(log_interceptor)
    try:
        asyncio.run(bot_main())
    except Exception as e:
        logger.error(f"Bot error: {e}")
        url_queue.put(None)  # Signal error

@app.route('/')
def index():
    """Serve the main index.html page"""
    try:
        return send_file('index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Failed to load page"}), 500

@app.route('/api/get-room-url')
def get_room_url():
    """Start bot and get room URL from logs"""
    try:
        # Start bot in a separate thread
        bot_thread = threading.Thread(target=run_bot)
        bot_thread.daemon = True
        bot_thread.start()
        
        # Wait for URL from logger
        try:
            room_url = url_queue.get(timeout=30)  # Increased timeout
            if room_url is None:
                raise Exception("Bot failed to generate URL")
            return jsonify({"url": room_url})
        except queue.Empty:
            raise TimeoutError("Timeout waiting for room URL")
            
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        return jsonify({"error": str(e)}), 500

# Add health check endpoint
@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
