"""Entry point to run the Bench Forecast API server.

Start with:
    python server.py

Or with uvicorn directly:
    uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
"""

import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import uvicorn
from src.core.config import Config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.info("Starting Bench Forecast API...")
    logger.info(f"LLM Provider: {Config.LLM_PROVIDER}")
    
    uvicorn.run(
        "src.api.main:app",
        host=Config.API_HOST,
        port=Config.API_PORT,
        reload=Config.DEBUG,
        log_level="info",
    )
