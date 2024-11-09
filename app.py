import os
import asyncio
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from loguru import logger
import sys
import threading
import queue
from bot import main as bot_main

# Configure logger
logger.remove()
logger.add(
    sys.stdout,
    level="DEBUG",
    enqueue=True,
    catch=True
)

# Thread-safe queue for URL sharing
url_queue = queue.Queue()
# Track active bot processes
bot_procs = {}

def run_bot():
    """Run the bot and capture room URL from logs"""
    def log_interceptor(message):
        if "TavusVideoService joined" in message:
            url = message.split("TavusVideoService joined")[-1].strip()
            url_queue.put(url)
        logger.info(message)
    
    logger.add(log_interceptor)
    try:
        asyncio.run(bot_main())
    except Exception as e:
        logger.error(f"Bot error: {e}")
        url_queue.put(None)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Cleanup on shutdown
    for proc in bot_procs.values():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except:
            pass

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def index():
    """Serve the main index.html page"""
    try:
        return FileResponse('index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return JSONResponse({"error": "Failed to load page"}, status_code=500)

@app.get("/api/get-room-url")
async def get_room_url():
    """Start bot and get room URL from logs"""
    try:
        # Clear any existing URLs from the queue
        while not url_queue.empty():
            url_queue.get_nowait()
        
        # Start bot in a separate thread
        bot_thread = threading.Thread(target=run_bot)
        bot_thread.daemon = True
        bot_thread.start()
        
        # Wait for URL from logger
        try:
            room_url = url_queue.get(timeout=30)  # Increased timeout
            if room_url is None:
                raise Exception("Bot failed to generate URL")
            return JSONResponse({"url": room_url})
        except queue.Empty:
            raise TimeoutError("Timeout waiting for room URL")
            
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/health")
async def health():
    """Health check endpoint"""
    return JSONResponse({"status": "healthy"})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
