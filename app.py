import os
import asyncio
from flask import Flask, send_file, jsonify
from loguru import logger
import sys
import threading
import queue
from bot import main as bot_main
from functools import wraps

# Thread-local storage
thread_local = threading.local()

def configure_thread_logger():
    """Configure logger for the current thread if not already configured"""
    if not hasattr(thread_local, "logger_configured"):
        # Remove any existing handlers
        logger.remove()
        # Add stdout handler with thread-safe configuration
        logger.add(
            sys.stdout,
            level="DEBUG",
            enqueue=True,  # Enable async logging
            serialize=True  # Prevent concurrent access
        )
        thread_local.logger_configured = True

def safe_logging(func):
    """Decorator to ensure thread-safe logging"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        configure_thread_logger()
        return func(*args, **kwargs)
    return wrapper

# Initialize Flask
app = Flask(__name__)

# Use a thread-safe queue for URL sharing
url_queue = queue.Queue()

class LogInterceptor:
    def __init__(self, url_queue):
        self.url_queue = url_queue
        self._lock = threading.Lock()
    
    def __call__(self, message):
        with self._lock:
            if "Join the video call at:" in message:
                url = message.split("Join the video call at:")[-1].strip()
                self.url_queue.put(url)
            # Use the thread-local logger
            configure_thread_logger()
            logger.info(message)

@safe_logging
def run_bot():
    """Run the bot and capture room URL from logs"""
    try:
        # Create a thread-specific interceptor
        interceptor = LogInterceptor(url_queue)
        # Add interceptor with thread-safe configuration
        logger.add(
            interceptor,
            enqueue=True,
            serialize=True,
            level="DEBUG"
        )
        
        asyncio.run(bot_main())
    except Exception as e:
        logger.error(f"Bot error: {e}")
        url_queue.put(None)  # Signal error
    finally:
        # Clean up logger handlers
        logger.remove()

@app.route('/')
@safe_logging
def index():
    """Serve the main index.html page"""
    try:
        return send_file('index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Failed to load page"}), 500

@app.route('/api/get-room-url')
@safe_logging
def get_room_url():
    """Start bot and get room URL from logs"""
    try:
        # Clear the queue before starting new bot
        while not url_queue.empty():
            url_queue.get()
            
        # Start bot in a separate thread
        bot_thread = threading.Thread(target=run_bot)
        bot_thread.daemon = True
        bot_thread.start()
        
        # Wait for URL from logger
        try:
            room_url = url_queue.get(timeout=30)
            if room_url is None:
                raise Exception("Bot failed to generate URL")
            return jsonify({"url": room_url})
        except queue.Empty:
            raise TimeoutError("Timeout waiting for room URL")
            
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
@safe_logging
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    # Configure initial logger
    configure_thread_logger()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
