from google.cloud import storage, bigquery
from io import StringIO
import os
import pandas as pd
import sys
import logging
from datetime import datetime
import apache_beam as beam
from apache_beam.io.gcp.gcsio import GcsIO
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.io.gcp.bigquery import WriteToBigQuery, BigQueryDisposition

recon_consilidated_schema = {
    "fields": [
        {"name": "DATASET", "type": "STRING", "mode": "NULLABLE"},
        {"name": "FILE_DATE", "type": "STRING", "mode": "NULLABLE"},
        {"name": "PROCESSED_DATE_TIME", "type": "STRING", "mode": "NULLABLE"},
        {"name": "FILENAME", "type": "STRING", "mode": "NULLABLE"},
        {"name": "SOURCE_COUNT", "type": "INTEGER", "mode": "NULLABLE"}, 
        {"name": "RAW_RECORDS", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "RAW_FAILED_RECORDS", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "CERT_RECORDS", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "CERT_FAILED_RECORDS", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "ANALYTIC_RECORDS", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "ANALYTIC_FAILED_RECORDS", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "RAW_COLUMN", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "CERT_COLUMN", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "ANALYTIC_COLUMN", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "BQ_STATUS", "type": "STRING", "mode": "NULLABLE"},
        {"name": "BQ_FAILED", "type": "INTEGER", "mode": "NULLABLE"},
        {"name": "ANALYTIC_col_sums", "type": "RECORD", "mode": "REPEATED", "fields": [
            {"name": "column_name", "type": "STRING", "mode": "NULLABLE"},
            {"name": "sum_value", "type": "STRING", "mode": "NULLABLE"}
        ]},
        {"name": "REASON", "type": "STRING", "mode": "NULLABLE"}

    ]
}
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
    "PRE_EMBARGO": {
        "prefix": "Pre_emabrgo_land_rover_",
        "tables": {
            "Capder": "PRE_EMBARGO_LR_CAPDER",
            "Price": "PRE_EMBARGO_LR_PRICE",
            "OPTION": "PRE_EMBARGO_LR_OPTION"
        }},
    "CAP_NVD_LCV": {
        "prefix": "LIGHTS_",
        "tables": {
            "Capder": "CAP_LCV_CAPDER",
            "Price": "CAP_LCV_PRICE",
            "OPTION": "CAP_LCV_OPTION"
        }},
    "REDBOOK": {
        "prefix": "LPRVAL",
        "tables": {"": "ALL_CAP_LPRVAL"}
    }
}

def load_env_config():
    import os
    from dotenv import load_dotenv, find_dotenv
    load_dotenv

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

def check_received_folder(project_id, raw_bucket,cap_hpi_path):
    client = storage.Client(project=project_id)
    received_dates = []
    received_path = f'{cap_hpi_path}/{dataset}/{folder_base}/Files/'
    received_list = client.list_blobs(raw_bucket, prefix=received_path)
    for blob in received_list:
        if blob.name.endswith('.csv') and 'metadata' not in blob.name and 'schema' not in blob.name:
            received_dates.append(blob.name.split('/')[-2])
            return max(set(received_dates))
        
# Function to list CSV files in the GCS folder
def list_files_in_folder(bucket_name, folder_path):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=folder_path)
    files = [blob.name for blob in blobs if blob.name.endswith(".csv")]
    return files

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

# Beam DoFn to list files from GCS
class ListFilesDoFn(beam.DoFn):
    def __init__(self, bucket_name, folder_path, prefix):
        self.bucket_name = bucket_name
        self.folder_path = folder_path
        self.prefix = prefix
        
    def setup(self):
        self.storage_client = storage.Client()
        
    def process(self, _):
        bucket = self.storage_client.bucket(self.bucket_name)
        blobs = bucket.list_blobs(prefix=self.folder_path)
        
        for blob in blobs:
            if blob.name.endswith(".csv") and blob.name.startswith(self.prefix) and not blob.name.endswith(("schema.csv", "metadata.csv")):
                yield blob.name

# Main function to process files
def process_files(file_path, buckets_info, folder_path, dataset, project_id, bq_dataset_id):
    records = {}
    dataset_info = dataset_mapping.get(dataset, {})
    prefix = dataset_info.get("prefix", "")
    bq_table_map = dataset_info.get("tables", {})
    
    filename = file_path.split("/")[-1]
    
    # Extract table name key based on dataset and filename pattern
    if dataset == "CAP_NVD_LCV" and filename.startswith("LIGHTS_"):
        # For CAP_NVD_LCV, extract the part after the date (e.g., "Capder" from "LIGHTS_2025-03-21_Capder.csv")
        parts = filename.replace(prefix, "").replace(".csv", "").split("_")
        if len(parts) >= 3:  # Ensure we have enough parts
            table_name_key = parts[-1]  # Get the last part (e.g., "Capder")
        else:
            table_name_key = filename.replace(prefix, "").replace(".csv", "")
    else:
        # For other datasets, use the original logic
        table_name_key = filename.replace(prefix, "").replace(".csv", "")
        
    bq_table_name = bq_table_map.get(table_name_key, "")

    if not bq_table_name:
        logging.warning(f"Could not find BigQuery table for {filename} with key {table_name_key}")
        return []  # Skip this file if table name is not found

    record = {}
    
    # Process each zone for this file
    for zone, bucket_name in buckets_info.items():
        # Check if file exists in this zone
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        zone_file_path = file_path.replace(buckets_info["RAW"], bucket_name)
        blob = bucket.blob(zone_file_path)
        
        if not blob.exists():
            logging.warning(f"File {zone_file_path} does not exist in {zone} zone")
            continue
            
        # Get schema and process file
        bq_schema = get_bq_schema(project_id, bq_dataset_id, bq_table_name)
        skip_header = (zone == "ANALYTIC")
        record_count, column_count, column_sums = get_record_count_and_sums(bucket_name, zone_file_path, zone, skip_header, bq_schema)
        source_count = metadata_count(bucket_name, zone_file_path) if zone == "RAW" else 0
        
        # Initialize record if not already done
        if not record:
            pick_date = file_path.split("/")[-2]
            folder_date = f"{pick_date[:4]}-{pick_date[4:6]}-{pick_date[6:]}"
            processed_time = datetime.now().strftime("%d/%m/%Y T %H:%M:%S")
            
            record = {
                "DATASET": dataset,
                "FILE_DATE": folder_date,
                "PROCESSED_DATE_TIME": processed_time,
                "FILENAME": filename,
                "SOURCE_COUNT": source_count,
                "RAW_RECORDS": 0, "CERT_RECORDS": 0, "ANALYTIC_RECORDS": 0,
                "RAW_FAILED_RECORDS": 0, "CERT_FAILED_RECORDS": 0, "ANALYTIC_FAILED_RECORDS": 0,
                "RAW_COLUMN": 0, "CERT_COLUMN": 0, "ANALYTIC_COLUMN": 0,
                "ANALYTIC_col_sums": [],
                "BQ_STATUS": "", "BQ_FAILED": 0, "REASON": ""
            }
        
        # Update record based on zone
        if zone == "RAW":
            record.update({"RAW_RECORDS": record_count, "RAW_COLUMN": column_count, "RAW_FAILED_RECORDS": source_count - record_count})
        elif zone == "CERT":
            record.update({"CERT_RECORDS": record_count, "CERT_COLUMN": column_count, "CERT_FAILED_RECORDS": record["RAW_RECORDS"] - record_count})
        elif zone == "ANALYTIC":
            record.update({
                "ANALYTIC_RECORDS": record_count, 
                "ANALYTIC_COLUMN": column_count, 
                "ANALYTIC_FAILED_RECORDS": record["CERT_RECORDS"] - record_count, 
                "ANALYTIC_col_sums": column_sums
            })
            
            # Check BigQuery status
            QUERY = f"SELECT count(*) as count FROM `{project_id}.{bq_table_name}`"
            bq_client = bigquery.Client(project_id)
            query_job = bq_client.query(QUERY)
            bq_count = next(query_job.result()).count
            ana_count = record_count
            bq_status = "Match" if bq_count == ana_count else "Not Match"
            bq_failed_count = abs(ana_count - bq_count)
            reason = f"{bq_status}: ana_records({ana_count}) vs. BQ_count({bq_count})"
            
            record.update({
                "BQ_STATUS": bq_status, 
                "BQ_FAILED": bq_failed_count, 
                "REASON": reason
            })
    
    # Return the record if it was created
    if record:
        return [record]
    return []

# Run Apache Beam pipeline
def run_pipeline(project_id, dataset, folder_path, raw_bucket, cert_bucket, analytic_bucket, bq_dataset_id, table_id):
    options = PipelineOptions()
    buckets_info = {"RAW": raw_bucket, "CERT": cert_bucket, "ANALYTIC": analytic_bucket}
    dataset_info = dataset_mapping.get(dataset, {})
    prefix = dataset_info.get("prefix", "")

    with beam.Pipeline(options=options) as p:
        # Create a PCollection with a single element to trigger the pipeline
        trigger = p | "Create trigger" >> beam.Create([None])
        
        # List files from RAW bucket
        files = (trigger 
                | "List files" >> beam.ParDo(ListFilesDoFn(raw_bucket, folder_path, prefix))
                | "Process files" >> beam.FlatMap(
                    process_files, 
                    buckets_info=buckets_info, 
                    folder_path=folder_path, 
                    dataset=dataset, 
                    project_id=project_id, 
                    bq_dataset_id=bq_dataset_id
                )
                | "Write to BigQuery" >> beam.io.WriteToBigQuery(
                    table=f"{project_id}:{bq_dataset_id}.{table_id}", 
                    schema=recon_consilidated_schema,
                    create_disposition=BigQueryDisposition.CREATE_NEVER,
                    write_disposition=BigQueryDisposition.WRITE_APPEND
                ))

if __name__ == "__main__":

    env_config = load_env_config()
    dataset = sys.argv[1] if len(sys.argv) > 1 else "PRE_EMBARGO_LR"
    folder_base = "DAILY" if dataset in ["PRE_EMBARGO_LR","PRE_EMBARGO_J","CAP_NVD_LCV"] else "MONTHLY"

    project_id = env_config.get('PROJECT')
    raw_bucket = env_config.get('RAW_ZONE')
    cert_bucket = env_config.get('CERT_ZONE')
    analytic_bucket = env_config.get('ANALYTIC_ZONE')
    bq_dataset_id = env_config.get('BQ_DATASET')
    cap_hpi_path = env_config.get("CAP_PATH")
    table_id = env_config.get("RECON_TABLE")
    recon_received_schema = recon_consilidated_schema
    month_folder = check_received_folder(project_id,raw_bucket,cap_hpi_path)
    folder_path = f"{cap_hpi_path}/{folder_base}/"

    run_pipeline(project_id, dataset, folder_path, raw_bucket, cert_bucket, analytic_bucket, bq_dataset_id, table_id)
