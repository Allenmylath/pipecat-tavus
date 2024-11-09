import os
import asyncio
from flask import Flask, send_file, jsonify
from loguru import logger
import sys
import threading
from bot import main as bot_main
import queue

# Configure logger
logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

app = Flask(__name__)
url_queue = queue.Queue()

def run_bot():
    """Run the bot and capture room URL from logs"""
    def log_interceptor(message):
        """Intercept logger messages to capture room URL"""
        if "Join the video call at:" in message:
            url = message.split("Join the video call at:")[-1].strip()
            url_queue.put(url)
        logger.info(message)

    # Add our interceptor to logger
    logger.add(log_interceptor)
    
    # Run the bot
    asyncio.run(bot_main())

@app.route('/')
def index():
    """Serve the main index.html page"""
    return send_file('index.html')

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
            room_url = url_queue.get(timeout=10)  # 10 second timeout
            return jsonify({"url": room_url})
        except queue.Empty:
            raise TimeoutError("Timeout waiting for room URL")
            
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        return jsonify({"error": "Failed to start bot"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
