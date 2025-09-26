import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from typing import Dict, Optional, Callable, Tuple, List, Any
import os
import datetime

def get_s3_client(config: Dict[str, str], mfa_token: Optional[str] = None):
    """
    Establishes a session with Wasabi and returns an S3 client.
    Handles MFA authentication if mfa_serial_number and mfa_token are provided.
    """
    try:
        session_params = {
            "aws_access_key_id": config['aws_access_key_id'],
            "aws_secret_access_key": config['aws_secret_access_key'],
        }

        if config.get('mfa_serial_number') and config['mfa_serial_number'] != 'YOUR_MFA_SERIAL_NUMBER_ARN (optional)':
            if not mfa_token:
                raise ValueError("MFA token is required for authentication.")

            sts_client = boto3.client('sts',
                aws_access_key_id=config['aws_access_key_id'],
                aws_secret_access_key=config['aws_secret_access_key']
            )

            token = sts_client.get_session_token(
                SerialNumber=config['mfa_serial_number'],
                TokenCode=mfa_token
            )

            credentials = token['Credentials']
            session_params = {
                "aws_access_key_id": credentials['AccessKeyId'],
                "aws_secret_access_key": credentials['SecretAccessKey'],
                "aws_session_token": credentials['SessionToken'],
            }

        client_params = {
            'endpoint_url': config['endpoint_url'],
            **session_params
        }

        # Add custom SSL certificate if provided
        if config.get('ssl_verify_path'):
            client_params['verify'] = config['ssl_verify_path']

        s3_client = boto3.client(
            's3',
            **client_params
        )
        return s3_client
    except (NoCredentialsError, ClientError) as e:
        raise e

def get_object_info(s3_client, bucket_name: str, source_key: str) -> Dict[str, Any]:
    """Gets metadata (like size) for a single object."""
    try:
        head = s3_client.head_object(Bucket=bucket_name, Key=source_key)
        return {
            'Key': source_key,
            'Size': head['ContentLength'],
            'LastModified': head['LastModified']
        }
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            raise FileNotFoundError(f"Error: Source file '{source_key}' not found in bucket '{bucket_name}'.")
        else:
            raise e

def list_objects_in_prefix(s3_client, bucket_name: str, source_prefix: str = '') -> Tuple[List[Dict[str, Any]], int]:
    """Lists all objects under a prefix, returning the list and their total size."""
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket_name, Prefix=source_prefix)

    objects_to_download = []
    total_size = 0
    for page in pages:
        if 'Contents' in page:
            for obj in page['Contents']:
                if obj['Size'] > 0: # Skip directories
                    objects_to_download.append(obj)
                    total_size += obj['Size']
    return objects_to_download, total_size

def list_object_versions_at_timestamp(s3_client, bucket_name: str, timestamp: datetime.datetime, source_prefix: str = '') -> Tuple[List[Dict[str, Any]], int]:
    """Finds the definitive list of object versions that existed at a given timestamp."""
    paginator = s3_client.get_paginator('list_object_versions')
    pages = paginator.paginate(Bucket=bucket_name, Prefix=source_prefix)

    latest_valid_versions = {}
    for page in pages:
        all_entries = page.get('Versions', []) + page.get('DeleteMarkers', [])
        for entry in all_entries:
            if entry['LastModified'] <= timestamp:
                key = entry['Key']
                if key not in latest_valid_versions or entry['LastModified'] > latest_valid_versions[key]['LastModified']:
                    latest_valid_versions[key] = entry

    objects_to_download = []
    total_size = 0
    for key, entry in latest_valid_versions.items():
        if 'VersionId' in entry and entry.get('Size', 0) > 0:
            objects_to_download.append(entry)
            total_size += entry['Size']

    return objects_to_download, total_size

def download_file(s3_client, bucket_name: str, source_key: str, destination_path: str, callback: Optional[Callable[[int], None]] = None):
    """Downloads a single object to a specific file path."""
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    try:
        s3_client.download_file(
            Bucket=bucket_name,
            Key=source_key,
            Filename=destination_path,
            Callback=callback
        )
    except ClientError as e:
        print(f"\nWarning: Could not download {source_key}. Error: {e}")

def download_objects(
    s3_client,
    bucket_name: str,
    destination_dir: str,
    source_prefix: str,
    object_list: List[Dict[str, Any]],
    callback: Optional[Callable[[int], None]] = None
):
    """Downloads a list of objects into a destination directory."""
    for obj in object_list:
        source_key = obj['Key']

        prefix_dir = source_prefix
        if source_prefix and not source_prefix.endswith('/'):
            prefix_dir = os.path.dirname(source_prefix.rstrip('/'))

        relative_path = os.path.relpath(source_key, start=prefix_dir if prefix_dir else '')
        destination_path = os.path.join(destination_dir, relative_path)

        os.makedirs(os.path.dirname(destination_path), exist_ok=True)

        extra_args = {}
        if 'VersionId' in obj:
            extra_args['VersionId'] = obj['VersionId']

        try:
            s3_client.download_file(
                Bucket=bucket_name,
                Key=source_key,
                Filename=destination_path,
                ExtraArgs=extra_args if extra_args else None,
                Callback=callback
            )
        except ClientError as e:
            print(f"\nWarning: Could not download {source_key} (Version: {obj.get('VersionId', 'N/A')}). Error: {e}")
