import os
from google.cloud import storage

def upload_folder_to_gcs(bucket_name, source_folder, destination_folder=""):
    # Initialize client
    client = storage.Client()

    # Get bucket
    bucket = client.get_bucket(bucket_name)

    # Walk through local folder
    for root, _, files in os.walk(source_folder):
        for file_name in files:
            local_path = os.path.join(root, file_name)

            # Define GCS path relative to the source folder
            relative_path = os.path.relpath(local_path, source_folder)
            gcs_path = os.path.join(destination_folder, relative_path)

            # Upload
            blob = bucket.blob(gcs_path)
            blob.upload_from_filename(local_path)

            print(f"Uploaded {local_path} to gs://{bucket_name}/{gcs_path}")

# Example usage
if __name__ == "__main__":
    bucket_name = "your-gcs-bucket-name"
    local_upload_folder = "upload"  # Make sure this folder exists and has files
    gcs_folder = "optional-folder-in-gcs"  # Leave empty if not needed

    upload_folder_to_gcs(bucket_name, local_upload_folder, gcs_folder)