import argparse
import os
import sys
from datetime import datetime, timezone

import boto3
import pandas as pd
from botocore.exceptions import ClientError
from tqdm import tqdm

# Constants
CONFIG_FILE = "config.csv"
DEFAULT_DOWNLOAD_DIR = "Download"


def load_config():
    """
    Loads configuration from config.csv.

    Returns:
        dict: A dictionary containing the configuration values.

    Raises:
        FileNotFoundError: If config.csv is not found.
        ValueError: If essential keys are missing in the config.
    """
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"Configuration file '{CONFIG_FILE}' not found. "
            f"Please create it from 'config.csv.example'."
        )

    try:
        df = pd.read_csv(CONFIG_FILE, header=None, index_col=0, names=['value'])
        config = df.squeeze('columns').to_dict()
    except Exception as e:
        raise ValueError(f"Error reading or parsing '{CONFIG_FILE}': {e}")

    essential_keys = [
        'aws_access_key_id',
        'aws_secret_access_key',
        'endpoint_url',
        'bucket_name'
    ]

    missing_keys = [key for key in essential_keys if key not in config or pd.isna(config.get(key))]
    if missing_keys:
        raise ValueError(f"Missing or empty essential keys in '{CONFIG_FILE}': {', '.join(missing_keys)}")

    config['mfa_serial_number'] = config.get('mfa_serial_number') if pd.notna(config.get('mfa_serial_number')) else None
    return config


def create_s3_client(config):
    """
    Creates and returns a Boto3 S3 client using the provided configuration.
    Handles MFA authentication if mfa_serial_number is provided.
    """
    session_params = {
        'aws_access_key_id': config['aws_access_key_id'],
        'aws_secret_access_key': config['aws_secret_access_key'],
    }

    if config.get('mfa_serial_number'):
        mfa_otp = input("Please enter your MFA OTP: ")
        try:
            sts_client = boto3.client('sts',
                aws_access_key_id=config['aws_access_key_id'],
                aws_secret_access_key=config['aws_secret_access_key'],
            )
            session_token = sts_client.get_session_token(
                SerialNumber=config['mfa_serial_number'],
                TokenCode=mfa_otp
            )
            credentials = session_token['Credentials']
            session_params = {
                'aws_access_key_id': credentials['AccessKeyId'],
                'aws_secret_access_key': credentials['SecretAccessKey'],
                'aws_session_token': credentials['SessionToken'],
            }
        except ClientError as e:
            if e.response['Error']['Code'] == 'MultiFactorAuthChallengeFailed':
                raise ClientError("MFA authentication failed. The OTP may be incorrect or expired.", "GetSessionToken")
            else:
                raise ClientError(f"An AWS client error occurred: {e}", "GetSessionToken")

    s3_client = boto3.client(
        's3',
        endpoint_url=config['endpoint_url'],
        **session_params
    )
    return s3_client


class TqdmProgressCallback:
    """A tqdm callback to show download progress."""
    def __init__(self, t):
        self.t = t
        self.last_block = 0

    def __call__(self, bytes_amount):
        block = bytes_amount - self.last_block
        self.last_block = bytes_amount
        self.t.update(block)


def download_single_file(s3_client, bucket, source, destination, version_id=None):
    """
    Downloads a single file from S3, with a progress bar and optional versioning.
    """
    if destination is None:
        os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)
        filename = os.path.basename(source)
        destination = os.path.join(DEFAULT_DOWNLOAD_DIR, filename)
    else:
        local_dir = os.path.dirname(destination)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)

    try:
        extra_args = {}
        if version_id:
            extra_args['VersionId'] = version_id

        meta = s3_client.head_object(Bucket=bucket, Key=source, **extra_args)
        total_size = int(meta.get('ContentLength', 0))

        download_desc = f"{os.path.basename(source)}"
        if version_id:
            download_desc += f" (ver: {version_id[:7]})"

        with tqdm(total=total_size, unit='B', unit_scale=True, desc=download_desc, leave=False) as t:
            s3_client.download_file(
                Bucket=bucket,
                Key=source,
                Filename=destination,
                ExtraArgs=extra_args,
                Callback=TqdmProgressCallback(t)
            )
        return True, destination
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            print(f"Error: Source file '{source}' not found in bucket '{bucket}'.", file=sys.stderr)
        else:
            print(f"An unexpected error occurred for '{source}': {e}", file=sys.stderr)
        return False, None
    except PermissionError:
        print(f"Error: Permission denied. Could not write to '{destination}'.", file=sys.stderr)
        return False, None
    except Exception as e:
        print(f"An error occurred during download of '{source}': {e}", file=sys.stderr)
        return False, None


def download_dir(s3_client, bucket, source_prefix, dest_dir):
    """
    Downloads all files from a specific S3 prefix (directory).
    """
    if dest_dir is None:
        dest_dir = DEFAULT_DOWNLOAD_DIR

    paginator = s3_client.get_paginator('list_objects_v2')
    try:
        pages = paginator.paginate(Bucket=bucket, Prefix=source_prefix)
        all_keys = [obj['Key'] for page in pages if "Contents" in page for obj in page["Contents"] if not obj['Key'].endswith('/')]
    except ClientError as e:
        print(f"Error listing files in '{source_prefix}': {e}", file=sys.stderr)
        return

    if not all_keys:
        print(f"No files found in '{source_prefix if source_prefix else 'bucket root'}'.")
        return

    print(f"Found {len(all_keys)} files to download from '{source_prefix if source_prefix else 'bucket root'}'.")

    success_count = 0
    fail_count = 0

    with tqdm(total=len(all_keys), desc="Downloading directory", unit="file") as pbar:
        for key in all_keys:
            relative_path = os.path.relpath(key, start=source_prefix)
            local_path = os.path.join(dest_dir, relative_path)

            success, _ = download_single_file(s3_client, bucket, key, local_path)
            if success:
                success_count += 1
            else:
                fail_count += 1
            pbar.update(1)

    print(f"\nDirectory download complete. {success_count} files succeeded, {fail_count} files failed.")


def download_versioned(s3_client, bucket, timestamp_str, source_prefix, dest_dir):
    """
    Downloads the latest version of each file as it existed at a specific
    point in time (end of the given day).
    """
    if dest_dir is None:
        dest_dir = DEFAULT_DOWNLOAD_DIR

    try:
        target_date = datetime.strptime(timestamp_str, '%Y%m%d')
        target_timestamp = target_date.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
        print(f"Fetching versions as of end-of-day {target_date.date()} UTC...")
    except ValueError:
        print(f"Error: Invalid timestamp format '{timestamp_str}'. Please use YYYYMMDD.", file=sys.stderr)
        sys.exit(1)

    paginator = s3_client.get_paginator('list_object_versions')
    try:
        pages = paginator.paginate(Bucket=bucket, Prefix=source_prefix)

        latest_versions_in_time = {}
        all_entries = []
        for page in pages:
            all_entries.extend(page.get('Versions', []))
            all_entries.extend(page.get('DeleteMarkers', []))

        for entry in all_entries:
            if entry['Key'].endswith('/'):
                continue
            if entry['LastModified'] <= target_timestamp:
                key = entry['Key']
                if key not in latest_versions_in_time or entry['LastModified'] > latest_versions_in_time[key]['LastModified']:
                    latest_versions_in_time[key] = entry

        to_download = {}
        for key, entry in latest_versions_in_time.items():
            if 'Size' in entry: # A DeleteMarker does not have a 'Size' key.
                to_download[key] = entry['VersionId']

    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidRequest':
             print(f"Error: Bucket '{bucket}' may not have versioning enabled, which is required for this command.", file=sys.stderr)
        else:
             print(f"Error listing object versions: {e}", file=sys.stderr)
        sys.exit(1)

    if not to_download:
        print(f"No file versions found for the specified timestamp '{timestamp_str}'.")
        return

    print(f"Found {len(to_download)} file(s) to download for the state at {timestamp_str}.")

    success_count = 0
    fail_count = 0

    with tqdm(total=len(to_download), desc="Downloading versions", unit="file") as pbar:
        for key, version_id in to_download.items():
            relative_path = os.path.relpath(key, start=source_prefix)
            local_path = os.path.join(dest_dir, relative_path)

            success, _ = download_single_file(s3_client, bucket, key, local_path, version_id=version_id)
            if success:
                success_count += 1
            else:
                fail_count += 1
            pbar.update(1)

    print(f"\nVersioned download complete. {success_count} file(s) succeeded, {fail_count} file(s) failed.")


def main():
    """Main function to handle argument parsing and command execution."""
    parser = argparse.ArgumentParser(
        description="A command-line tool to download files from Wasabi Hot Cloud Storage."
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # --- Sub-command: download_file ---
    parser_file = subparsers.add_parser("download_file", help="Download a single file from Wasabi.")
    parser_file.add_argument("--source", type=str, required=True, help="The object key (file path) on Wasabi.")
    parser_file.add_argument("--destination", type=str, help="Local path to save the file. Defaults to './Download/<filename>'.")

    # --- Sub-command: download_dir ---
    parser_dir = subparsers.add_parser("download_dir", help="Download an entire directory (or bucket) from Wasabi.")
    parser_dir.add_argument("--source", type=str, default="", help="The directory (prefix) on Wasabi to download. Defaults to the entire bucket.")
    parser_dir.add_argument("--destination", type=str, help=f"Local directory to save files. Defaults to './{DEFAULT_DOWNLOAD_DIR}/'.")

    # --- Sub-command: download_versioned ---
    parser_versioned = subparsers.add_parser("download_versioned", help="Download the latest versions of files from a specific point in time.")
    parser_versioned.add_argument("--timestamp", type=str, required=True, help="The date for versioning, in YYYYMMDD format.")
    parser_versioned.add_argument("--source", type=str, default="", help="The directory (prefix) on Wasabi. Defaults to the entire bucket.")
    parser_versioned.add_argument("--destination", type=str, help=f"Local directory to save files. Defaults to './{DEFAULT_DOWNLOAD_DIR}/'.")

    args = parser.parse_args()

    try:
        config = load_config()
        s3_client = create_s3_client(config)
        bucket_name = config['bucket_name']

        if args.command == "download_file":
            print(f"Starting download of '{args.source}'...")
            success, local_path = download_single_file(s3_client, bucket_name, args.source, args.destination)
            if success:
                print(f"\nSuccessfully downloaded to '{local_path}'")
            else:
                print(f"\nDownload failed for '{args.source}'", file=sys.stderr)
                sys.exit(1)

        elif args.command == "download_dir":
            download_dir(s3_client, bucket_name, args.source, args.destination)

        elif args.command == "download_versioned":
            download_versioned(s3_client, bucket_name, args.timestamp, args.source, args.destination)

    except (FileNotFoundError, ValueError, ClientError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
