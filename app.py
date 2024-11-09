from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from loguru import logger
import sys
import threading
import asyncio
import os
from contextlib import asynccontextmanager
from bot import main as bot_main

# Configure logger
logger.remove()
logger.add(
    sys.stdout,
    level="DEBUG",
    enqueue=True,
    catch=True
)

# Global tracking
bot_procs = {}

class BotRunner:
    def __init__(self):
        self.room_url = None
        self.event = asyncio.Event()
        
    async def run(self):
        try:
            # Run the bot and capture URL directly from main()
            self.room_url = await bot_main()
            self.event.set()
        except Exception as e:
            logger.error(f"Bot error: {e}")
            self.event.set()

def run_bot_thread():
    """Run the bot in a separate thread"""
    runner = BotRunner()
    try:
        # Create new event loop for the thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the bot
        loop.run_until_complete(runner.run())
    except Exception as e:
        logger.error(f"Thread error: {e}")
    finally:
        loop.close()
    
    return runner

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Cleanup
    for proc in bot_procs.values():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except:
            pass

# Initialize FastAPI
app = FastAPI(lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def index():
    try:
        return FileResponse('index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return JSONResponse({"error": "Failed to load page"}, status_code=500)

@app.get("/api/get-room-url")
async def get_room_url():
    try:
        # Start bot in a thread
        bot_thread = threading.Thread(target=run_bot_thread)
        bot_thread.daemon = True
        bot_thread.start()
        
        # Wait for the bot to generate URL (maximum 30 seconds)
        start_time = asyncio.get_event_loop().time()
        while bot_thread.is_alive():
            if asyncio.get_event_loop().time() - start_time > 30:
                raise TimeoutError("Timeout waiting for room URL")
            await asyncio.sleep(0.1)
            
        # If thread finished but no URL, something went wrong
        if not bot_thread.is_alive():
            raise Exception("Bot failed to generate URL")
            
        return JSONResponse({"url": bot_thread.room_url})
            
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy"})
