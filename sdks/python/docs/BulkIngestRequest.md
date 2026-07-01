# BulkIngestRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**collection** | **str** |  | [optional] [default to 'scam_intel']
**items** | **List[Dict[str, object]]** |  | [optional] 

## Example

```python
from rugmunch.models.bulk_ingest_request import BulkIngestRequest

# TODO update the JSON string below
json = "{}"
# create an instance of BulkIngestRequest from a JSON string
bulk_ingest_request_instance = BulkIngestRequest.from_json(json)
# print the JSON string representation of the object
print(BulkIngestRequest.to_json())

# convert the object into a dict
bulk_ingest_request_dict = bulk_ingest_request_instance.to_dict()
# create an instance of BulkIngestRequest from a dict
bulk_ingest_request_from_dict = BulkIngestRequest.from_dict(bulk_ingest_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


