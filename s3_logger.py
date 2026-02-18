import asyncio
import logging
from datetime import datetime
from typing import List
from functools import partial

import boto3
from botocore.exceptions import ClientError

from config import config

logger = logging.getLogger(__name__)


class S3Logger:
    def __init__(self):
        self._client = boto3.client(
            "s3",
            region_name=config.S3_REGION,
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
        )
        self._bucket = config.S3_BUCKET_NAME

    def _format_logs(self, logs: List[dict]) -> str:
        lines = []
        for entry in logs:
            time_str = entry.get("time", "")
            message = entry.get("message", "")
            lines.append(f"[{time_str}] {message}")
        return "\n".join(lines)

    def _upload(self, key: str, body: str):
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )

    async def upload_session_logs(
        self, passport: str, session_id: str, logs: List[dict]
    ):
        if not logs:
            return

        date_str = datetime.now().strftime("%Y-%m-%d")
        key = f"logs/{passport}/{date_str}/{session_id}.log"
        body = self._format_logs(logs)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, partial(self._upload, key, body))
            logger.info(f"Uploaded session logs to s3://{self._bucket}/{key}")
        except ClientError as e:
            logger.warning(f"Failed to upload session logs to S3: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error uploading session logs to S3: {e}")
