import os
import asyncio
from flask import Flask, send_file, jsonify
from loguru import logger
import sys
import threading
import queue
from bot import main as bot_main
import signal
import functools

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
                print(message)  # Using print instead of logger
        except Exception as e:
            print(f"Error in log interceptor: {e}")

# Initialize Flask
app = Flask(__name__)

def run_in_main_thread(f):
    """Decorator to ensure a function runs in the main thread"""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not isinstance(threading.current_thread(), threading._MainThread):
            # If we're not in the main thread, use a queue to run in main thread
            result_queue = queue.Queue()
            def main_thread_func():
                try:
                    result = f(*args, **kwargs)
                    result_queue.put(('result', result))
                except Exception as e:
                    result_queue.put(('error', e))
            
            # Schedule execution in main thread
            app.config['main_queue'].put(main_thread_func)
            
            # Wait for result
            result_type, result = result_queue.get()
            if result_type == 'error':
                raise result
            return result
        return f(*args, **kwargs)
    return wrapper

def process_main_queue():
    """Process functions queued for main thread execution"""
    try:
        while True:
            try:
                # Non-blocking get
                func = app.config['main_queue'].get_nowait()
                func()
            except queue.Empty:
                break
    except Exception as e:
        logger.error(f"Error processing main queue: {e}")

@run_in_main_thread
def run_async_bot():
    """Run the bot in the main thread"""
    asyncio.run(bot_main())

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
            # Run the bot in the main thread
            run_async_bot()
        finally:
            try:
                logger.remove(log_id)
            except:
                pass
                
    except Exception as e:
        print(f"Bot error: {e}")
        url_queue.put(None)  # Signal error

@app.route('/')
def index():
    """Serve the main index.html page"""
    try:
        # Process any pending main thread tasks
        process_main_queue()
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
            
        # Process any pending main thread tasks
        process_main_queue()
            
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

def init_app():
    """Initialize the application"""
    # Create queue for main thread execution
    app.config['main_queue'] = queue.Queue()
    
    # Disable signal handling in sub-threads
    signal.signal(signal.SIGINT, signal.SIG_IGN)

if __name__ == "__main__":
    init_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
