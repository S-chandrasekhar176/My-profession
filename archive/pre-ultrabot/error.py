from google.cloud import storage
from google.cloud import bigquery
import pandas as pd
import sys
from io import StringIO
from datetime import datetime
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

# Combined Redbook + Blackbook schema (all fields as STRING)
recon_schema = {
    'fields': [
        {'name': 'ZONE', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'DATASET', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'FILE_DATE', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'PROCESSED_DATE', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'PROCESSED_TIME', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'FILENAME', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'RECORDS', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Failed_null_check', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Failed_CapCode_value', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Failed_CapId_value', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Failed_year_column_value', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Failed_plate_column_value', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Failed_Volume_check', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Failed_unique_check', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'CAPCode', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'CAPId', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'VersionChangeDate', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Year', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Letter', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'NewPrice', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'LowMileage', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'LowRetail', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'LowClean', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'LowAverage', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'LowBelow', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Mileage1', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Retail', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Clean1', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Average1', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Below1', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Mileage2', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Retail2', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Clean2', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Average2', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Below2', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Mileage3', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Retail3', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Clean3', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Average3', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Below3', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High1Mileage1', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High1Retail1', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High1Clean', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High1Average', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High1Below', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'HighMileage2', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High2Retail', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High2Clean', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High2Average', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'High2Below', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'EngineCC', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'Insurancegrp2', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'IntroductionDate', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'MONTH', 'type': 'STRING', 'mode': 'NULLABLE'}
    ]
}

# Index Map for Field Checks
ERROR_INDEX_MAP = {
    'capcode': 0,
    'capid': 1,
    'null': [0, 1],
    'year': 3,
    'plate': 4,
    'date': 39
}

# Read file content
def read_csv_file_from_gcs(bucket_name, file_path):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_path)
    content = blob.download_as_text()
    return pd.read_csv(StringIO(content), header=None)

# Process file and detect errors
def process_file(bucket_name, file_path, dataset, zone):
    df = read_csv_file_from_gcs(bucket_name, file_path)
    if df.empty:
        return None

    file_name = file_path.split('/')[-1]
    date_str = file_name[:8]
    file_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    now = datetime.now()

    results = []
    for _, row in df.iterrows():
        error_flags = {
            'Failed_null_check': 'NO', 'Failed_CapCode_value': 'NO',
            'Failed_CapId_value': 'NO', 'Failed_year_column_value': 'NO',
            'Failed_plate_column_value': 'NO', 'Failed_Volume_check': 'NO',
            'Failed_unique_check': 'NO'
        }

        record_data = [''] * len(recon_schema['fields'])

        record_data[0:6] = [zone, dataset, file_date, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), file_name]
        record_data[6] = '1'

        for col_idx, val in row.items():
            if isinstance(val, str) and val.startswith("Failed"):
                if "null" in val:
                    error_flags['Failed_null_check'] = 'YES'
                elif "CapCode" in val:
                    error_flags['Failed_CapCode_value'] = 'YES'
                    record_data[[f['name'] for f in recon_schema['fields']].index('CAPCode')] = str(row[ERROR_INDEX_MAP['capcode']])
                elif "CapId" in val:
                    error_flags['Failed_CapId_value'] = 'YES'
                    record_data[[f['name'] for f in recon_schema['fields']].index('CAPId')] = str(row[ERROR_INDEX_MAP['capid']])
                elif "year" in val:
                    error_flags['Failed_year_column_value'] = 'YES'
                    record_data[[f['name'] for f in recon_schema['fields']].index('Year')] = str(row[ERROR_INDEX_MAP['year']])
                elif "plate" in val:
                    error_flags['Failed_plate_column_value'] = 'YES'
                    record_data[[f['name'] for f in recon_schema['fields']].index('Letter')] = str(row[ERROR_INDEX_MAP['plate']])
                elif "volume" in val:
                    error_flags['Failed_Volume_check'] = 'YES'
                elif "unique" in val:
                    error_flags['Failed_unique_check'] = 'YES'

        for key, val in error_flags.items():
            idx = [f['name'] for f in recon_schema['fields']].index(key)
            record_data[idx] = val

        results.append(dict(zip([f['name'] for f in recon_schema['fields']], record_data)))

    return results

# Apache Beam Runner
def run_pipeline(project_id, raw_zone_bucket, certify_zone_bucket, folder_path, bq_dataset_id, bq_table_name, dataset):
    options = PipelineOptions(
        project=project_id,
        job_name=f'reconciliation-{dataset}'.lower(),
        runner="DataflowRunner",
        region='your-region',
        staging_location=f'gs://{raw_zone_bucket}/staging',
        temp_location=f'gs://{raw_zone_bucket}/temp',
        save_main_session=True
    )

    with beam.Pipeline(options=options) as p:
        raw_files = list_files_in_folder(raw_zone_bucket, folder_path)
        certify_files = list_files_in_folder(certify_zone_bucket, folder_path)

        raw_data = (p | "CreateRawFileList" >> beam.Create(raw_files)
                     | "ProcessRaw" >> beam.FlatMap(lambda file: process_file(raw_zone_bucket, file, dataset, 'RAWZONE')))

        cert_data = (p | "CreateCertFileList" >> beam.Create(certify_files)
                      | "ProcessCert" >> beam.FlatMap(lambda file: process_file(certify_zone_bucket, file, dataset, 'CERTIFY')))

        (raw_data, cert_data) | "FlattenAll" >> beam.Flatten() | "WriteBQ" >> beam.io.WriteToBigQuery(
                                  f"{project_id}:{bq_dataset_id}.{bq_table_name}",
                                  schema=recon_schema,
                                  write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND,
                                  create_disposition=beam.io.BigQueryDisposition.CREATE_IF_NEEDED
                              )

def list_files_in_folder(bucket_name, folder_path):
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    return [blob.name for blob in bucket.list_blobs(prefix=folder_path) if blob.name.endswith(".csv")]

if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else 'BLACKBOOK'
    project_id = 'your_project_id'
    raw_zone_bucket = 'your_raw_zone_bucket'
    certify_zone_bucket = 'your_certify_zone_bucket'
    folder_path = 'data_folder_path/'
    bq_dataset_id = 'your_dataset_id'
    bq_table_name = 'consolidated_record_count_report'

    print("******** Recon Started ********")
    run_pipeline(project_id, raw_zone_bucket, certify_zone_bucket, folder_path, bq_dataset_id, bq_table_name, dataset)
    print("******** Recon Completed ********")
