import base64
import asyncio
import httpx
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class CaptchaSolver:
    def __init__(self, capsolver_api_key: str):
        self.capsolver_api_key = capsolver_api_key
        self.ocr = None
        self._client = None  
        self._init_local_ocr()
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create reusable HTTP client"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client
    
    async def close(self):
        """Close HTTP client - call on shutdown"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    def _init_local_ocr(self):
        """Initialize ddddocr for local solving"""
        try:
            import ddddocr
            self.ocr = ddddocr.DdddOcr(show_ad=False)
            logger.info("Local OCR (ddddocr) initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize ddddocr: {e}")
            self.ocr = None
    
    def solve_local(self, image_bytes: bytes) -> Optional[str]:
        """
        Solve CAPTCHA using local OCR
        Returns: Captcha text or None if failed
        """
        if not self.ocr:
            logger.warning("Local OCR skipped: ddddocr not initialized")
            return None
        
        try:
            result = self.ocr.classification(image_bytes)
            # Clean result - remove spaces, ensure alphanumeric
            result = ''.join(c for c in result if c.isalnum())
            logger.info(f"Local OCR result: {result}")
            return result if len(result) >= 4 else None
        except Exception as e:
            logger.error(f"Local OCR failed: {e}")
            return None
    
    async def solve_capsolver(self, image_base64: str) -> Optional[str]:
        """
        Solve CAPTCHA using CapSolver API
        Task type: ImageToTextTask
        """
        payload = {
            "clientKey": self.capsolver_api_key,
            "task": {
                "type": "ImageToTextTask",
                "body": image_base64,
                "module": "common",  # General image captcha
                "score": 0.9
            }
        }
        
        try:
            # Use pooled HTTP client
            client = await self._get_client()
            
            # Create task
            response = await client.post(
                "https://api.capsolver.com/createTask",
                json=payload
            )
            data = response.json()
            
            if data.get("errorId", 0) != 0:
                logger.error(f"CapSolver error: {data.get('errorDescription')}")
                return None
            
            task_id = data.get("taskId")
            if not task_id:
                # Some tasks return result immediately
                solution = data.get("solution", {})
                return solution.get("text")
            
            # Poll for result
            for _ in range(30):  
                await asyncio.sleep(1)
                result_response = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={
                        "clientKey": self.capsolver_api_key,
                        "taskId": task_id
                    }
                )
                result_data = result_response.json()
                
                if result_data.get("status") == "ready":
                    text = result_data.get("solution", {}).get("text")
                    logger.info(f"CapSolver result: {text}")
                    return text
                
                if result_data.get("errorId", 0) != 0:
                    logger.error(f"CapSolver task error: {result_data}")
                    return None
            
            logger.error("CapSolver timeout")
            return None
                
        except Exception as e:
            logger.error(f"CapSolver API failed: {e}")
            return None
    
    async def solve(self, image_data: str | bytes, max_retries: int = 3) -> Optional[str]:

        # Normalize input
        if isinstance(image_data, str):
            # Remove data URL prefix if present
            if "base64," in image_data:
                image_data = image_data.split("base64,")[1]
            image_bytes = base64.b64decode(image_data)
            image_base64 = image_data
        else:
            image_bytes = image_data
            image_base64 = base64.b64encode(image_data).decode()
        
        for attempt in range(max_retries):
            logger.info(f"CAPTCHA solve attempt {attempt + 1}/{max_retries}")
            
            # Try local OCR first (fast, free)
            result = self.solve_local(image_bytes)
            if result:
                logger.info(f"Local OCR succeeded: {result}")
                return result
            
            # Fallback to CapSolver (slower, paid, more accurate)
            logger.info("Local OCR failed, trying CapSolver...")
            result = await self.solve_capsolver(image_base64)
            if result:
                logger.info(f"CapSolver succeeded: {result}")
                return result
            
            logger.warning(f"Attempt {attempt + 1} failed")
        
        logger.error("All CAPTCHA solve attempts exhausted")
        return None


# Quick test
if __name__ == "__main__":
    import sys
    
    async def test():
        solver = CaptchaSolver("CAP-TEST-KEY")
        # Test with sample base64 image
        if len(sys.argv) > 1:
            with open(sys.argv[1], "rb") as f:
                result = await solver.solve(f.read())
                print(f"Result: {result}")
    
    asyncio.run(test())
