# SearchHit

A single search result.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**content** | **str** |  | 
**score** | **float** |  | [optional] [default to 0.0]
**metadata** | **Dict[str, object]** |  | [optional] 
**collection** | **str** |  | [optional] [default to '']
**doc_id** | **str** |  | [optional] [default to '']

## Example

```python
from rugmunch.models.search_hit import SearchHit

# TODO update the JSON string below
json = "{}"
# create an instance of SearchHit from a JSON string
search_hit_instance = SearchHit.from_json(json)
# print the JSON string representation of the object
print(SearchHit.to_json())

# convert the object into a dict
search_hit_dict = search_hit_instance.to_dict()
# create an instance of SearchHit from a dict
search_hit_from_dict = SearchHit.from_dict(search_hit_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


