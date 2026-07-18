"""HTTP transport layer for Salesforce API requests."""

from __future__ import annotations

import re

import backoff
import requests
import singer

from tap_salesforce.salesforce.credentials import SalesforceAuth
from tap_salesforce.salesforce.exceptions import (
    SFDCCustomNotAcceptableError,
    TapSalesforceExceptionError,
    TapSalesforceQuotaExceededError,
)

LOGGER = singer.get_logger()


def log_backoff_attempt(details):
    LOGGER.info("ConnectionError detected, triggering backoff: %d try", details.get("tries"))


def raise_for_status(resp: requests.Response) -> None:
    """Raise with additional handling for Salesforce-specific HTTP errors.

    ``CustomNotAcceptable`` (406) is ephemeral and resolved after retries.
    """
    if resp.status_code >= 400:
        err_msg = f"{resp.status_code} Client Error: {resp.reason} for url: {resp.url}"
        LOGGER.warning(err_msg)

    if resp.status_code == 406 and "CustomNotAcceptable" in resp.reason:
        raise SFDCCustomNotAcceptableError(err_msg)
    else:
        resp.raise_for_status()


class SalesforceClient:
    """Handles HTTP transport, retries, and quota tracking for Salesforce API calls."""

    def __init__(
        self,
        auth: SalesforceAuth,
        quota_percent_per_run: float = 25.0,
        quota_percent_total: float = 80.0,
    ):
        self.session = requests.Session()
        self.auth = auth
        self.quota_percent_per_run = quota_percent_per_run
        self.quota_percent_total = quota_percent_total
        self.rest_requests_attempted = 0
        self.jobs_completed = 0

    @property
    def instance_url(self) -> str:
        return self.auth.instance_url

    def login(self) -> None:
        self.auth.login()

    @backoff.on_exception(
        backoff.expo,
        (requests.exceptions.ConnectionError, SFDCCustomNotAcceptableError),
        max_tries=10,
        factor=2,
        on_backoff=log_backoff_attempt,
    )
    def make_request(
        self, http_method: str, url: str, headers=None, body=None, stream: bool = False, params=None
    ) -> requests.Response:
        """Execute an HTTP request with retry and quota tracking."""
        if http_method == "GET":
            LOGGER.info("Making %s request to %s with params: %s", http_method, url, params)
            resp = self.session.get(url, headers=headers, stream=stream, params=params)
        elif http_method == "POST":
            LOGGER.info("Making %s request to %s with body %s", http_method, url, body)
            resp = self.session.post(url, headers=headers, data=body)
        else:
            raise TapSalesforceExceptionError("Unsupported HTTP method")

        raise_for_status(resp)

        if resp.headers.get("Sforce-Limit-Info") is not None:
            self.rest_requests_attempted += 1
            self.check_rest_quota_usage(resp.headers)

        return resp

    def check_rest_quota_usage(self, headers: dict) -> None:
        """Check REST API quota usage from response headers and raise if exceeded."""
        match = re.search(r"^api-usage=(\d+)/(\d+)$", headers.get("Sforce-Limit-Info"))

        if match is None:
            return

        remaining, allotted = map(int, match.groups())

        LOGGER.info("Used %s of %s daily REST API quota", remaining, allotted)

        percent_used_from_total = (remaining / allotted) * 100
        max_requests_for_run = int((self.quota_percent_per_run * allotted) / 100)

        if percent_used_from_total > self.quota_percent_total:
            total_message = (
                "Salesforce has reported {}/{} ({:3.2f}%) total REST quota "
                + "used across all Salesforce Applications. Terminating "
                + "replication to not continue past configured percentage "
                + "of {}% total quota."
            ).format(remaining, allotted, percent_used_from_total, self.quota_percent_total)
            raise TapSalesforceQuotaExceededError(total_message)
        elif self.rest_requests_attempted > max_requests_for_run:
            partial_message = (
                "This replication job has made {} REST requests ({:3.2f}% of "
                + "total quota). Terminating replication due to allotted "
                + "quota of {}% per replication."
            ).format(
                self.rest_requests_attempted,
                (self.rest_requests_attempted / allotted) * 100,
                self.quota_percent_per_run,
            )
            raise TapSalesforceQuotaExceededError(partial_message)
