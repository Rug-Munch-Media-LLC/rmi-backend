# SearchResponse

RAG search response.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**query** | **str** |  | 
**hits** | [**List[SearchHit]**](SearchHit.md) |  | [optional] 
**total** | **int** |  | [optional] [default to 0]
**took_ms** | **int** |  | [optional] [default to 0]
**collection** | **str** |  | [optional] [default to '']

## Example

```python
from rugmunch.models.search_response import SearchResponse

# TODO update the JSON string below
json = "{}"
# create an instance of SearchResponse from a JSON string
search_response_instance = SearchResponse.from_json(json)
# print the JSON string representation of the object
print(SearchResponse.to_json())

# convert the object into a dict
search_response_dict = search_response_instance.to_dict()
# create an instance of SearchResponse from a dict
search_response_from_dict = SearchResponse.from_dict(search_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


