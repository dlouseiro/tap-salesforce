"""Tests for tap_salesforce.salesforce.schema."""

import pytest
from singer import metadata

from tap_salesforce.salesforce.exceptions import TapSalesforceExceptionError
from tap_salesforce.salesforce.schema import (
    DATE_TYPES,
    LOOSE_TYPES,
    NUMBER_TYPES,
    QUERY_INCOMPATIBLE_SALESFORCE_OBJECTS,
    QUERY_RESTRICTED_SALESFORCE_OBJECTS,
    STRING_TYPES,
    UNSUPPORTED_BULK_API_SALESFORCE_OBJECTS,
    field_to_property_schema,
)


class TestFieldToPropertySchema:
    def _make_field(self, name="TestField", sf_type="string", **kwargs):
        return {"name": name, "type": sf_type, **kwargs}

    def _fresh_metadata(self):
        return metadata.new()

    def test_string_types(self):
        for sf_type in STRING_TYPES:
            field = self._make_field(sf_type=sf_type)
            schema, _ = field_to_property_schema(field, self._fresh_metadata())
            assert schema["type"] == ["null", "string"]

    def test_number_types(self):
        for sf_type in NUMBER_TYPES:
            field = self._make_field(sf_type=sf_type)
            schema, _ = field_to_property_schema(field, self._fresh_metadata())
            assert schema["type"] == ["null", "number"]

    def test_date_types_use_anyof(self):
        for sf_type in DATE_TYPES:
            field = self._make_field(sf_type=sf_type)
            schema, _ = field_to_property_schema(field, self._fresh_metadata())
            assert "anyOf" in schema
            assert schema["anyOf"][0] == {"type": "string", "format": "date-time"}

    def test_boolean_type(self):
        field = self._make_field(sf_type="boolean")
        schema, _ = field_to_property_schema(field, self._fresh_metadata())
        assert schema["type"] == ["null", "boolean"]

    def test_integer_types(self):
        for sf_type in ("int", "long"):
            field = self._make_field(sf_type=sf_type)
            schema, _ = field_to_property_schema(field, self._fresh_metadata())
            assert schema["type"] == ["null", "integer"]

    def test_address_type(self):
        field = self._make_field(sf_type="address")
        schema, _ = field_to_property_schema(field, self._fresh_metadata())
        assert schema["type"] == ["null", "object"]
        assert "street" in schema["properties"]
        assert "latitude" in schema["properties"]

    def test_location_type(self):
        field = self._make_field(sf_type="location")
        schema, _ = field_to_property_schema(field, self._fresh_metadata())
        assert schema["type"] == ["number", "object", "null"]
        assert "longitude" in schema["properties"]

    def test_binary_type_marks_unsupported(self):
        field = self._make_field(sf_type="byte")
        mdata = self._fresh_metadata()
        schema, mdata = field_to_property_schema(field, mdata)
        assert schema == {}
        mdata_map = metadata.to_map(metadata.to_list(mdata))
        assert mdata_map[("properties", "TestField")]["inclusion"] == "unsupported"

    def test_json_type(self):
        field = self._make_field(sf_type="json")
        schema, _ = field_to_property_schema(field, self._fresh_metadata())
        assert schema["type"] == ["null", "string"]

    def test_time_type(self):
        field = self._make_field(sf_type="time")
        schema, _ = field_to_property_schema(field, self._fresh_metadata())
        assert schema["type"] == ["null", "string"]

    def test_loose_types(self):
        for sf_type in LOOSE_TYPES:
            field = self._make_field(sf_type=sf_type)
            schema, _ = field_to_property_schema(field, self._fresh_metadata())
            assert schema["type"] == ["null", "string"]

    def test_unsupported_type_raises(self):
        field = self._make_field(sf_type="completely_unknown_type")
        with pytest.raises(TapSalesforceExceptionError, match="unsupported type"):
            field_to_property_schema(field, self._fresh_metadata())

    def test_id_field_not_nullable(self):
        field = self._make_field(name="Id", sf_type="id")
        schema, _ = field_to_property_schema(field, self._fresh_metadata())
        assert schema["type"] == "string"  # not wrapped in ["null", ...]

    def test_formula_field_excluded_when_ignore_enabled(self):
        field = self._make_field(sf_type="string", calculated=True)
        mdata = self._fresh_metadata()
        schema, mdata = field_to_property_schema(field, mdata, ignore_formula_fields=True)
        assert schema == {}
        mdata_map = metadata.to_map(metadata.to_list(mdata))
        assert mdata_map[("properties", "TestField")]["inclusion"] == "unsupported"

    def test_formula_field_not_excluded_when_ignore_disabled(self):
        field = self._make_field(sf_type="string", calculated=True)
        schema, _ = field_to_property_schema(field, self._fresh_metadata(), ignore_formula_fields=False)
        assert schema["type"] == ["null", "string"]


class TestObjectSets:
    def test_unsupported_bulk_objects_is_non_empty(self):
        assert len(UNSUPPORTED_BULK_API_SALESFORCE_OBJECTS) > 0

    def test_query_restricted_objects_is_non_empty(self):
        assert len(QUERY_RESTRICTED_SALESFORCE_OBJECTS) > 0

    def test_query_incompatible_objects_is_non_empty(self):
        assert len(QUERY_INCOMPATIBLE_SALESFORCE_OBJECTS) > 0

    def test_no_overlap_between_unsupported_and_restricted(self):
        overlap = UNSUPPORTED_BULK_API_SALESFORCE_OBJECTS & QUERY_RESTRICTED_SALESFORCE_OBJECTS
        assert overlap == set()
