import os
import asyncio
import aiohttp
from flask import Flask, send_file, jsonify
from dotenv import load_dotenv
from pipecat.services.tavus import TavusVideoService
from loguru import logger
import sys

# Load environment variables
load_dotenv(override=True)

# Configure logger
logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

app = Flask(__name__)

async def generate_room_url():
    """Generate a new room URL using Tavus"""
    async with aiohttp.ClientSession() as session:
        tavus = TavusVideoService(
            api_key=os.getenv("TAVUS_API_KEY"),
            replica_id=os.getenv("TAVUS_REPLICA_ID"),
            persona_id=os.getenv("TAVUS_PERSONA_ID", "pipecat0"),
            session=session,
        )
        
        # Initialize and get room URL
        room_url = await tavus.initialize()
        logger.info(f"Generated room URL: {room_url}")
        return room_url

def sync_generate_room_url():
    """Synchronous wrapper for generate_room_url"""
    return asyncio.run(generate_room_url())

@app.route('/')
def index():
    """Serve the main index.html page"""
    return send_file('index.html')

@app.route('/api/get-room-url')
def get_room_url():
    """Generate and return a new room URL"""
    try:
        room_url = sync_generate_room_url()
        return jsonify({"url": room_url})
    except Exception as e:
        logger.error(f"Error generating room URL: {e}")
        return jsonify({"error": "Failed to generate room URL"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
