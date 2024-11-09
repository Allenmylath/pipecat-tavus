import os
import asyncio
from flask import Flask, send_file, jsonify
from loguru import logger
import sys
import threading
import queue
from bot import main as bot_main
from functools import wraps

# Thread-safe queue for URL sharing
url_queue = queue.Queue()

# Global logger configuration
logger.remove()  # Remove default handler
logger.add(
    sys.stdout,
    level="DEBUG",
    enqueue=True,  # Enable async logging
    catch=True     # Prevent errors from propagating
)

class LogInterceptor:
    def __init__(self, url_queue):
        self.url_queue = url_queue
        self._lock = threading.Lock()
    
    def __call__(self, message):
        try:
            with self._lock:
                if isinstance(message, str) and "Join the video call at:" in message:
                    url = message.split("Join the video call at:")[-1].strip()
                    self.url_queue.put(url)
                # Log to stdout directly
                print(message)  # Using print instead of logger to avoid recursion
        except Exception as e:
            print(f"Error in log interceptor: {e}")

# Initialize Flask
app = Flask(__name__)

def run_bot():
    """Run the bot and capture room URL from logs"""
    try:
        # Create a thread-specific interceptor
        interceptor = LogInterceptor(url_queue)
        log_id = logger.add(
            interceptor,
            level="DEBUG",
            enqueue=True,
            catch=True
        )
        
        try:
            asyncio.run(bot_main())
        finally:
            # Remove only the interceptor handler
            try:
                logger.remove(log_id)
            except:
                pass  # Ignore removal errors
                
    except Exception as e:
        print(f"Bot error: {e}")  # Use print for error reporting
        url_queue.put(None)  # Signal error

@app.route('/')
def index():
    """Serve the main index.html page"""
    try:
        return send_file('index.html')
    except Exception as e:
        return jsonify({"error": "Failed to load page"}), 500

@app.route('/api/get-room-url')
def get_room_url():
    """Start bot and get room URL from logs"""
    try:
        # Clear the queue before starting new bot
        while not url_queue.empty():
            url_queue.get_nowait()
            
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
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
