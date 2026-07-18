"""Schema discovery for Salesforce objects."""

from __future__ import annotations

import json
import sys

import singer
from singer import metadata

import tap_salesforce.salesforce
from tap_salesforce.config import FORCED_FULL_TABLE
from tap_salesforce.salesforce import Salesforce

LOGGER = singer.get_logger()


class DiscoveryService:
    """Discovers Salesforce objects and generates Singer catalog schemas."""

    def __init__(self, sf: Salesforce):
        self._sf = sf

    def discover(self, streams: list[str]) -> None:
        """Run discovery and write the catalog JSON to stdout.

        Args:
            streams: Specific streams to discover. If empty, discovers all.
        """
        if not streams:
            LOGGER.info("Start discovery for all streams")
            global_description = self._sf.describe()
            objects_to_discover = {o["name"] for o in global_description["sobjects"]}
        else:
            LOGGER.info(f"Start discovery: {streams=}")
            objects_to_discover = streams

        entries = self._discover_objects(objects_to_discover)
        result = {"streams": entries}
        json.dump(result, sys.stdout, indent=4)

    def _discover_objects(self, objects_to_discover: set[str]) -> list[dict]:
        """Describe all requested objects and build catalog entries."""
        key_properties = ["Id"]
        sf_custom_setting_objects = []
        object_to_tag_references = {}

        batches = self._build_batches(objects_to_discover)
        entries = []

        for batch in batches:
            sobject_descriptions = self._sf.describe(batch)

            for subrequest_result in sobject_descriptions:
                sobject_description = subrequest_result["result"]
                sobject_name = sobject_description["name"]

                if sobject_description.get("customSetting"):
                    sf_custom_setting_objects.append(sobject_name)
                elif sobject_name.endswith("__Tag"):
                    relationship_field = next(
                        (f for f in sobject_description["fields"] if f.get("relationshipName") == "Item"),
                        None,
                    )
                    if relationship_field:
                        object_to_tag_references[relationship_field["referenceTo"][0]] = sobject_name

                entry = self._build_catalog_entry(sobject_description, key_properties)
                if entry is not None:
                    entries.append(entry)

        entries = self._remove_unsupported_tag_objects(entries, sf_custom_setting_objects, object_to_tag_references)
        return entries

    def _build_batches(self, objects_to_discover: set[str]) -> list[list[str]]:
        """Split objects into batches of 25 for the composite API."""
        batches: list[list[str]] = []
        batch: list[str] = []

        for sobject_name in objects_to_discover:
            if sobject_name in self._sf.get_blacklisted_objects() or sobject_name.endswith("ChangeEvent"):
                continue
            batch.append(sobject_name)
            if len(batch) == 25:
                batches.append(batch)
                batch = []

        if batch:
            batches.append(batch)
        return batches

    def _build_catalog_entry(self, sobject_description: dict, key_properties: list[str]) -> dict | None:
        """Build a single catalog entry from an object description."""
        sobject_name = sobject_description["name"]
        fields = sobject_description["fields"]
        replication_key = get_replication_key(sobject_name, fields)

        unsupported_fields = set()
        properties = {}
        mdata = metadata.new()
        found_id_field = False

        for f in fields:
            field_name = f["name"]

            if field_name == "Id":
                found_id_field = True

            property_schema, mdata = self._create_property_schema(f, mdata)
            self._check_field_support(f, sobject_name, unsupported_fields)

            inclusion = metadata.get(mdata, ("properties", field_name), "inclusion")
            if self._sf.select_fields_by_default and inclusion != "unsupported":
                mdata = metadata.write(mdata, ("properties", field_name), "selected-by-default", True)

            properties[field_name] = property_schema

        if not found_id_field:
            LOGGER.info("Skipping Salesforce Object %s, as it has no Id field", sobject_name)
            return None

        if replication_key:
            mdata = metadata.write(mdata, ("properties", replication_key), "inclusion", "automatic")

        mdata = self._apply_unsupported_fields(mdata, unsupported_fields, fields, sobject_name)
        mdata = self._set_replication_metadata(mdata, replication_key)
        mdata = metadata.write(mdata, (), "table-key-properties", key_properties)

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
        }

        return {
            "stream": sobject_name,
            "tap_stream_id": sobject_name,
            "schema": schema,
            "metadata": metadata.to_list(mdata),
        }

    def _create_property_schema(self, field: dict, mdata) -> tuple[dict, object]:
        """Create a JSON Schema property and metadata for a single field."""
        field_name = field["name"]

        if field_name == "Id":
            mdata = metadata.write(mdata, ("properties", field_name), "inclusion", "automatic")
        else:
            mdata = metadata.write(mdata, ("properties", field_name), "inclusion", "available")

        property_schema, mdata = tap_salesforce.salesforce.field_to_property_schema(
            field, mdata, self._sf.ignore_formula_fields
        )
        return property_schema, mdata

    def _check_field_support(self, field: dict, sobject_name: str, unsupported_fields: set) -> None:
        """Check if a field is unsupported for the current API type."""
        field_name = field["name"]

        if field["type"] in ("address", "location") and self._sf.api_type in [
            tap_salesforce.salesforce.BULK_API_TYPE,
            tap_salesforce.salesforce.BULK2_API_TYPE,
        ]:
            unsupported_fields.add((field_name, "cannot query compound address fields with bulk API"))

        if field["type"] == "json":
            unsupported_fields.add(
                (field_name, "do not currently support json fields - please contact support")
            )

        field_pair = (sobject_name, field_name)
        if field_pair in self._sf.get_blacklisted_fields():
            unsupported_fields.add((field_name, self._sf.get_blacklisted_fields()[field_pair]))

    def _apply_unsupported_fields(self, mdata, unsupported_fields: set, fields: list, sobject_name: str):
        """Mark unsupported fields in metadata."""
        field_name_set = {f["name"] for f in fields}
        filtered = [f for f in unsupported_fields if f[0] in field_name_set]
        missing = [f[0] for f in unsupported_fields if f[0] not in field_name_set]

        if missing:
            LOGGER.info(
                "Ignoring the following unsupported fields for object %s as they are missing from the field list: %s",
                sobject_name,
                ", ".join(sorted(missing)),
            )

        if filtered:
            LOGGER.info(
                "Not syncing the following unsupported fields for object %s: %s",
                sobject_name,
                ", ".join(sorted([k for k, _ in filtered])),
            )

        for prop, description in filtered:
            if metadata.get(mdata, ("properties", prop), "selected-by-default"):
                metadata.delete(mdata, ("properties", prop), "selected-by-default")
            mdata = metadata.write(mdata, ("properties", prop), "unsupported-description", description)
            mdata = metadata.write(mdata, ("properties", prop), "inclusion", "unsupported")

        return mdata

    def _set_replication_metadata(self, mdata, replication_key: str | None):
        """Set replication method and key metadata on the stream."""
        if replication_key:
            mdata = metadata.write(mdata, (), "valid-replication-keys", [replication_key])
            mdata = metadata.write(mdata, (), "replication-key", replication_key)
            mdata = metadata.write(mdata, (), "replication-method", "INCREMENTAL")
        else:
            mdata = metadata.write(
                mdata,
                (),
                "forced-replication-method",
                {
                    "replication-method": "FULL_TABLE",
                    "reason": "No replication keys found from the Salesforce API",
                },
            )
        return mdata

    def _remove_unsupported_tag_objects(
        self, entries: list[dict], sf_custom_setting_objects: list, object_to_tag_references: dict
    ) -> list[dict]:
        """Remove tag objects associated with custom settings (unsupported by Bulk API)."""
        unsupported_tag_objects = [
            object_to_tag_references[f] for f in sf_custom_setting_objects if f in object_to_tag_references
        ]
        if unsupported_tag_objects:
            LOGGER.info(
                "Skipping the following Tag objects, Tags on Custom Settings Salesforce objects "
                "are not supported by the Bulk API:"
            )
            LOGGER.info(unsupported_tag_objects)
            entries = [e for e in entries if e["stream"] not in unsupported_tag_objects]
        return entries


def get_replication_key(sobject_name: str, fields: list[dict]) -> str | None:
    """Determine the replication key for a Salesforce object."""
    if sobject_name in FORCED_FULL_TABLE:
        return None

    fields_list = [f["name"] for f in fields]

    if "SystemModstamp" in fields_list:
        return "SystemModstamp"
    elif "LastModifiedDate" in fields_list:
        return "LastModifiedDate"
    elif "CreatedDate" in fields_list:
        return "CreatedDate"
    elif "LoginTime" in fields_list and sobject_name == "LoginHistory":
        return "LoginTime"
    return None
