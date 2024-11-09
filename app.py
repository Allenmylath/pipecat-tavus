# app.py
import os
import asyncio
import subprocess
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
import sys
import threading
import queue
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

# Thread-safe queue for URL sharing
url_queue = queue.Queue()
# Track active bot processes
bot_procs = {}

class LogInterceptor:
    def __init__(self, url_queue):
        self.url_queue = url_queue
        self._lock = threading.Lock()
    
    def __call__(self, message):
        try:
            with self._lock:
                if isinstance(message, str):
                    if "TavusVideoService joined" in message:
                        url = message.split("TavusVideoService joined")[-1].strip()
                        self.url_queue.put(url)
                    print(message)
        except Exception as e:
            print(f"Error in log interceptor: {e}")

def run_bot():
    try:
        interceptor = LogInterceptor(url_queue)
        log_id = logger.add(
            interceptor,
            level="DEBUG",
            enqueue=True,
            catch=True
        )
        
        try:
            proc = subprocess.Popen(
                ["python3", "-m", "bot"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                bufsize=1,
                text=True
            )
            
            bot_procs[proc.pid] = proc
            
            try:
                proc_url = url_queue.get(timeout=30)
                if proc_url:
                    return proc_url, proc.pid
            except queue.Empty:
                proc.terminate()
                raise TimeoutError("Timeout waiting for room URL")
            
        finally:
            logger.remove(log_id)
            
    except Exception as e:
        logger.error(f"Bot error: {e}")
        return None, None

async def cleanup():
    for proc in bot_procs.values():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up FastAPI application")
    yield
    logger.info("Shutting down FastAPI application")
    await cleanup()

# Initialize FastAPI with lifespan
app = FastAPI(lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

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
        raise HTTPException(status_code=500, detail="Failed to load page")

@app.get("/api/get-room-url")
async def get_room_url():
    try:
        while not url_queue.empty():
            url_queue.get_nowait()
        
        url, pid = run_bot()
        
        if url and pid:
            return {"url": url, "bot_pid": pid}
        else:
            raise HTTPException(status_code=500, detail="Failed to generate room URL")
            
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status/{pid}")
async def get_status(pid: int):
    try:
        proc = bot_procs.get(pid)
        if not proc:
            raise HTTPException(status_code=404, detail="Bot not found")
        
        if proc.poll() is None:
            status = "running"
        else:
            status = "finished"
            bot_procs.pop(pid, None)
            
        return {"bot_pid": pid, "status": status}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking bot status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "healthy"}
