# RagSearchRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**query** | **str** |  | 
**collection** | **str** |  | [optional] [default to 'scam_intel']
**top_k** | **int** |  | [optional] [default to 5]

## Example

```python
from rugmunch.models.rag_search_request import RagSearchRequest

# TODO update the JSON string below
json = "{}"
# create an instance of RagSearchRequest from a JSON string
rag_search_request_instance = RagSearchRequest.from_json(json)
# print the JSON string representation of the object
print(RagSearchRequest.to_json())

# convert the object into a dict
rag_search_request_dict = rag_search_request_instance.to_dict()
# create an instance of RagSearchRequest from a dict
rag_search_request_from_dict = RagSearchRequest.from_dict(rag_search_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


