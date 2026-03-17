import logging
import os
from s3_logger import S3Logger

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_logs_to_file(local_dir: str = "downloaded_logs"):
    """
    Fetches all logs from S3 logs directory and stores them locally.
    Preserves the directory structure from S3.
    """
    s3_logger = S3Logger()
    
    logger.info("Listing logs from S3...")
    log_keys = s3_logger.list_logs(prefix="logs/")
    
    if not log_keys:
        logger.info("No logs found in S3.")
        return

    logger.info(f"Found {len(log_keys)} log files.")
    
    for key in log_keys:
        # Construct local path, preserving S3 structure
        # Key format: logs/passport/date/session.log
        local_path = os.path.join(local_dir, key)
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        if s3_logger.download_log(key, local_path):
            logger.info(f"Downloaded: {key} -> {local_path}")
        else:
            logger.error(f"Failed to download: {key}")

if __name__ == "__main__":
    fetch_logs_to_file()
