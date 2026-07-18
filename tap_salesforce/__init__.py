"""tap-salesforce: Singer.io tap for extracting data from the Salesforce API."""

from __future__ import annotations

import sys

import singer
import singer.utils as singer_utils

from tap_salesforce.config import DEFAULT_CONFIG, REQUIRED_CONFIG_KEYS
from tap_salesforce.discovery import DiscoveryService
from tap_salesforce.salesforce import Salesforce
from tap_salesforce.salesforce.credentials import parse_credentials
from tap_salesforce.salesforce.exceptions import (
    TapSalesforceExceptionError,
    TapSalesforceQuotaExceededError,
)
from tap_salesforce.sync_orchestrator import SyncOrchestrator, build_state

LOGGER = singer.get_logger()

CONFIG = {**DEFAULT_CONFIG}


def main_impl():
    args = singer_utils.parse_args(REQUIRED_CONFIG_KEYS)
    CONFIG.update(args.config)

    credentials = parse_credentials(CONFIG)
    sf = None
    try:
        lookback_window = CONFIG.get("lookback_window")
        lookback_window = int(lookback_window) if lookback_window else None
        api_version = CONFIG.get("api_version")
        api_version = api_version if api_version else "v60.0"
        sf = Salesforce(
            credentials=credentials,
            quota_percent_total=CONFIG.get("quota_percent_total"),
            quota_percent_per_run=CONFIG.get("quota_percent_per_run"),
            select_fields_by_default=CONFIG.get("select_fields_by_default"),
            default_start_date=CONFIG.get("start_date"),
            api_type=CONFIG.get("api_type"),
            lookback_window=lookback_window,
            api_version=api_version,
            ignore_formula_fields=CONFIG.get("ignore_formula_fields", False),
            soql_filters=CONFIG.get("soql_filters") or {},
            redirect_uri=CONFIG.get("redirect_uri"),
        )
        sf.login()

        if args.discover:
            discovery = DiscoveryService(sf)
            discovery.discover(CONFIG.get("streams_to_discover", []))
        elif args.properties or args.catalog:
            catalog = args.properties or args.catalog.to_dict()
            state = build_state(args.state, catalog)
            orchestrator = SyncOrchestrator(sf, CONFIG)
            orchestrator.sync(catalog, state)
    finally:
        if sf:
            if sf.rest_requests_attempted > 0:
                LOGGER.debug(
                    "This job used %s REST requests towards the Salesforce quota.",
                    sf.rest_requests_attempted,
                )
            if sf.jobs_completed > 0:
                LOGGER.debug(
                    "Replication used %s Bulk API jobs towards the Salesforce quota.",
                    sf.jobs_completed,
                )
            if sf.auth.login_timer:
                sf.auth.login_timer.cancel()


def main():
    try:
        main_impl()
    except TapSalesforceQuotaExceededError as e:
        LOGGER.critical(e)
        sys.exit(2)
    except TapSalesforceExceptionError as e:
        LOGGER.critical(e)
        sys.exit(1)
    except Exception as e:
        LOGGER.critical(e)
        raise e
