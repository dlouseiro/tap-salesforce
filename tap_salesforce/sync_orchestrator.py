"""Sync orchestration — coordinates stream extraction across the catalog."""

from __future__ import annotations

import asyncio
import concurrent.futures
from copy import deepcopy

import singer
from singer import metadata, metrics

from tap_salesforce.salesforce import Salesforce
from tap_salesforce.sync import get_stream_version, resume_syncing_bulk_query, sync_stream

LOGGER = singer.get_logger()


class SyncOrchestrator:
    """Orchestrates syncing selected streams from a Salesforce catalog."""

    def __init__(self, sf: Salesforce, config: dict):
        self._sf = sf
        self._config = config

    def sync(self, catalog: dict, state: dict) -> None:
        """Run the sync for all streams in the catalog."""
        LOGGER.info("Starting sync")

        max_workers = self._config.get("max_workers", 8)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        loop = asyncio.get_event_loop()
        loop.set_default_executor(executor)

        try:
            streams_to_sync = catalog["streams"]
            sync_tasks = (self._sync_catalog_entry(catalog_entry, state) for catalog_entry in streams_to_sync)
            tasks = asyncio.gather(*sync_tasks)
            loop.run_until_complete(tasks)
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

        singer.write_state(state)
        LOGGER.info("Finished sync")

    async def _sync_catalog_entry(self, catalog_entry: dict, state: dict) -> None:
        """Sync a single catalog entry (stream)."""
        stream_version = get_stream_version(catalog_entry, state)
        stream = catalog_entry["stream"]
        stream_alias = catalog_entry.get("stream_alias")
        stream_name = catalog_entry["tap_stream_id"]
        activate_version_message = singer.ActivateVersionMessage(
            stream=(stream_alias or stream), version=stream_version
        )

        catalog_metadata = metadata.to_map(catalog_entry["metadata"])
        replication_key = catalog_metadata.get((), {}).get("replication-key")
        mdata = metadata.to_map(catalog_entry["metadata"])

        if not stream_is_selected(mdata):
            LOGGER.debug("%s: Skipping - not selected", stream_name)
            return

        LOGGER.info("%s: Starting", stream_name)

        singer.write_state(state)
        key_properties = metadata.to_map(catalog_entry["metadata"]).get((), {}).get("table-key-properties")

        schema = deepcopy(catalog_entry["schema"])
        pop_deselected_schema(schema, stream_name, (), mdata)

        singer.write_schema(stream, schema, key_properties, replication_key, stream_alias)
        loop = asyncio.get_event_loop()

        job_id = singer.get_bookmark(state, catalog_entry["tap_stream_id"], "JobID")
        if job_id:
            await self._resume_bulk_job(catalog_entry, job_id, state, stream_name, replication_key, loop)
        else:
            await self._sync_fresh(
                catalog_entry, state, stream_name, replication_key,
                activate_version_message, stream_version, loop,
            )

    async def _resume_bulk_job(
        self, catalog_entry: dict, job_id: str, state: dict, stream_name: str, replication_key: str | None, loop
    ) -> None:
        """Resume a previously interrupted bulk query job."""
        stream = catalog_entry["stream"]
        with metrics.record_counter(stream) as counter:
            LOGGER.info("Found JobID from previous Bulk Query. Resuming sync for job: %s", job_id)
            await loop.run_in_executor(
                None,
                resume_syncing_bulk_query,
                self._sf,
                catalog_entry,
                job_id,
                state,
                counter,
            )
            LOGGER.info("Completed sync for %s", stream_name)
            state.get("bookmarks", {}).get(catalog_entry["tap_stream_id"], {}).pop("JobID", None)
            state.get("bookmarks", {}).get(catalog_entry["tap_stream_id"], {}).pop("BatchIDs", None)
            bookmark = (
                state.get("bookmarks", {}).get(catalog_entry["tap_stream_id"], {}).pop("JobHighestBookmarkSeen", None)
            )
            state = singer.write_bookmark(state, catalog_entry["tap_stream_id"], replication_key, bookmark)
            singer.write_state(state)

    async def _sync_fresh(
        self,
        catalog_entry: dict,
        state: dict,
        stream_name: str,
        replication_key: str | None,
        activate_version_message,
        stream_version: int,
        loop,
    ) -> None:
        """Sync a stream from scratch (no previous bulk job to resume)."""
        state_msg_threshold = self._config.get("state_message_threshold", 1000)
        bookmark_is_empty = state.get("bookmarks", {}).get(catalog_entry["tap_stream_id"]) is None

        if replication_key or bookmark_is_empty:
            singer.write_message(activate_version_message)
            state = singer.write_bookmark(state, catalog_entry["tap_stream_id"], "version", stream_version)

        await loop.run_in_executor(None, sync_stream, self._sf, catalog_entry, state, state_msg_threshold)
        LOGGER.info("Completed sync for %s", stream_name)


def build_state(raw_state: dict, catalog: dict) -> dict:
    """Build a clean state from raw state preserving only relevant bookmarks."""
    state = {}

    for catalog_entry in catalog["streams"]:
        tap_stream_id = catalog_entry["tap_stream_id"]
        catalog_metadata = metadata.to_map(catalog_entry["metadata"])
        replication_method = catalog_metadata.get((), {}).get("replication-method")

        version = singer.get_bookmark(raw_state, tap_stream_id, "version")

        if singer.get_bookmark(raw_state, tap_stream_id, "JobID"):
            job_id = singer.get_bookmark(raw_state, tap_stream_id, "JobID")
            batches = singer.get_bookmark(raw_state, tap_stream_id, "BatchIDs")
            current_bookmark = singer.get_bookmark(raw_state, tap_stream_id, "JobHighestBookmarkSeen")
            state = singer.write_bookmark(state, tap_stream_id, "JobID", job_id)
            state = singer.write_bookmark(state, tap_stream_id, "BatchIDs", batches)
            state = singer.write_bookmark(state, tap_stream_id, "JobHighestBookmarkSeen", current_bookmark)

        if replication_method == "INCREMENTAL":
            replication_key = catalog_metadata.get((), {}).get("replication-key")
            replication_key_value = singer.get_bookmark(raw_state, tap_stream_id, replication_key)
            if version is not None:
                state = singer.write_bookmark(state, tap_stream_id, "version", version)
            if replication_key_value is not None:
                state = singer.write_bookmark(state, tap_stream_id, replication_key, replication_key_value)
        elif replication_method == "FULL_TABLE" and version is None:
            state = singer.write_bookmark(state, tap_stream_id, "version", version)

    return state


def stream_is_selected(mdata: dict) -> bool:
    """Check if a stream is selected for extraction."""
    return mdata.get((), {}).get("selected", False)


def is_object_type(property_schema: dict) -> bool | None:
    """Return True if the JSON Schema type is an object, None if detection fails."""
    if "anyOf" not in property_schema and "type" not in property_schema:
        return None
    for property_type in property_schema.get("anyOf", [property_schema.get("type")]):
        if "object" in property_type or property_type == "object":
            return True
    return False


def is_property_selected(stream_name: str, metadata_map: dict, breadcrumb: tuple | str | None) -> bool:  # noqa: C901
    """Return True if the property at the given breadcrumb is selected for extract."""
    breadcrumb = breadcrumb or ()
    if isinstance(breadcrumb, str):
        breadcrumb = (breadcrumb,)

    if not metadata:
        return True

    md_entry = metadata_map.get(breadcrumb, {})
    parent_value = None
    if len(breadcrumb) > 0:
        parent_breadcrumb = tuple(list(breadcrumb)[:-2])
        parent_value = is_property_selected(stream_name, metadata_map, parent_breadcrumb)
    if parent_value is False:
        return parent_value

    selected = md_entry.get("selected")
    selected_by_default = md_entry.get("selected-by-default")
    inclusion = md_entry.get("inclusion")

    if inclusion == "unsupported":
        if selected is True:
            LOGGER.debug(
                "Property '%s' was selected but is not supported. Ignoring selected==True input.",
                ":".join(breadcrumb),
            )
        return False

    if inclusion == "automatic":
        if selected is False:
            LOGGER.debug(
                "Property '%s' was deselected while also set for automatic inclusion. Ignoring selected==False input.",
                ":".join(breadcrumb),
            )
        return True

    if selected is not None:
        return selected

    if selected_by_default is not None:
        return selected_by_default

    LOGGER.debug(
        "Selection metadata omitted for '%s':'%s'. Using parent value of selected=%s.",
        stream_name,
        breadcrumb,
        parent_value,
    )
    return parent_value or False


def pop_deselected_schema(schema: dict, stream_name: str, breadcrumb: tuple, metadata_map: dict) -> None:
    """Remove anything from schema that is not selected (walks recursively in place)."""
    for property_name, val in list(schema.get("properties", {}).items()):
        property_breadcrumb = (*list(breadcrumb), "properties", property_name)
        selected = is_property_selected(stream_name, metadata_map, property_breadcrumb)
        LOGGER.info(stream_name + "." + property_name + " - " + str(selected))
        if not selected:
            schema["properties"].pop(property_name)
            continue

        if is_object_type(val):
            pop_deselected_schema(val, stream_name, property_breadcrumb, metadata_map)
