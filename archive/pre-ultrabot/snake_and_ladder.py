from google.cloud import storage, bigquery
from io import StringIO
import pandas as pd
import sys
import logging
from datetime import datetime
import apache_beam as beam
from apache_beam.io.gcp.gcsio import GcsIO
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.io.gcp.bigquery import WriteToBigQuery, BigQueryDisposition

# Mapping dataset names, filename prefixes, and BigQuery table names
dataset_mapping = {
    "PRE_EMBARGO": {
        "prefix": "Pre_emabrgo_land_rover_",
        "tables": {
            "Capder": "PRE_EMBARGO_LR_CAPDER",
            "Price": "PRE_EMBARGO_LR_PRICE",
            "OPTION": "PRE_EMBARGO_LR_OPTION"
        }
    },
    "BLACKBOOK": {
        "prefix": "CPRVAL",
        "tables": {"": "ALL_CAP_CPRVAL"}
    },
    "REDBOOK": {
        "prefix": "LPRVAL",
        "tables": {"": "ALL_CAP_LPRVAL"}
    }
}

# Function to get schema from BigQuery
def get_bq_schema(project_id, dataset_id, table_id):
    bq_client = bigquery.Client(project=project_id)
    table_ref = bq_client.dataset(dataset_id).table(table_id)
    table = bq_client.get_table(table_ref)
    schema = table.schema
    return {field.name: field.field_type for field in schema}

# Function to get column names from BigQuery schema
def get_bq_column_names(bq_schema, exclude_columns=[]):
    return [col for col in bq_schema if col not in exclude_columns]

# Function to get metadata record count
def metadata_count(bucket_name, metadata_file):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    metadatafile = metadata_file[:-4] + ".metadata.csv"
    blob = bucket.blob(metadatafile)
    if not blob.exists():
        return 0

    content = blob.download_as_text()
    df = pd.read_csv(StringIO(content))
    return int(df[df['Key'] == 'Total Records']['Value'].values[0])

# Function to list CSV files in the GCS folder
def list_files_in_folder(bucket_name, folder_path):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=folder_path)
    return [blob.name for blob in blobs if blob.name.endswith(".csv")]

# Function to read CSV file and get record count & column sums
def get_record_count_and_sums(bucket_name, file_path, zone, skip_header, bq_schema):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_path)
    content = blob.download_as_text()

    column_names = get_bq_column_names(bq_schema)
    df = pd.read_csv(StringIO(content), header=None, names=column_names, low_memory=False)

    column_count = len(df.columns)
    record_count = len(df) - (1 if skip_header else 0)

    # Compute column sums if it's ANAL zone
    column_sums = []
    if zone == "ANAL":
        numeric_columns = df.select_dtypes(include=["int64", "float64"]).columns
        column_sums = [{"column_name": col, "sum_value": str(df[col].sum())} for col in numeric_columns if col in bq_schema]

    return record_count, column_count, column_sums

# Function to check data consistency with BigQuery
def analytic_to_bq_checking(ana_bucket_name, dataset, project_id, records, bq_table_name):
    client = storage.Client(project_id)
    fs = GcsIO(client)
    ana_bucket = client.bucket(ana_bucket_name)
    blob_list_ana = client.list_blobs(ana_bucket)

    for blob_ana in blob_list_ana:
        filename = blob_ana.name.split('/')[-1]
        if dataset not in filename:
            continue

        with fs.open(f"gs://{ana_bucket_name}/{blob_ana.name}", mode='r') as pf:
            ana_lines = pf.readlines()
        ana_count = len(ana_lines) - 1  # Excluding header

        QUERY = f"SELECT count(*) as count FROM `{project_id}.{bq_table_name}`"
        bq_client = bigquery.Client(project_id)
        query_job = bq_client.query(QUERY)
        bq_count = next(query_job.result()).count

        bq_status = "Match" if bq_count == ana_count else "Not Match"
        bq_failed_count = abs(ana_count - bq_count)
        reason = f"{bq_status}: ana_records({ana_count}) vs. BQ_count({bq_count})"

        if filename in records:
            records[filename].update({"BQ_STATUS": bq_status, "BQ_FAILED": bq_failed_count, "REASON": reason})

    return records

# Main function to process files
def process_files(_, buckets_info, folder_path, dataset, project_id, bq_dataset_id):
    records = {}
    dataset_info = dataset_mapping.get(dataset, {})
    prefix = dataset_info.get("prefix", "")
    bq_table_map = dataset_info.get("tables", {})
    
    bq_table_name = None  # Ensure the variable is initialized

    for zone, bucket_name in buckets_info.items():
        files = list_files_in_folder(bucket_name, folder_path)

        for file in files:
            filename = file.split("/")[-1]
            if not filename.startswith(prefix) or filename.endswith(("schema.csv", "metadata.csv")):
                continue

            table_name_key = filename.replace(prefix, "").replace(".csv", "")
            bq_table_name = bq_table_map.get(table_name_key, "")

            if not bq_table_name:
                continue  # Skip this file if table name is not found

            bq_schema = get_bq_schema(project_id, bq_dataset_id, bq_table_name)

            skip_header = (zone == "ANAL")
            record_count, column_count, column_sums = get_record_count_and_sums(bucket_name, file, zone, skip_header, bq_schema)
            source_count = metadata_count(bucket_name, file) if zone == "RAW" else 0

            pick_date = file.split("/")[-2]
            folder_date = f"{pick_date[:4]}-{pick_date[4:6]}-{pick_date[6:]}"
            processed_time = datetime.now().strftime("%d/%m/%Y T %H:%M:%S")

            if filename not in records:
                records[filename] = {
                    "DATASET": dataset,
                    "FILE_DATE": folder_date,
                    "PROCESSED_DATE_TIME": processed_time,
                    "FILENAME": filename,
                    "SOURCE_COUNT": source_count,
                    "RAW_RECORDS": 0, "CERT_RECORDS": 0, "ANAL_RECORDS": 0,
                    "RAW_FAILED_RECORDS": 0, "CERT_FAILED_RECORDS": 0, "ANAL_FAILED_RECORDS": 0,
                    "RAW_COLUMN": 0, "CERT_COLUMN": 0, "ANAL_COLUMN": 0,
                    "ANAL_col_sums": [],
                    "BQ_STATUS": "", "BQ_FAILED": 0, "REASON": ""
                }

            if zone == "RAW":
                records[filename].update({"RAW_RECORDS": record_count, "RAW_COLUMN": column_count, "RAW_FAILED_RECORDS": source_count - record_count})
            elif zone == "CERT":
                records[filename].update({"CERT_RECORDS": record_count, "CERT_COLUMN": column_count, "CERT_FAILED_RECORDS": records[filename]["RAW_RECORDS"] - record_count})
            elif zone == "ANAL":
                records[filename].update({"ANAL_RECORDS": record_count, "ANAL_COLUMN": column_count, "ANAL_FAILED_RECORDS": records[filename]["CERT_RECORDS"] - record_count, "ANAL_col_sums": column_sums})

    # Only call the function if bq_table_name is not None or empty
    if bq_table_name:
        records = analytic_to_bq_checking(buckets_info["ANAL"], dataset, project_id, records, bq_table_name)

    return list(records.values())
# Run Apache Beam pipeline
def run_pipeline():
    options = PipelineOptions()
    buckets_info = {"RAW": "raw_bucket", "CERT": "cert_bucket", "ANAL": "anal_bucket"}

    with beam.Pipeline(options=options) as p:
        (p | beam.Impulse()
           | beam.Map(process_files, buckets_info, "path/to/data", "PRE_EMBARGO", "your_project_id", "your_dataset_id")
           | beam.io.WriteToBigQuery(table="your_project_id:your_dataset_id.recon_table", schema="SCHEMA_AUTODETECT", write_disposition=BigQueryDisposition.WRITE_APPEND))

if __name__ == "__main__":
    run_pipeline()
