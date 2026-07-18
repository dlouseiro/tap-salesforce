"""Salesforce API facade — composes client, auth, and schema modules."""

from __future__ import annotations

import json
from datetime import timedelta

import singer
import singer.utils as singer_utils
from singer import metadata, metrics

from tap_salesforce.salesforce.bulk import Bulk
from tap_salesforce.salesforce.bulk2 import Bulk2
from tap_salesforce.salesforce.client import SalesforceClient
from tap_salesforce.salesforce.credentials import SalesforceAuth
from tap_salesforce.salesforce.exceptions import (
    TapSalesforceExceptionError,
)
from tap_salesforce.salesforce.rest import Rest
from tap_salesforce.salesforce.schema import (
    QUERY_INCOMPATIBLE_SALESFORCE_OBJECTS,
    QUERY_RESTRICTED_SALESFORCE_OBJECTS,
    UNSUPPORTED_BULK_API_SALESFORCE_OBJECTS,
)
from tap_salesforce.salesforce.schema import (
    field_to_property_schema as field_to_property_schema,
)

LOGGER = singer.get_logger()

BULK_API_TYPE = "BULK"
BULK2_API_TYPE = "BULK2"
REST_API_TYPE = "REST"


class Salesforce:
    """High-level Salesforce API client coordinating auth, HTTP, and queries."""

    def __init__(
        self,
        credentials=None,
        token=None,
        quota_percent_per_run=None,
        quota_percent_total=None,
        is_sandbox=None,
        select_fields_by_default=None,
        default_start_date=None,
        api_type=None,
        lookback_window=None,
        api_version=None,
        ignore_formula_fields=False,
        soql_filters=None,
        redirect_uri=None,
    ):
        self.api_type = api_type.upper() if api_type else None
        if isinstance(quota_percent_per_run, str) and quota_percent_per_run.strip() == "":
            quota_percent_per_run = None
        if isinstance(quota_percent_total, str) and quota_percent_total.strip() == "":
            quota_percent_total = None

        self.quota_percent_per_run = float(quota_percent_per_run) if quota_percent_per_run is not None else 25
        self.quota_percent_total = float(quota_percent_total) if quota_percent_total is not None else 80
        self.is_sandbox = is_sandbox is True or (isinstance(is_sandbox, str) and is_sandbox.lower() == "true")
        self.select_fields_by_default = select_fields_by_default is True or (
            isinstance(select_fields_by_default, str) and select_fields_by_default.lower() == "true"
        )
        self.ignore_formula_fields = ignore_formula_fields
        self.soql_filters = soql_filters or {}
        self.pk_chunking = False
        self.lookback_window = lookback_window
        self.api_version = api_version
        self.data_url = "{}/services/data/{}/{}"

        self.auth = SalesforceAuth.from_credentials(credentials, is_sandbox=self.is_sandbox, redirect_uri=redirect_uri)

        # Compose the HTTP client
        self._client = SalesforceClient(
            auth=self.auth,
            quota_percent_per_run=self.quota_percent_per_run,
            quota_percent_total=self.quota_percent_total,
        )

        # Validate start_date
        self.default_start_date = (
            singer_utils.strptime_to_utc(default_start_date)
            if default_start_date
            else (singer_utils.now() - timedelta(weeks=4))
        ).isoformat()

        if default_start_date:
            LOGGER.info(
                "Parsed start date '%s' from value '%s'",
                self.default_start_date,
                default_start_date,
            )

    @property
    def rest_requests_attempted(self) -> int:
        return self._client.rest_requests_attempted

    @property
    def jobs_completed(self) -> int:
        return self._client.jobs_completed

    @jobs_completed.setter
    def jobs_completed(self, value: int) -> None:
        self._client.jobs_completed = value

    @property
    def instance_url(self) -> str:
        return self._client.instance_url

    @property
    def session(self):
        return self._client.session

    def login(self) -> None:
        self._client.login()

    def _make_request(self, http_method, url, headers=None, body=None, stream=False, params=None):
        """Delegate to the client's make_request (preserves internal API for Bulk/Rest)."""
        return self._client.make_request(http_method, url, headers, body, stream, params)

    def check_rest_quota_usage(self, headers):
        self._client.check_rest_quota_usage(headers)

    def describe(self, sobject=None):
        """Describes all objects or a specific object."""
        headers = self.auth.rest_headers
        instance_url = self.auth.instance_url
        body = None
        method = "GET"
        if sobject is None:
            endpoint = "sobjects"
            endpoint_tag = "sobjects"
            url = self.data_url.format(instance_url, self.api_version, endpoint)
        elif isinstance(sobject, list):
            batch_length = len(sobject)
            if batch_length > 25:
                raise TapSalesforceExceptionError(f"Composite limited to 25 sObjects per batch. ({batch_length}).")
            endpoint = "composite/batch"
            endpoint_tag = "CompositeBatch"
            url = self.data_url.format(instance_url, self.api_version, endpoint)
            method = "POST"
            headers["Content-Type"] = "application/json"
            composite_subrequests = []
            for obj in sobject:
                sub_endpoint = f"sobjects/{obj}/describe"
                sub_url = self.data_url.format("", self.api_version, sub_endpoint)
                subrequest = {"method": "GET", "url": sub_url}
                composite_subrequests.append(subrequest)
            body = json.dumps({"batchRequests": composite_subrequests})
        else:
            endpoint = f"sobjects/{sobject}/describe"
            endpoint_tag = sobject
            url = self.data_url.format(instance_url, self.api_version, endpoint)

        with metrics.http_request_timer("describe") as timer:
            timer.tags["endpoint"] = endpoint_tag
            resp = self._make_request(method, url, headers=headers, body=body)

        if isinstance(sobject, list):
            return resp.json()["results"]
        else:
            return resp.json()

    def _get_selected_properties(self, catalog_entry):
        mdata = metadata.to_map(catalog_entry["metadata"])
        properties = catalog_entry["schema"].get("properties", {})

        return [
            k
            for k in properties
            if singer.should_sync_field(
                metadata.get(mdata, ("properties", k), "inclusion"),
                metadata.get(mdata, ("properties", k), "selected"),
                self.select_fields_by_default,
            )
        ]

    def get_start_date(self, state, catalog_entry):
        catalog_metadata = metadata.to_map(catalog_entry["metadata"])
        replication_key = catalog_metadata.get((), {}).get("replication-key")

        bookmark_value = singer.get_bookmark(state, catalog_entry["tap_stream_id"], replication_key)
        sync_start_date = bookmark_value or self.default_start_date

        if bookmark_value and self.lookback_window:
            sync_start_date = singer_utils.strftime(
                singer_utils.strptime_with_tz(sync_start_date) - timedelta(seconds=self.lookback_window)
            )

        return sync_start_date

    def _build_query_string(self, catalog_entry, start_date, end_date=None, order_by_clause=True):
        selected_properties = self._get_selected_properties(catalog_entry)

        query = "SELECT {} FROM {}".format(",".join(selected_properties), catalog_entry["stream"])

        catalog_metadata = metadata.to_map(catalog_entry["metadata"])
        replication_key = catalog_metadata.get((), {}).get("replication-key")

        user_filter = None
        if isinstance(self.soql_filters, dict):
            user_filter = self.soql_filters.get(catalog_entry["stream"])

        clauses = []
        if user_filter:
            clauses.append(f"({user_filter})")

        if replication_key:
            window = f"{replication_key} >= {start_date}"
            if end_date:
                window += f" AND {replication_key} < {end_date}"
            clauses.append(f"({window})")

        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        if replication_key and order_by_clause:
            query += f" ORDER BY {replication_key} ASC"

        return query

    def query(self, catalog_entry, state):
        if self.api_type == BULK_API_TYPE:
            bulk = Bulk(self)
            return bulk.query(catalog_entry, state)
        elif self.api_type == BULK2_API_TYPE:
            bulk = Bulk2(self)
            return bulk.query(catalog_entry, state)
        elif self.api_type == REST_API_TYPE:
            rest = Rest(self)
            return rest.query(catalog_entry, state)
        else:
            raise TapSalesforceExceptionError(f"api_type should be REST or BULK was: {self.api_type}")

    def get_blacklisted_objects(self):
        if self.api_type in [BULK_API_TYPE, BULK2_API_TYPE]:
            return UNSUPPORTED_BULK_API_SALESFORCE_OBJECTS.union(QUERY_RESTRICTED_SALESFORCE_OBJECTS).union(
                QUERY_INCOMPATIBLE_SALESFORCE_OBJECTS
            )
        elif self.api_type == REST_API_TYPE:
            return QUERY_RESTRICTED_SALESFORCE_OBJECTS.union(QUERY_INCOMPATIBLE_SALESFORCE_OBJECTS)
        else:
            raise TapSalesforceExceptionError(f"api_type should be REST or BULK was: {self.api_type}")

    def get_blacklisted_fields(self):
        if self.api_type == BULK_API_TYPE or self.api_type == BULK2_API_TYPE:
            return {
                (
                    "EntityDefinition",
                    "RecordTypesSupported",
                ): "this field is unsupported by the Bulk API."
            }
        elif self.api_type == REST_API_TYPE:
            return {}
        else:
            raise TapSalesforceExceptionError(f"api_type should be REST or BULK was: {self.api_type}")
