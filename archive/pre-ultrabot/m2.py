file_to_key_mapping = {
    "PreEmbargoed_Land_Rover_CAPVehicles.csv": "CAPVehicles",
    "PreEmbargoed_Land_Rover_CapDer.csv": "CapDer",
    "PreEmbargoed_Land_Rover_NVDGenericStatus.csv": "NVDGenericStatus",
    "PreEmbargoed_Land_Rover_NVDModelYear.csv": "NVDModelYear",
    "PreEmbargoed_Land_Rover_NVDOptions.csv": "NVDOptions",
    "PreEmbargoed_Land_Rover_NVDPrices.csv": "NVDPrices",
    "PreEmbargoed_Land_Rover_NVDStandardEquipment.csv": "NVDStandardEquipment",
    "PreEmbargoed_Land_Rover_NVDTechnical.csv": "NVDTechnical"
}

key_to_bq_table_mapping = {
    "CAPVehicles": "PRE_EMBARGO_LR_CAPVEHICLES",
    "CapDer": "PRE_EMBARGO_LR_CAPDER",
    "NVDGenericStatus": "PRE_EMBARGO_LR_NDVGENERICSTATUS",
    "NVDModelYear": "PRE_EMBARGO_LR_NVDMODELYEAR",
    "NVDOptions": "PRE_EMBARGO_LR_NVDOPTIONS",
    "NVDPrices": "PRE_EMBARGO_LR_NVDPRICES",
    "NVDStandardEquipment": "PRE_EMBARGO_LR_NVDSTANDARDEQUIPMENT",
    "NVDTechnical": "PRE_EMBARGO_LR_NVDTECHNICAL"
}

def process_files(_, buckets_info, folder_path, dataset, project_id, bq_dataset_id):
    records = {}
    dataset_info = dataset_mapping.get(dataset, {})

    for zone, bucket_name in buckets_info.items():
        logging.info(f"Processing zone: {zone}, bucket: {bucket_name}")
        files = list_files_in_folder(bucket_name, folder_path)

        for file in files:
            filename = file.split("/")[-1]
            if filename.endswith(("schema.csv", "metadata.csv")):
                logging.info(f"Skipping file: {filename} (metadata or schema file)")
                continue

            # Get the key from the filename
            key = file_to_key_mapping.get(filename, "")
            if not key:
                logging.warning(f"No key mapping found for file: {filename}")
                continue

            # Get the BigQuery table name from the key
            bq_table_name = key_to_bq_table_mapping.get(key, "")
            if not bq_table_name:
                logging.warning(f"No table mapping found for key: {key}")
                continue

            logging.info(f"Found table mapping: {bq_table_name} for file: {filename}")
            bq_schema = get_bq_schema(project_id, bq_dataset_id, bq_table_name)

            skip_header = (zone == "ANALYTIC")
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
                    "RAW_RECORDS": 0, "CERT_RECORDS": 0, "ANALYTIC_RECORDS": 0,
                    "RAW_FAILED_RECORDS": 0, "CERT_FAILED_RECORDS": 0, "ANALYTIC_FAILED_RECORDS": 0,
                    "RAW_COLUMN": 0, "CERT_COLUMN": 0, "ANALYTIC_COLUMN": 0,
                    "ANALYTIC_col_sums": [],
                    "BQ_STATUS": "", "BQ_FAILED": 0, "REASON": ""
                }

            if zone == "RAW":
                records[filename].update({"RAW_RECORDS": record_count, "RAW_COLUMN": column_count, "RAW_FAILED_RECORDS": source_count - record_count})
            elif zone == "CERT":
                records[filename].update({"CERT_RECORDS": record_count, "CERT_COLUMN": column_count, "CERT_FAILED_RECORDS": records[filename]["RAW_RECORDS"] - record_count})
            elif zone == "ANALYTIC":
                records[filename].update({"ANALYTIC_RECORDS": record_count, "ANALYTIC_COLUMN": column_count, "ANALYTIC_FAILED_RECORDS": records[filename]["CERT_RECORDS"] - record_count, "ANALYTIC_col_sums": column_sums})

    if bq_table_name:
        logging.info(f"Calling analytic_to_bq_checking for table: {bq_table_name}")
        records = analytic_to_bq_checking(buckets_info["ANALYTIC"], dataset, project_id, records, bq_table_name)
    else:
        logging.warning("No bq_table_name found. Skipping analytic_to_bq_checking.")

    logging.info(f"Finished process_files for dataset: {dataset}")
    return list(records.values())
