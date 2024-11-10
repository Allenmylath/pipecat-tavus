from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
from typing import Dict
from pydantic import BaseModel
import subprocess
import sys
from loguru import logger

# Configure logger
logger.remove()
logger.add(sys.stderr, level="INFO")

app = FastAPI(title="Bot Management API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store bot process info
bot_process: Dict[str, subprocess.Popen] = {}

class BotStatus(BaseModel):
    status: str
    pid: int = None

@app.post("/start", response_model=BotStatus)
async def start_bot():
    """Start the bot process"""
    try:
        if bot_process.get('process'):
            # Bot is already running
            return BotStatus(
                status="already running",
                pid=bot_process['process'].pid
            )
        
        # Start bot.py as a separate process
        process = subprocess.Popen(
            [sys.executable, "bot.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        bot_process['process'] = process
        
        return BotStatus(
            status="started",
            pid=process.pid
        )
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status", response_model=BotStatus)
async def get_status():
    """Check if bot is running"""
    if not bot_process.get('process'):
        return BotStatus(status="not running")
    
    process = bot_process['process']
    # Check if process is still running
    if process.poll() is None:
        return BotStatus(
            status="running",
            pid=process.pid
        )
    else:
        # Process has ended
        stdout, stderr = process.communicate()
        if stderr:
            logger.error(f"Bot error output: {stderr}")
        bot_process.clear()
        return BotStatus(status="stopped")

@app.post("/stop", response_model=BotStatus)
async def stop_bot():
    """Stop the bot process"""
    if not bot_process.get('process'):
        return BotStatus(status="not running")
    
    try:
        process = bot_process['process']
        process.terminate()
        process.wait(timeout=5)  # Wait up to 5 seconds for graceful shutdown
        bot_process.clear()
        return BotStatus(status="stopped")
    except subprocess.TimeoutExpired:
        # Force kill if graceful shutdown fails
        process.kill()
        process.wait()
        bot_process.clear()
        return BotStatus(status="force stopped")
    except Exception as e:
        logger.error(f"Failed to stop bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    if bot_process.get('process'):
        await stop_bot()
    logger.info("Server shutting down")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )
