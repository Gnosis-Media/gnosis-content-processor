import boto3
import json
import os
from botocore.exceptions import ClientError

def get_secrets(secret_name="gnosis-secrets", region_name="us-east-1"):        
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
        return json.loads(get_secret_value_response['SecretString'])
    except ClientError as e:
        raise e

def get_service_secrets(service_name):
    secrets = get_secrets()
    return secrets.get(service_name, {})