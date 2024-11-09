import os
import asyncio
import subprocess
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from loguru import logger
import sys
import threading
import queue
from contextlib import asynccontextmanager
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
            logger.error(f"Error in log interceptor: {e}")

async def run_bot_async():
    """Asynchronous bot runner"""
    try:
        interceptor = LogInterceptor(url_queue)
        log_id = logger.add(
            interceptor,
            level="DEBUG",
            enqueue=True,
            catch=True
        )
        
        try:
            # Create subprocess
            proc = await asyncio.create_subprocess_exec(
                "python3",
                "-m",
                "bot",
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Store the process
            bot_procs[proc.pid] = proc
            
            # Set a shorter timeout for initial URL generation
            url_future = asyncio.get_event_loop().run_in_executor(
                None, 
                url_queue.get, 
                True,  # block
                15     # timeout seconds
            )
            
            try:
                url = await url_future
                return url, proc.pid
            except (asyncio.TimeoutError, queue.Empty):
                logger.error("Timeout waiting for room URL")
                if proc.pid in bot_procs:
                    await cleanup_process(proc.pid)
                raise TimeoutError("Bot failed to generate room URL in time")
                
        finally:
            logger.remove(log_id)
            
    except Exception as e:
        logger.error(f"Bot error: {e}")
        return None, None

async def cleanup_process(pid: int):
    """Clean up a specific bot process"""
    proc = bot_procs.pop(pid, None)
    if proc:
        try:
            proc.terminate()
            await proc.wait()
        except Exception as e:
            logger.error(f"Error cleaning up process {pid}: {e}")

async def cleanup():
    """Cleanup all bot processes"""
    for pid in list(bot_procs.keys()):
        await cleanup_process(pid)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up FastAPI application")
    yield
    logger.info("Shutting down FastAPI application")
    await cleanup()

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
    try:
        return FileResponse('index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        raise HTTPException(status_code=500, detail="Failed to load page")

@app.get("/api/get-room-url")
async def get_room_url(background_tasks: BackgroundTasks):
    """Start bot and get room URL from Tavus"""
    try:
        # Clear queue
        while not url_queue.empty():
            url_queue.get_nowait()
        
        # Run bot asynchronously
        url, pid = await run_bot_async()
        
        if url and pid:
            # Add cleanup task to run after response is sent
            background_tasks.add_task(cleanup_process, pid)
            return JSONResponse({
                "url": url,
                "bot_pid": pid,
                "status": "success"
            })
        else:
            raise HTTPException(
                status_code=500, 
                detail="Failed to generate room URL"
            )
            
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status/{pid}")
async def get_status(pid: int):
    try:
        proc = bot_procs.get(pid)
        if not proc:
            raise HTTPException(status_code=404, detail="Bot not found")
        
        # Check if process is still running
        if proc.returncode is None:
            status = "running"
        else:
            status = "finished"
            # Clean up finished process
            await cleanup_process(pid)
            
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
    return JSONResponse({"status": "healthy"})

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Tavus-Pipecat FastAPI server")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", 5000)))
    parser.add_argument("--reload", action="store_true")
    
    args = parser.parse_args()
    
    logger.info(f"Starting server at http://{args.host}:{args.port}")
    
    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )
