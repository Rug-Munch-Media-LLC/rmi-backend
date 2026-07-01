# PaginatedResponse

Standard paginated list response.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**items** | **List[object]** |  | [optional] 
**total** | **int** |  | [optional] [default to 0]
**page** | **int** |  | [optional] [default to 1]
**limit** | **int** |  | [optional] [default to 20]
**has_more** | **bool** |  | [optional] [default to False]

## Example

```python
from rugmunch.models.paginated_response import PaginatedResponse

# TODO update the JSON string below
json = "{}"
# create an instance of PaginatedResponse from a JSON string
paginated_response_instance = PaginatedResponse.from_json(json)
# print the JSON string representation of the object
print(PaginatedResponse.to_json())

# convert the object into a dict
paginated_response_dict = paginated_response_instance.to_dict()
# create an instance of PaginatedResponse from a dict
paginated_response_from_dict = PaginatedResponse.from_dict(paginated_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


