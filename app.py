import os
import asyncio
import subprocess
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from loguru import logger
import sys
import threading
import queue
from contextlib import asynccontextmanager
from bot import main as bot_main
import uvicorn

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
    """Custom log interceptor to capture the Daily room URL from Tavus"""
    def __init__(self, url_queue):
        self.url_queue = url_queue
        self._lock = threading.Lock()
    
    def __call__(self, message):
        try:
            with self._lock:
                if isinstance(message, str):
                    # Capture the Daily URL from Tavus service logs
                    if "TavusVideoService joined" in message:
                        url = message.split("TavusVideoService joined")[-1].strip()
                        self.url_queue.put(url)
                    print(message)
        except Exception as e:
            print(f"Error in log interceptor: {e}")

def run_bot():
    """Run the bot in a separate process"""
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
            # Run the bot as a subprocess to avoid signal handling issues
            proc = subprocess.Popen(
                ["python3", "-m", "bot"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                bufsize=1,
                text=True
            )
            
            # Store the process
            bot_procs[proc.pid] = proc
            
            # Wait for URL or timeout
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
    """Cleanup function for terminating any running bots"""
    for proc in bot_procs.values():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up FastAPI application")
    yield
    # Shutdown
    logger.info("Shutting down FastAPI application")
    await cleanup()

# Initialize FastAPI with lifespan
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
    """Serve the main index.html page"""
    try:
        return FileResponse('index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        raise HTTPException(status_code=500, detail="Failed to load page")

@app.get("/api/get-room-url")
async def get_room_url():
    """Start bot and get room URL from Tavus"""
    try:
        # Clear any existing URLs from the queue
        while not url_queue.empty():
            url_queue.get_nowait()
        
        # Start the bot and get the URL
        url, pid = run_bot()
        
        if url and pid:
            return JSONResponse({
                "url": url,
                "bot_pid": pid
            })
        else:
            raise HTTPException(status_code=500, detail="Failed to generate room URL")
            
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status/{pid}")
async def get_status(pid: int):
    """Get the status of a specific bot process"""
    try:
        proc = bot_procs.get(pid)
        if not proc:
            raise HTTPException(status_code=404, detail="Bot not found")
        
        # Check process status
        if proc.poll() is None:
            status = "running"
        else:
            status = "finished"
            # Clean up finished process
            bot_procs.pop(pid, None)
            
        return JSONResponse({
            "bot_pid": pid,
            "status": status
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking bot status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    """Health check endpoint"""
    return JSONResponse({"status": "healthy"})

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Tavus-Pipecat FastAPI server")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", 5000)), help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    
    args = parser.parse_args()
    
    logger.info(f"Starting server at http://{args.host}:{args.port}")
    
    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )
