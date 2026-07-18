"""Record synchronization — extraction, transformation, and state management."""

from __future__ import annotations

import time

import singer
import singer.utils as singer_utils
from requests.exceptions import RequestException
from singer import Transformer, metadata, metrics

from tap_salesforce.salesforce.bulk import Bulk

LOGGER = singer.get_logger()

BLACKLISTED_FIELDS = {"attributes"}


class SyncService:
    """Handles record-level sync: extraction, transformation, and bookmark management."""

    def __init__(self, sf):
        self._sf = sf

    def sync_stream(self, catalog_entry: dict, state: dict, state_msg_threshold: int) -> None:
        """Sync all records for a single stream."""
        stream = catalog_entry["stream"]

        with metrics.record_counter(stream) as counter:
            try:
                self._sync_records(catalog_entry, state, counter, state_msg_threshold)
                singer.write_state(state)
            except RequestException as ex:
                raise Exception(f"Error syncing {stream}: {ex} Response: {ex.response.text}")  # noqa: B904
            except Exception as ex:
                raise Exception(f"Error syncing {stream}: {ex}") from ex

    def resume_syncing_bulk_query(self, catalog_entry: dict, job_id: str, state: dict, counter) -> None:
        """Resume a previously interrupted bulk query job."""
        bulk = Bulk(self._sf)
        current_bookmark = singer.get_bookmark(
            state, catalog_entry["tap_stream_id"], "JobHighestBookmarkSeen"
        ) or self._sf.get_start_date(state, catalog_entry)
        current_bookmark = singer_utils.strptime_with_tz(current_bookmark)
        batch_ids = singer.get_bookmark(state, catalog_entry["tap_stream_id"], "BatchIDs")

        start_time = singer_utils.now()
        stream = catalog_entry["stream"]
        stream_alias = catalog_entry.get("stream_alias")
        catalog_metadata = metadata.to_map(catalog_entry.get("metadata"))
        replication_key = catalog_metadata.get((), {}).get("replication-key")
        stream_version = get_stream_version(catalog_entry, state)
        schema = catalog_entry["schema"]

        if not bulk.job_exists(job_id):
            LOGGER.info("Found stored Job ID that no longer exists, resetting bookmark and removing JobID from state.")
            return

        for batch_id in batch_ids[:]:
            with Transformer(pre_hook=transform_bulk_data_hook) as transformer:
                for rec in bulk.get_batch_results(job_id, batch_id, catalog_entry):
                    counter.increment()
                    rec = transformer.transform(rec, schema)
                    rec = fix_record_anytype(rec, schema)
                    singer.write_message(
                        singer.RecordMessage(
                            stream=(stream_alias or stream),
                            record=rec,
                            version=stream_version,
                            time_extracted=start_time,
                        )
                    )

                    replication_key_value = replication_key and singer_utils.strptime_with_tz(rec[replication_key])
                    if (
                        replication_key_value
                        and replication_key_value <= start_time
                        and replication_key_value > current_bookmark
                    ):
                        current_bookmark = singer_utils.strptime_with_tz(rec[replication_key])

            state = singer.write_bookmark(
                state,
                catalog_entry["tap_stream_id"],
                "JobHighestBookmarkSeen",
                singer_utils.strftime(current_bookmark),
            )
            batch_ids.remove(batch_id)
            LOGGER.info("Finished syncing batch %s. Removing batch from state.", batch_id)
            LOGGER.info("Batches to go: %d", len(batch_ids))
            singer.write_state(state)

    def _sync_records(self, catalog_entry: dict, state: dict, counter, state_msg_threshold: int) -> None:
        """Iterate over query results, transform, write records, and manage bookmarks."""
        chunked_bookmark = singer_utils.strptime_with_tz(self._sf.get_start_date(state, catalog_entry))
        stream = catalog_entry["stream"]
        schema = catalog_entry["schema"]
        stream_alias = catalog_entry.get("stream_alias")
        catalog_metadata = metadata.to_map(catalog_entry["metadata"])
        replication_key = catalog_metadata.get((), {}).get("replication-key")
        stream_version = get_stream_version(catalog_entry, state)
        activate_version_message = singer.ActivateVersionMessage(
            stream=(stream_alias or stream), version=stream_version
        )

        start_time = singer_utils.now()

        LOGGER.info("Syncing Salesforce data for stream %s", stream)

        for rec in self._sf.query(catalog_entry, state):
            counter.increment()
            with Transformer(pre_hook=transform_bulk_data_hook) as transformer:
                rec = transformer.transform(rec, schema)
            rec = fix_record_anytype(rec, schema)
            singer.write_message(
                singer.RecordMessage(
                    stream=(stream_alias or stream),
                    record=rec,
                    version=stream_version,
                    time_extracted=start_time,
                )
            )

            replication_key_value = replication_key and singer_utils.strptime_with_tz(rec[replication_key])

            if self._sf.pk_chunking:
                if (
                    replication_key_value
                    and replication_key_value <= start_time
                    and replication_key_value > chunked_bookmark
                ):
                    chunked_bookmark = singer_utils.strptime_with_tz(rec[replication_key])
                    state = singer.write_bookmark(
                        state,
                        catalog_entry["tap_stream_id"],
                        "JobHighestBookmarkSeen",
                        singer_utils.strftime(chunked_bookmark),
                    )

                    if counter.value % state_msg_threshold == 0:
                        singer.write_state(state)
            elif replication_key_value and replication_key_value <= start_time:
                state = singer.write_bookmark(
                    state,
                    catalog_entry["tap_stream_id"],
                    replication_key,
                    rec[replication_key],
                )

                if counter.value % state_msg_threshold == 0:
                    singer.write_state(state)

        if not replication_key:
            singer.write_message(activate_version_message)
            state = singer.write_bookmark(state, catalog_entry["tap_stream_id"], "version", None)

        if self._sf.pk_chunking:
            state = singer.write_bookmark(
                state,
                catalog_entry["tap_stream_id"],
                replication_key,
                singer_utils.strftime(chunked_bookmark),
            )


def get_stream_version(catalog_entry: dict, state: dict) -> int:
    """Determine the version number for a stream sync."""
    tap_stream_id = catalog_entry["tap_stream_id"]
    catalog_metadata = metadata.to_map(catalog_entry["metadata"])
    replication_key = catalog_metadata.get((), {}).get("replication-key")

    if singer.get_bookmark(state, tap_stream_id, "version") is None:
        stream_version = int(time.time() * 1000)
    else:
        stream_version = singer.get_bookmark(state, tap_stream_id, "version")

    if replication_key:
        return stream_version
    return int(time.time() * 1000)


def remove_blacklisted_fields(data: dict) -> dict:
    """Remove fields that should not appear in output records."""
    return {k: v for k, v in data.items() if k not in BLACKLISTED_FIELDS}


def transform_bulk_data_hook(data, typ, schema):
    """Pre-hook for Singer Transformer: cleans bulk API data."""
    result = data
    if isinstance(data, dict):
        result = remove_blacklisted_fields(data)

    if data == "" and "null" in schema["type"]:
        result = None

    return result


def fix_record_anytype(rec: dict, schema: dict) -> dict:
    """Fix records where the schema has no 'type' due to SF 'anyType' fields."""

    def try_cast(val, coercion):
        try:
            return coercion(val)
        except BaseException:
            return val

    for k, v in rec.items():
        if schema["properties"][k].get("type") is None:
            val = v
            val = try_cast(v, int)
            val = try_cast(v, float)
            if v in ["true", "false"]:
                val = v == "true"

            if v == "":
                val = None

            rec[k] = val

    return rec


# Keep backward-compatible module-level functions that delegate to SyncService
def sync_stream(sf, catalog_entry, state, state_msg_threshold):
    """Module-level convenience wrapper for SyncService.sync_stream."""
    service = SyncService(sf)
    service.sync_stream(catalog_entry, state, state_msg_threshold)


def resume_syncing_bulk_query(sf, catalog_entry, job_id, state, counter):
    """Module-level convenience wrapper for SyncService.resume_syncing_bulk_query."""
    service = SyncService(sf)
    service.resume_syncing_bulk_query(catalog_entry, job_id, state, counter)
