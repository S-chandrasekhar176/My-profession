from google.cloud import storage
from google.cloud import bigquery
import pandas as pd
import sys
from io import StringIO
from datetime import datetime
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

# Define the function to list CSV files in the GCS folder
def list_files_in_folder(bucket_name, folder_path):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=folder_path)
    files = [blob.name for blob in blobs if blob.name.endswith('.csv')]
    return files

# ✅ Updated BigQuery schema
consolidated_schema = {
    'fields': [
        {'name': 'ZONE', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'DATASET', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'FILE_DATE', 'type': 'DATE', 'mode': 'NULLABLE'},
        {'name': 'PROCESSED_DATE', 'type': 'DATE', 'mode': 'NULLABLE'},
        {'name': 'PROCESSED_TIME', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'FILENAME', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'RECORDS', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'Failed_null_check', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'Failed_CapCode_value', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'Failed_CapId_value', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'Failed_year_column_value', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'Failed_plate_column_value', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'Failed_Volume_check', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'Failed_unique_check', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'Error_record', 'type': 'STRING', 'mode': 'NULLABLE'},  # ✅ New column
    ]
}

# Read file from GCS
def read_csv_file_from_gcs(bucket_name, file_path):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_path)
    content = blob.download_as_text()
    return pd.read_csv(StringIO(content), header=None)

# ✅ Enhanced function to process and explode errors per record
def process_file(bucket_name, file, dataset, zone):
    try:
        df = read_csv_file_from_gcs(bucket_name, file)
        if df.empty:
            return []

        records = []
        pick_date = file.split('/')[-1]
        folder_date = f"{pick_date[:4]}-{pick_date[4:6]}-{pick_date[6:8]}"
        processed_date = datetime.now().strftime("%Y-%m-%d")
        processed_time = datetime.now().strftime("%H:%M-%S")
        filename = file.split('/')[-1]

        error_tags = {
            "Failed null check": "Failed_null_check",
            "Failed CapCode value": "Failed_CapCode_value",
            "Failed CapId value": "Failed_CapId_value",
            "Failed year column value": "Failed_year_column_value",
            "Failed plate column value": "Failed_plate_column_value",
            "Failed volume check": "Failed_Volume_check",
            "Failed unique check": "Failed_unique_check"
        }

        for _, row in df.iterrows():
            row_str = ",".join(str(x) for x in row)
            for err_text, err_field in error_tags.items():
                if any(err_text in str(cell) for cell in row):
                    result = {
                        'ZONE': zone,
                        'DATASET': dataset,
                        'FILE_DATE': folder_date,
                        'PROCESSED_DATE': processed_date,
                        'PROCESSED_TIME': processed_time,
                        'FILENAME': filename,
                        'RECORDS': 1,
                        'Failed_null_check': 1 if err_field == 'Failed_null_check' else 0,
                        'Failed_CapCode_value': 1 if err_field == 'Failed_CapCode_value' else 0,
                        'Failed_CapId_value': 1 if err_field == 'Failed_CapId_value' else 0,
                        'Failed_year_column_value': 1 if err_field == 'Failed_year_column_value' else 0,
                        'Failed_plate_column_value': 1 if err_field == 'Failed_plate_column_value' else 0,
                        'Failed_Volume_check': 1 if err_field == 'Failed_Volume_check' else 0,
                        'Failed_unique_check': 1 if err_field == 'Failed_unique_check' else 0,
                        'Error_record': f"{row_str},{err_text}"
                    }
                    records.append(result)

        return records
    except pd.errors.EmptyDataError:
        return []

# Beam pipeline
def run_pipeline(project_id, raw_zone_bucket, certify_zone_bucket, folder_path, bq_dataset_id, bq_table_name, dataset):
    options = PipelineOptions(
        project=project_id,
        job_name=f'reconciliation-{dataset}'.lower(),
        runner="DataflowRunner",
        region='your-region',
        staging_location=f'gs://{raw_zone_bucket}/staging',
        temp_location=f'gs://{raw_zone_bucket}/temp',
        num_workers=1,
        max_num_workers=4,
        use_public_ips=False,
        save_main_session=True
    )

    with beam.Pipeline(options=options) as p:
        raw_files = list_files_in_folder(raw_zone_bucket, folder_path)
        raw_data = (
            p
            | "Raw file list" >> beam.Create(raw_files)
            | "Process raw" >> beam.FlatMap(lambda file: process_file(raw_zone_bucket, file, dataset, 'RAW'))
        )

        certify_files = list_files_in_folder(certify_zone_bucket, folder_path)
        certify_data = (
            p
            | "Certify file list" >> beam.Create(certify_files)
            | "Process certify" >> beam.FlatMap(lambda file: process_file(certify_zone_bucket, file, dataset, 'CERTIFY'))
        )

        (raw_data, certify_data) | "Merge zones" >> beam.Flatten() \
            | "Write to BQ" >> beam.io.WriteToBigQuery(
                table=f'{project_id}:{bq_dataset_id}.{bq_table_name}',
                schema=consolidated_schema,
                write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND,
                create_disposition=beam.io.BigQueryDisposition.CREATE_IF_NEEDED
            )

# Entry point
if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else 'BLACKBOOK'
    project_id = 'your_project_id'
    raw_zone_bucket = 'your_raw_zone_bucket'
    certify_zone_bucket = 'your_certify_zone_bucket'
    folder_path = 'data_folder_path/'
    bq_dataset_id = 'your_dataset_id'
    bq_table_name = 'consolidated_record_count_report'

    print("***** Recon Started for Rawzone and Certify Zone *****")
    run_pipeline(project_id, raw_zone_bucket, certify_zone_bucket, folder_path, bq_dataset_id, bq_table_name, dataset)
    print("***** Recon Finished for Rawzone and Certify Zone *****")