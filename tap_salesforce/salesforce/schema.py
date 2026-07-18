"""Salesforce field type to JSON Schema mapping."""

from __future__ import annotations

from singer import metadata

from tap_salesforce.salesforce.exceptions import TapSalesforceExceptionError

STRING_TYPES = {
    "id",
    "string",
    "picklist",
    "textarea",
    "phone",
    "url",
    "reference",
    "multipicklist",
    "combobox",
    "encryptedstring",
    "email",
    "complexvalue",
    "masterrecord",
    "datacategorygroupreference",
    "base64",
}

NUMBER_TYPES = {"double", "currency", "percent"}

DATE_TYPES = {"datetime", "date"}

BINARY_TYPES = {"byte"}

LOOSE_TYPES = {
    "anyType",
    "calculated",
}

# Objects not supported by the Bulk API
UNSUPPORTED_BULK_API_SALESFORCE_OBJECTS = {
    "AssetTokenEvent",
    "AttachedContentNote",
    "EventWhoRelation",
    "QuoteTemplateRichTextData",
    "TaskWhoRelation",
    "SolutionStatus",
    "ContractStatus",
    "RecentlyViewed",
    "DeclinedEventRelation",
    "AcceptedEventRelation",
    "TaskStatus",
    "PartnerRole",
    "TaskPriority",
    "CaseStatus",
    "UndecidedEventRelation",
    "OrderStatus",
}

# Objects with WHERE clause restrictions
QUERY_RESTRICTED_SALESFORCE_OBJECTS = {
    "Announcement",
    "CollaborationGroupRecord",
    "Vote",
    "IdeaComment",
    "FieldDefinition",
    "PlatformAction",
    "UserEntityAccess",
    "RelationshipInfo",
    "ContentFolderMember",
    "ContentFolderItem",
    "SearchLayout",
    "SiteDetail",
    "EntityParticle",
    "OwnerChangeOptionInfo",
    "DataStatistics",
    "UserFieldAccess",
    "PicklistValueInfo",
    "RelationshipDomain",
    "FlexQueueItem",
    "NetworkUserHistoryRecent",
    "FieldHistoryArchive",
    "RecordActionHistory",
    "FlowVersionView",
    "FlowVariableView",
    "AppTabMember",
    "ColorDefinition",
    "IconDefinition",
}

# Objects not supported by the query method
QUERY_INCOMPATIBLE_SALESFORCE_OBJECTS = {
    "DataType",
    "ListViewChartInstance",
    "FeedLike",
    "OutgoingEmail",
    "OutgoingEmailRelation",
    "FeedSignal",
    "ActivityHistory",
    "EmailStatus",
    "UserRecordAccess",
    "Name",
    "AggregateResult",
    "OpenActivity",
    "ProcessInstanceHistory",
    "OwnedContentDocument",
    "FolderedContentDocument",
    "FeedTrackedChange",
    "CombinedAttachment",
    "AttachedContentDocument",
    "ContentBody",
    "NoteAndAttachment",
    "LookedUpFromActivity",
    "AttachedContentNote",
    "QuoteTemplateRichTextData",
}


def field_to_property_schema(field: dict, mdata, ignore_formula_fields: bool = False) -> tuple[dict, object]:  # noqa: C901
    """Convert a Salesforce field descriptor to a JSON Schema property.

    Returns:
        Tuple of (property_schema dict, updated metadata).
    """
    property_schema = {}
    field_name = field["name"]

    if ignore_formula_fields and field.get("calculated"):
        mdata = metadata.write(mdata, ("properties", field_name), "selected-by-default", False)
        mdata = metadata.write(mdata, ("properties", field_name), "selected", False)
        mdata = metadata.write(mdata, ("properties", field_name), "inclusion", "unsupported")
        mdata = metadata.write(
            mdata, ("properties", field_name), "unsupported-description", "formula field excluded by configuration"
        )
        return property_schema, mdata

    sf_type = field["type"]

    if sf_type in STRING_TYPES:
        property_schema["type"] = "string"
    elif sf_type in DATE_TYPES:
        date_type = {"type": "string", "format": "date-time"}
        string_type = {"type": ["string", "null"]}
        property_schema["anyOf"] = [date_type, string_type]
    elif sf_type == "boolean":
        property_schema["type"] = "boolean"
    elif sf_type in NUMBER_TYPES:
        property_schema["type"] = "number"
    elif sf_type == "address":
        property_schema["type"] = "object"
        property_schema["properties"] = {
            "street": {"type": ["null", "string"]},
            "state": {"type": ["null", "string"]},
            "postalCode": {"type": ["null", "string"]},
            "city": {"type": ["null", "string"]},
            "country": {"type": ["null", "string"]},
            "longitude": {"type": ["null", "number"]},
            "latitude": {"type": ["null", "number"]},
            "geocodeAccuracy": {"type": ["null", "string"]},
        }
    elif sf_type in ("int", "long"):
        property_schema["type"] = "integer"
    elif sf_type == "time" or sf_type in LOOSE_TYPES:
        property_schema["type"] = "string"
    elif sf_type in BINARY_TYPES:
        mdata = metadata.write(mdata, ("properties", field_name), "inclusion", "unsupported")
        mdata = metadata.write(mdata, ("properties", field_name), "unsupported-description", "binary data")
        return property_schema, mdata
    elif sf_type == "location":
        property_schema["type"] = ["number", "object", "null"]
        property_schema["properties"] = {
            "longitude": {"type": ["null", "number"]},
            "latitude": {"type": ["null", "number"]},
        }
    elif sf_type == "json":
        property_schema["type"] = "string"
    else:
        raise TapSalesforceExceptionError(f"Found unsupported type: {sf_type}")

    # The nillable field cannot be trusted
    if field_name != "Id" and sf_type != "location" and sf_type not in DATE_TYPES:
        property_schema["type"] = ["null", property_schema["type"]]

    return property_schema, mdata
