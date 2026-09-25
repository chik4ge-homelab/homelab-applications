import os
import sys

import boto3
from botocore.config import Config


def main() -> None:
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        verify=False,
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )
    client.put_bucket_lifecycle_configuration(
        Bucket=os.environ["BUCKET_NAME"],
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "expire-litellm-audit-objects-after-30-days",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "Expiration": {"Days": 30},
                }
            ]
        },
    )
    print("Ceph RGW lifecycle configuration applied")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"failed to apply S3 lifecycle configuration ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1)
