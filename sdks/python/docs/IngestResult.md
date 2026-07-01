# IngestResult

RAG document ingestion result.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**doc_id** | **str** |  | 
**collection** | **str** |  | 
**status** | **str** |  | [optional] [default to 'ok']
**chunks** | **int** |  | [optional] [default to 0]
**error** | **str** |  | [optional] 
**ingested_at** | **datetime** |  | [optional] 

## Example

```python
from rugmunch.models.ingest_result import IngestResult

# TODO update the JSON string below
json = "{}"
# create an instance of IngestResult from a JSON string
ingest_result_instance = IngestResult.from_json(json)
# print the JSON string representation of the object
print(IngestResult.to_json())

# convert the object into a dict
ingest_result_dict = ingest_result_instance.to_dict()
# create an instance of IngestResult from a dict
ingest_result_from_dict = IngestResult.from_dict(ingest_result_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


