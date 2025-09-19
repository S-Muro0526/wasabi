import pandas as pd
from typing import Dict, Optional

def load_config(config_path: str = 'config.csv') -> Dict[str, str]:
    """
    Reads the configuration from a CSV file and returns it as a dictionary.

    The CSV file must have two columns: 'key' and 'value'.

    Args:
        config_path: The path to the configuration CSV file.

    Returns:
        A dictionary containing the configuration settings.

    Raises:
        FileNotFoundError: If the config file is not found.
        ValueError: If the config file is invalid (e.g., missing required keys).
    """
    try:
        df = pd.read_csv(config_path)
    except FileNotFoundError:
        raise FileNotFoundError(f"Error: Configuration file not found at '{config_path}'")

    if 'key' not in df.columns or 'value' not in df.columns:
        raise ValueError("Invalid config file: Must contain 'key' and 'value' columns.")

    # Convert the two-column DataFrame to a dictionary
    config = pd.Series(df.value.values, index=df.key).to_dict()

    # Validate required keys
    required_keys = [
        'aws_access_key_id',
        'aws_secret_access_key',
        'endpoint_url',
        'bucket_name'
    ]

    missing_keys = [key for key in required_keys if key not in config or pd.isna(config[key])]
    if missing_keys:
        raise ValueError(f"Missing required configuration keys: {', '.join(missing_keys)}")

    # Handle optional mfa_serial_number
    if 'mfa_serial_number' in config and pd.isna(config['mfa_serial_number']):
        config['mfa_serial_number'] = None

    return config
