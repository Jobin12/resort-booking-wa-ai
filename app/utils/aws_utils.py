import logging
import boto3
from flask import current_app
from botocore.exceptions import ClientError
from urllib.parse import urlparse

def generate_presigned_url(s3_url: str, expiration=3600):
    """
    Generate a presigned URL for an S3 object.
    Expects formats like s3://bucket-name/key or https://bucket-name.s3.amazonaws.com/key
    """
    try:
        bucket_name = current_app.config.get("AWS_S3_BUCKET_NAME")
        
        # Parse the object key from the URL
        parsed_url = urlparse(s3_url)
        object_key = None
        
        if parsed_url.scheme == 's3':
            # s3://bucket/key/path
            object_key = parsed_url.path.lstrip('/')
            # Use bucket name from url if exists, otherwise fallback to config
            bucket_name = parsed_url.netloc or bucket_name
        elif parsed_url.scheme in ['http', 'https']:
            # Either https://bucket.s3.region.amazonaws.com/key or https://s3.region.amazonaws.com/bucket/key
            path_parts = parsed_url.path.lstrip('/').split('/')
            if 's3' in parsed_url.netloc and parsed_url.netloc.startswith('s3'):
                # https://s3.amazonaws.com/bucket/key
                bucket_name = path_parts[0]
                object_key = '/'.join(path_parts[1:])
            else:
                # https://bucket.s3.amazonaws.com/key
                object_key = parsed_url.path.lstrip('/')
        else:
            # Fallback assuming it's just the key
            object_key = s3_url

        if not object_key or not bucket_name:
            logging.warning(f"Could not determine bucket or object key from url: {s3_url}")
            return s3_url

        s3_client = boto3.client(
            's3',
            aws_access_key_id=current_app.config.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=current_app.config.get("AWS_SECRET_ACCESS_KEY"),
            region_name=current_app.config.get("AWS_REGION", "us-east-1")
        )

        response = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': object_key},
            ExpiresIn=expiration
        )
        return response
    except ClientError as e:
        logging.error(f"AWS ClientError generating presigned URL: {e}")
        return s3_url
    except Exception as e:
        logging.error(f"Unexpected error generating presigned URL: {e}")
        return s3_url
