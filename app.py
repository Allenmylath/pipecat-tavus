import os
import asyncio
import subprocess
import time
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from loguru import logger
import sys
import threading
import queue
from contextlib import asynccontextmanager
import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

# Configure logger
logger.remove()
logger.add(
    sys.stdout,
    level="DEBUG",
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    enqueue=True,
    catch=True
)

# Queue for room URLs
url_queue = queue.Queue()
# Track bot processes
bot_procs = {}

class LogInterceptor:
    def __init__(self, url_queue):
        self.url_queue = url_queue
        self._lock = threading.Lock()
    
    def __call__(self, message):
        try:
            with self._lock:
                if isinstance(message, str):
                    logger.debug(f"Bot output: {message}")
                    if "Join the video call at:" in message:
                        url = message.split("Join the video call at:")[-1].strip()
                        logger.info(f"Found room URL: {url}")
                        self.url_queue.put(url)
        except Exception as e:
            logger.error(f"Error in log interceptor: {e}")

def run_bot(room_url=None, token=None):
    """Run the medical intake bot"""
    proc = None
    try:
        logger.info("Starting medical intake bot...")
        
        # Verify required environment variables
        required_vars = [
            "TAVUS_API_KEY",
            "TAVUS_REPLICA_ID",
            "OPENAI_API_KEY",
            "DEEPGRAM_API_KEY",
            "CARTESIA_API_KEY",
            "FIREBASE_CREDENTIALS"
        ]
        
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        # Construct the bot command
        cmd = f"{sys.executable} -m bot"
        if room_url:
            cmd += f" -u {room_url}"
        if token:
            cmd += f" -t {token}"
            
        logger.debug(f"Running command: {cmd}")
        
        # Start the bot process
        proc = subprocess.Popen(
            [cmd],
            shell=True,
            bufsize=1,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=os.environ.copy()  # Pass current environment variables
        )
        
        logger.info(f"Bot process started with PID: {proc.pid}")
        bot_procs[proc.pid] = proc
        
        # Start output reading threads
        def read_output(pipe, prefix):
            try:
                for line in pipe:
                    clean_line = line.strip()
                    logger.debug(f"{prefix}: {clean_line}")
                    
                    # Look for room URL in the output
                    if "Join the video call at:" in clean_line:
                        url = clean_line.split("Join the video call at:")[-1].strip()
                        logger.info(f"Found room URL: {url}")
                        url_queue.put(url)
            except Exception as e:
                logger.error(f"Error reading {prefix}: {e}")
        
        threading.Thread(target=read_output, args=(proc.stdout, "STDOUT"), daemon=True).start()
        threading.Thread(target=read_output, args=(proc.stderr, "STDERR"), daemon=True).start()
        
        # Wait for URL with periodic checks
        start_time = time.time()
        timeout = 25  # Increased timeout since this bot needs more startup time
        check_interval = 0.5
        
        while time.time() - start_time < timeout:
            # Check if process is still running
            if proc.poll() is not None:
                returncode = proc.poll()
                logger.error(f"Bot process exited with code: {returncode}")
                # Get any remaining output
                stdout, stderr = proc.communicate()
                logger.error(f"Final stdout: {stdout}")
                logger.error(f"Final stderr: {stderr}")
                raise RuntimeError(f"Bot process exited unexpectedly with code {returncode}")
            
            # Try to get URL
            try:
                url = url_queue.get_nowait()
                logger.info(f"Successfully got room URL: {url}")
                return url, proc.pid
            except queue.Empty:
                time.sleep(check_interval)
                continue
        
        # Timeout reached
        logger.error("Timeout reached waiting for room URL")
        proc.terminate()
        raise TimeoutError("Timeout waiting for room URL")
        
    except Exception as e:
        logger.error(f"Bot error: {e}")
        if proc and proc.poll() is None:
            proc.terminate()
        return None, None

async def cleanup_process(pid: int):
    """Clean up a specific bot process"""
    proc = bot_procs.pop(pid, None)
    if proc:
        logger.info(f"Cleaning up process {pid}")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f"Process {pid} did not terminate, forcing kill")
                proc.kill()
                proc.wait()
        except Exception as e:
            logger.error(f"Error cleaning up process {pid}: {e}")
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

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
    """Serve the main index.html page"""
    try:
        return FileResponse('index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        raise HTTPException(status_code=500, detail="Failed to load page")

@app.get("/api/get-room-url")
async def get_room_url(background_tasks: BackgroundTasks, room_url: str = None, token: str = None):
    """Start bot and get room URL from Tavus"""
    try:
        # Clear queue
        while not url_queue.empty():
            url_queue.get_nowait()
        
        logger.info("Starting bot process for room URL generation")
        url, pid = run_bot(room_url, token)
        
        if url and pid:
            logger.info(f"Successfully generated room URL with bot PID: {pid}")
            background_tasks.add_task(cleanup_process, pid)
            return JSONResponse({
                "url": url,
                "bot_pid": pid,
                "status": "success"
            })
        else:
            logger.error("Failed to generate room URL")
            raise HTTPException(
                status_code=500, 
                detail="Failed to generate room URL"
            )
            
    except TimeoutError as e:
        logger.error(f"Timeout error: {e}")
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"Error in get_room_url: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status/{pid}")
async def get_status(pid: int):
    """Get the status of a specific bot process"""
    try:
        proc = bot_procs.get(pid)
        if not proc:
            raise HTTPException(status_code=404, detail="Bot not found")
        
        # Check if process is still running
        if proc.poll() is None:
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
    """Health check endpoint"""
    return JSONResponse({"status": "healthy"})

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Medical Intake Bot FastAPI server")
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
