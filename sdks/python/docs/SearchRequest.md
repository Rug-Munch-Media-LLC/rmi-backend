# SearchRequest

RAG search request.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**query** | **str** |  | 
**collection** | **str** |  | [optional] [default to 'scam_intel']
**top_k** | **int** |  | [optional] [default to 5]
**min_similarity** | **float** |  | [optional] [default to 0.0]
**filters** | **Dict[str, object]** |  | [optional] 

## Example

```python
from rugmunch.models.search_request import SearchRequest

# TODO update the JSON string below
json = "{}"
# create an instance of SearchRequest from a JSON string
search_request_instance = SearchRequest.from_json(json)
# print the JSON string representation of the object
print(SearchRequest.to_json())

# convert the object into a dict
search_request_dict = search_request_instance.to_dict()
# create an instance of SearchRequest from a dict
search_request_from_dict = SearchRequest.from_dict(search_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


