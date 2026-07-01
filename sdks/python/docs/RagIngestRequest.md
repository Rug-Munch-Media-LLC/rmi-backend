# RagIngestRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**content** | **str** |  | 
**collection** | **str** |  | [optional] [default to 'scam_intel']
**doc_id** | **str** |  | [optional] 
**metadata** | **Dict[str, object]** |  | [optional] 

## Example

```python
from rugmunch.models.rag_ingest_request import RagIngestRequest

# TODO update the JSON string below
json = "{}"
# create an instance of RagIngestRequest from a JSON string
rag_ingest_request_instance = RagIngestRequest.from_json(json)
# print the JSON string representation of the object
print(RagIngestRequest.to_json())

# convert the object into a dict
rag_ingest_request_dict = rag_ingest_request_instance.to_dict()
# create an instance of RagIngestRequest from a dict
rag_ingest_request_from_dict = RagIngestRequest.from_dict(rag_ingest_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


